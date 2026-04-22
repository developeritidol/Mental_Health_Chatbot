from datetime import datetime, timedelta
from jose import JWTError, jwt, ExpiredSignatureError
from app.api.schemas.response import TokenData
from app.core.config import get_settings
from app.core.auth.token_blacklist import is_token_blacklisted
from fastapi import HTTPException
from app.core.logger import get_logger

logger = get_logger(__name__)


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
    if is_token_blacklisted(token):
        logger.warning("event=refresh_token_rejected reason=blacklisted")
        raise HTTPException(status_code=401, detail="Token is blacklisted")
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
<<<<<<< HEAD
        sub: str = payload.get("sub")
=======
        useremail: str = payload.get("sub")
        user_id: str = payload.get("user_id")
>>>>>>> 1317e4c411bdef0b6b5dba31035af40b6db0bd5b
        token_type: str = payload.get("type")
        role: str = payload.get("role")
        if sub is None or token_type != "refresh":
            logger.warning("event=refresh_token_rejected reason=invalid_claims")
            raise credentials_exception
<<<<<<< HEAD
        token_data = TokenData(sub=sub, role=role)
=======

        token_data = TokenData(useremail=useremail, user_id=user_id)
>>>>>>> 1317e4c411bdef0b6b5dba31035af40b6db0bd5b
        return token_data
    except ExpiredSignatureError:
        logger.warning("event=refresh_token_rejected reason=expired")
        # Return 403 for expired refresh token so frontend knows to logout
        raise HTTPException(
            status_code=403,
            detail="Refresh token expired! Please login again.",
        )
    except JWTError:
        logger.warning("event=refresh_token_rejected reason=decode_error")
        raise credentials_exception


def verify_token(token: str, credentials_exception):
    settings = get_settings()
    # Check if token is blacklisted
    if is_token_blacklisted(token):
        logger.warning("event=access_token_rejected reason=blacklisted")
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked. Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
<<<<<<< HEAD
        sub: str = payload.get("sub")
=======
        useremail: str = payload.get("sub")
        user_id: str = payload.get("user_id")
>>>>>>> 1317e4c411bdef0b6b5dba31035af40b6db0bd5b
        token_type: str = payload.get("type")
        role: str = payload.get("role")
        if sub is None or token_type != "access":
            logger.warning("event=access_token_rejected reason=invalid_claims")
            raise credentials_exception
<<<<<<< HEAD
        token_data = TokenData(sub=sub, role=role)
=======

        token_data = TokenData(useremail=useremail, user_id=user_id)
>>>>>>> 1317e4c411bdef0b6b5dba31035af40b6db0bd5b
        return token_data
    except ExpiredSignatureError:
        logger.warning("event=access_token_rejected reason=expired")
        # Handle expired token specifically
        raise HTTPException(
            status_code=401,
            detail="Token expired! Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        logger.warning("event=access_token_rejected reason=decode_error")
        raise credentials_exception