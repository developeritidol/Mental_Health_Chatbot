from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer

from app.core.auth.JWTtoken import verify_token
from app.api.schemas.response import TokenData


security = HTTPBearer()

def get_current_token(credentials = Security(security)):
    return credentials.credentials

def get_current_user(credentials = Security(security)):
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Single source of truth - uses the imported verify_token from JWTtoken
    token_data = verify_token(token, credentials_exception)
    return token_data

def get_current_admin(token_data: TokenData = Depends(get_current_user)):
    if token_data.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user does not have enough privileges"
        )
    return token_data