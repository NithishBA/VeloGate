from app.models import Note, User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select


async def create_note(db: AsyncSession, data, owner_id: int):
    note = Note(**data.dict(), owner_id=owner_id)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note

async def create_user(db: AsyncSession, username: str, email: str , hashed_password: str):
    user = User(username=username, email=email, password=hashed_password)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_notes(db: AsyncSession, owner_id: int):
    result = await db.execute(select(Note).where(Note.owner_id == owner_id))
    return result.scalars().all()

async def get_users(db: AsyncSession):
    result = await db.execute(select(User))
    return result.scalars().all()
