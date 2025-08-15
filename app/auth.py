from jose import jwt, JWTError
from fastapi import Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User
from sqlalchemy.future import select
from passlib.context import CryptContext
from app.utils import create_error_response
from  app.database import get_db
from datetime import datetime, timedelta, timezone

JWT_SECRET = "super-secret"
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# def create_access_token(data: dict):
#     return jwt.encode(data, JWT_SECRET, algorithm=ALGORITHM)

def create_access_token(data: dict, expires_in: int = 3600) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)


async def get_current_user(
    request: Request, 
    db: AsyncSession = Depends(get_db)
):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise create_error_response(code=401, message="Missing or invalid token")

    token = auth_header.split(" ")[1]

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")

        if not user_id:
            raise create_error_response(code=401, message="Invalid or token payload")

        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()

        if not user:
            raise create_error_response(code=401, message="User not found")

        return user

    except jwt.ExpiredSignatureError:
        raise create_error_response(code=401, message="Token expired")
    except JWTError:
        raise create_error_response(code=401, message="Invalid token")


def verify_jwt_token(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")