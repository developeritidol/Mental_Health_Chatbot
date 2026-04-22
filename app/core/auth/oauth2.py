from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer

from app.core.auth.JWTtoken import verify_token
from app.api.schemas.response import TokenData


security = HTTPBearer()

<<<<<<< HEAD
def get_current_token(credentials = Security(security)):
    return credentials.credentials

def get_current_user(credentials = Security(security)):
    token = credentials.credentials
=======
async def get_current_user(credentials = Security(security)):
    token = credentials.credentials

    # 1. Strip "Bearer " if Swagger acciden tally doubled it
    if token.startswith("Bearer "):
        token = token.split(" ")[1]

    print("CLEAN TOKEN RECEIVED:", token)

>>>>>>> 1317e4c411bdef0b6b5dba31035af40b6db0bd5b
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

<<<<<<< HEAD
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
=======
    # 2. Single source of truth for decoding and checking blacklist
    # (This uses the verify_token function imported from JWTtoken.py)
    token_data = verify_token(token, credentials_exception)

    # 3. Create a stateless user object directly from the token data!
    user_doc = {
        "_id": token_data.user_id,         
        "user_id": token_data.user_id,     
        "email": token_data.useremail,     
        "useremail": token_data.useremail
    }

    # 4. Return the dictionary so chat.py gets the user_id instantly
    return user_doc
>>>>>>> 1317e4c411bdef0b6b5dba31035af40b6db0bd5b
