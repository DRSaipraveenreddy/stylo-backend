"""
Pydantic models for request/response validation
"""
from pydantic import BaseModel
from typing import Optional


class UserSignup(BaseModel):
    email: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    created_at: str


class ItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    user_id: str


class ItemResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    user_id: str
    created_at: str
