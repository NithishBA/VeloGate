
from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import SessionLocal, engine, Base
from app.models import Note, User
from sqlalchemy.future import select 
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app.schemas import NoteCreate, NoteRead, UserCreate, UserRead , LoginRequest
from app.crud import create_note,  create_user, get_users
from app.auth import get_current_user, create_access_token, hash_password, verify_password
from app.limiter import rate_limit_middleware
from app.utils import create_response , create_error_response
import asyncio
from redis import asyncio as redis
from app.database import get_db
import json
from datetime import datetime
from typing import Optional 
app = FastAPI()
app.middleware("http")(rate_limit_middleware)
from redis .asyncio.lock import Lock

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


#-------------------------------------   Redis Pub/Sub setup  ----------------------------------------
# Redis Pub/Sub setup
redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()



#-------------------------------------   Redis caching setup  ----------------------------------------
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

async def get_cache(key: str):
    data = await redis_client.get(key)
    return json.loads(data) if data else None

# Set cache with TTL (time-to-live in seconds)
async def set_cache(key: str, value, ttl: int = 300):
    await redis_client.set(key, json.dumps(value, cls=DateTimeEncoder), ex=ttl)

# Delete a cache key
async def delete_cache(key: str):
    await redis_client.delete(key)


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


#---------------------------------------------------- User registration endpoint   ---------------------------------------

@app.post("/users")
async def create_user(
    request: Request,
    user: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).filter(
            (User.username == user.username) | (User.email == user.email)
        )
    )
    existing_user = result.scalars().first()
    if existing_user:
        return create_error_response("Username or Email already exists", 400)

    try:
        # Prepare data and hash password
        user_data = user.dict()
        user_data["password"] = hash_password(user_data["password"])

        # Create user instance
        user_obj = User(**user_data)
        db.add(user_obj)
        await db.commit()
        await db.refresh(user_obj)

        return create_response(True, UserRead.from_orm(user_obj), "User created successfully", 201)

    except Exception as e:
        await db.rollback()
        return create_error_response(f"Failed to create user: {e}", 500)
    

@app.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    try:
        users = await get_users(db)  # This should return a list of User objects
        return create_response(
            status=True,
            data=[UserRead.model_validate(user).model_dump() for user in users],
            message="Users retrieved successfully",
            code=200
        )
    except Exception as e:
        return create_error_response(f"Failed to fetch users: {e}", 500)

#----------------------------------------------------- User login endpoint    ---------------------------------------

@app.post("/login")
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(
            select(User).where(User.username == data.username)
        )
        user = result.scalar_one_or_none()

        if not user or not verify_password(data.password, user.password):
            return create_error_response("Invalid credentials", 401)

        token = create_access_token({"sub": str(user.id)})

        return create_response(
            status=True,
            data={
                "access_token": token,
                "token_type": "bearer"
            },
            message="Login successful",
            code=200
        )

    except Exception as e:
        return create_error_response(f"Login failed: {e}", 500)

#---------------------------------------------------- Notes endpoint  ---------------------------------------

@app.post("/notes")
async def add_note(
    data: NoteCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user)
):
    try:
        note = await create_note(db, data, owner_id=user.id)

        # 🔹 Fetch updated list from DB
        result = await db.execute(select(Note).where(Note.owner_id == user.id))
        notes = result.scalars().all()
        notes_data = [NoteRead.from_orm(n).dict() for n in notes]

        # 🔹 Update Redis cache immediately
        await set_cache(f"notes_list:{user.id}", notes_data, ttl=300)

        # Publish event
        await redis_client.publish(
            "notes",
            f"Note added by {user.username}: {note.title}"
        )

        return create_response(
            True,
            NoteRead.from_orm(note),
            "Note created successfully",
            201
        )

    except Exception as e:
        await db.rollback()
        return create_error_response(f"Failed to create note: {e}", 500)


