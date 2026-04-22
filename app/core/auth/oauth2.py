from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer

from app.core.auth.JWTtoken import verify_token

security = HTTPBearer()


async def get_current_user(credentials=Security(security)):
    token = credentials.credentials

    # Fix Swagger double "Bearer Bearer"
    if token.startswith("Bearer "):
        token = token.split(" ")[1]

    print("CLEAN TOKEN RECEIVED:", token)

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Decode + validate token
    token_data = verify_token(token, credentials_exception)

    # ✅ FIXED: use `sub` instead of `useremail`
    user_doc = {
        "_id": token_data.user_id,
        "user_id": token_data.user_id,
        "email": token_data.sub,
        "useremail": token_data.sub,  # optional (keep if your code expects it)
        "role": token_data.role,
        "token": token  # IMPORTANT for logout
    }

    return user_doc