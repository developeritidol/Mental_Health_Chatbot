from fastapi import Depends, HTTPException, status, Header, Security
from fastapi.security import HTTPBearer

from app.core.auth.JWTtoken import verify_token
from app.core.auth.token_blacklist import is_blacklisted

def get_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")

    return authorization.split(" ")[1]

security = HTTPBearer()

def get_current_user(credentials = Security(security)):
    token = credentials.credentials

    print("TOKEN RECEIVED:", token)

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    #  REMOVE manual blacklist check from here

    #  Single source of truth
    token_data = verify_token(token, credentials_exception)

    return token_data

def verify_token(token: str, credentials_exception):

    #  ONLY place where blacklist is checked
    if is_blacklisted(token):
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked",
        )