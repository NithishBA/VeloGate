
from pydantic import BaseModel 
from typing import Optional , List , Any
from datetime import datetime


class Response(BaseModel):
    status: bool
    data: Optional[Any]
    errors: Optional[List[Any]] = None 
    message: str
    code: int

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserRead(UserCreate):
    id: int

    class Config:
        from_attributes = True

class LoginRequest(BaseModel):
    username: str
    password: str


class NoteCreate(BaseModel):
    title: str
    content: str

class NoteRead(NoteCreate):
    id: int
    title: str
    content: str
    owner_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
