import os
from datetime import datetime, timedelta

from jose import JWTError, jwt, ExpiredSignatureError

from app.api.schemas.response import TokenData
from app.core.config import get_settings
from fastapi import HTTPException


def create_access_token(data: dict):
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return encoded_jwt


def create_refresh_token(data: dict):
    """Create a refresh token with longer expiry"""
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return encoded_jwt


def verify_refresh_token(token: str, credentials_exception):
    """Verify refresh token specifically"""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        useremail: str = payload.get("sub")
        token_type: str = payload.get("type")

        if useremail is None or token_type != "refresh":
            raise credentials_exception

        token_data = TokenData(useremail=useremail)
        return token_data
    except ExpiredSignatureError:
        # Return 403 for expired refresh token so frontend knows to logout
        raise HTTPException(
            status_code=403,
            detail="Refresh token expired! Please login again.",
        )
    except JWTError:
        raise credentials_exception


def verify_token(token: str, credentials_exception):
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        useremail: str = payload.get("sub")
        token_type: str = payload.get("type")

        if useremail is None or token_type != "access":
            raise credentials_exception

        token_data = TokenData(useremail=useremail)
        return token_data
    except ExpiredSignatureError:
        # Handle expired token specifically
        raise HTTPException(
            status_code=401,
            detail="Token expired! Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise credentials_exception