"""JWT authentication with three mock demo roles.

Passwords are bcrypt-hashed at import (never compared in plain text) and tokens are
HS256 JWTs. The signing secret comes from the JWT_SECRET env var, else a random
secret persisted in data/.jwt_secret so sessions survive API restarts.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from .config import DATA_DIR

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=8)

ROLES = {
    "admin": {"title": "Validation Lead", "permissions": ["read", "upload", "generate"]},
    "engineer": {"title": "VLSI Engineer", "permissions": ["read", "upload", "generate"]},
    "executive": {"title": "Executive Viewer", "permissions": ["read"]},
}

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Mock identity store for the demo (password hashes only).
_USERS = {
    "admin@sandisk.com": {"name": "Avery Admin", "role": "admin", "hash": _pwd.hash("admin123")},
    "engineer@sandisk.com": {"name": "Riley Engineer", "role": "engineer", "hash": _pwd.hash("eng123")},
    "executive@sandisk.com": {"name": "Jordan Executive", "role": "executive", "hash": _pwd.hash("exec123")},
}


def _secret() -> str:
    env = os.getenv("JWT_SECRET", "").strip()
    if env:
        return env
    path = DATA_DIR / ".jwt_secret"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
    return path.read_text().strip()


SECRET = _secret()


def public_user(email: str) -> dict:
    u = _USERS[email]
    role = ROLES[u["role"]]
    return {"email": email, "name": u["name"], "role": u["role"], "role_title": role["title"], "permissions": role["permissions"]}


def authenticate(email: str, password: str) -> dict | None:
    u = _USERS.get(email.strip().lower())
    if not u or not _pwd.verify(password, u["hash"]):
        return None
    return public_user(email.strip().lower())


def create_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": email, "iat": now, "exp": now + TOKEN_TTL}, SECRET, algorithm=ALGORITHM)


_bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        email = jwt.decode(creds.credentials, SECRET, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        raise unauthorized
    if email not in _USERS:
        raise unauthorized
    return public_user(email)


def require_permission(permission: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if permission not in user["permissions"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{user['role_title']}' cannot perform '{permission}'")
        return user
    return checker


class LoginReq(BaseModel):
    email: str
    password: str


router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login")
def login(req: LoginReq):
    user = authenticate(req.email, req.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return {"access_token": create_token(user["email"]), "token_type": "bearer", "expires_in": int(TOKEN_TTL.total_seconds()), "user": user}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user
