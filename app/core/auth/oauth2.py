# from fastapi import Depends, HTTPException, status, Security
# from fastapi.security import HTTPBearer

# from app.core.auth.JWTtoken import verify_token
# from app.api.schemas.response import TokenData


# security = HTTPBearer()

# async def get_current_user(credentials = Security(security)):
#     token = credentials.credentials

#     # 1. Strip "Bearer " if Swagger acciden tally doubled it
#     if token.startswith("Bearer "):
#         token = token.split(" ")[1]

#     print("CLEAN TOKEN RECEIVED:", token)

#     credentials_exception = HTTPException(
#         status_code=status.HTTP_401_UNAUTHORIZED,
#         detail="Invalid authentication credentials",
#         headers={"WWW-Authenticate": "Bearer"},
#     )

#     # 2. Single source of truth for decoding and checking blacklist
#     # (This uses the verify_token function imported from JWTtoken.py)
#     token_data = verify_token(token, credentials_exception)

#     # 3. Create a stateless user object directly from the token data!
#     user_doc = {
#         "_id": token_data.user_id,         
#         "user_id": token_data.user_id,     
#         "email": token_data.useremail,     
#         "useremail": token_data.useremail
#     }

#     # 4. Return the dictionary so chat.py gets the user_id instantly
#     return user_doc




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