@app.patch("/notes/{note_id}")
async def update_note(
    note_id: int,
    data: NoteCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user)
):
    try:
        result = await db.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()

        if not note:
            return create_error_response(code=404, message="Note not found")

        if note.owner_id != user.id:
            return create_error_response(code=403, message="Not authorized to update this note")

        lock_key = f"note_lock:{note_id}"
        lock = Lock(redis_client, lock_key, timeout=10)
        async with lock:
        # Update note
            note.title = data.title
            note.content = data.content

            await db.commit()
            await db.refresh(note)

            # Invalidate cache for notes list
            await redis_client.delete("notes_list")

            # Update single note cache (if you store individual notes separately)
            note_data = NoteRead.from_orm(note).dict()
            note_data["created_at"] = note_data["created_at"].isoformat() if note_data["created_at"] else None
            note_data["updated_at"] = note_data["updated_at"].isoformat() if note_data["updated_at"] else None

            await redis_client.set(f"note:{note_id}", json.dumps(note_data))

            # Publish update event
            await redis_client.publish(
                "notes",
                f"Note updated by {user.username}: {note.title}"
            )

            return create_response(
                True,
                note_data,
                "Note updated successfully",
                200
            )

    except Exception as e:
        await db.rollback()
        return create_error_response(code=500, message=f"Failed to update note: {e}")


@app.get("/notes")
async def get_all_notes(
    page: Optional[int] = Query(1, ge=1, description="Page number"),
    limit: Optional[int] = Query(20, ge=1, le=100, description="Number of items per page"),
    note_id: Optional[int] = Query(None, description="Note ID to filter"),
    user_id: Optional[int] = Query(..., description="User ID to filter notes"),
    db: AsyncSession = Depends(get_db),
):
    try:
        # Build Redis cache key with filters
        cache_key = f"notes_list:{user_id}:{note_id if note_id else 'all'}:{page}:{limit}"
        cached_data = await redis_client.get(cache_key)
        if cached_data:
            logger.info(f"==========Data fetched from Redis cache for key: {cache_key}")
            return json.loads(cached_data)

        # Base query
        query = select(Note).where(Note.owner_id == user_id)

        # Apply note_id filter if provided
        if note_id:
            query = query.where(Note.id == note_id)

        # Count total notes for pagination
        total_count = await db.scalar(
            select(func.count()).select_from(Note).where(Note.owner_id == user_id if not note_id else Note.id == note_id)
        )
        total_pages = (total_count + limit - 1) // limit

        # Handle out-of-range page numbers
        page = min(page, total_pages) if total_pages > 0 else 1
        offset = (page - 1) * limit

        # Apply ordering and pagination
        query = query.order_by(Note.created_at.desc()).offset(offset).limit(limit)
        result = await db.execute(query)
        notes = result.scalars().all()

        # Format notes
        notes_data = [
            {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "owner_id": note.owner_id,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            }
            for note in notes
        ]

        pagination = {
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
        }

        response = {
            "status": True,
            "code": 200,
            "message": "Notes fetched successfully",
            "data": {
                "notes": notes_data,
                "pagination": pagination
            },
            "errors": None
        }

        # Store result in Redis for 5 minutes
        await redis_client.setex(cache_key, 300, json.dumps(response))

        return response

    except Exception as e:
        return {
            "status": False,
            "code": 500,
            "message": f"Failed to retrieve notes: {str(e)}",
            "data": None,
            "errors": str(e)
        }
@app.delete("/notes/{note_id}")
async def delete_note(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user)
):
    try:
        result = await db.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()

        if not note:
            return create_error_response(code=404, message="Note not found")

        if note.owner_id != user.id:
            return create_error_response(code=403, message="Not authorized to delete this note")

        # Delete from DB
        await db.delete(note)
        await db.commit()

        # Invalidate all cached notes list for this user
        pattern = f"notes_list:{user.id}:*"
        async for key in redis_client.scan_iter(match=pattern):
            await redis_client.delete(key)

        # Delete individual note cache if it exists
        await redis_client.delete(f"note:{note_id}")

        # Publish event
        await redis_client.publish(
            "notes",
            f"Note deleted by {user.username}: {note.title}"
        )

        return create_response(True, None, "Note deleted successfully", 200)

    except Exception as e:
        await db.rollback()
        return create_error_response(code=500, message=f"Failed to delete note: {e}")


# ---------------------------------------- WebSocket endpoint for real-time notifications
@app.websocket("/ws/notifications")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("notes")
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                await manager.broadcast(message["data"])
            await asyncio.sleep(0.1)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)
    finally:
        await pubsub.unsubscribe("notes")



@app.get("/health")
def health():
    return {"status": "ok"}
