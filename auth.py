"""
Auth for the internal logistics interface. Cookie-based session using a
signed JWT — no external identity provider, no signup flow anywhere.
Accounts only ever created via create_user.py, run directly on VM101.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request, HTTPException, Depends
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

import config
from database import get_db
from db_models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
SESSION_COOKIE_NAME = "session"
SESSION_EXPIRE_HOURS = 24


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def create_session_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=SESSION_EXPIRE_HOURS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, config.SESSION_SECRET_KEY, algorithm=ALGORITHM)


def decode_session_token(token: str) -> Optional[str]:
    """Returns the user_id if valid, None if expired/invalid/tampered."""
    try:
        payload = jwt.decode(token, config.SESSION_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    FastAPI dependency for protected routes. Reads the session cookie,
    validates it, loads the User. Raises 401 if anything's wrong — no
    session, expired session, tampered token, or a since-deactivated user.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user_id = decode_session_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.query(User).filter_by(id=user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Account not found or disabled")

    return user
