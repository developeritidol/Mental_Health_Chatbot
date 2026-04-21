from fastapi import Depends, HTTPException, status, Header, Security
from fastapi.security import HTTPBearer

from app.core.database import get_database

from app.core.auth.JWTtoken import verify_token
from app.core.auth.token_blacklist import is_blacklisted

def get_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")

    return authorization.split(" ")[1]

security = HTTPBearer()

# Made this async so it can query the database
async def get_current_user(credentials = Security(security)):
    token = credentials.credentials

    # 1. Strip "Bearer " if Swagger accidentally doubled it
    if token.startswith("Bearer "):
        token = token.split(" ")[1]

    print("CLEAN TOKEN RECEIVED:", token)

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 2. Single source of truth for decoding and checking blacklist
    # (This uses the verify_token function imported from JWTtoken.py)
    token_data = verify_token(token, credentials_exception)

    # 3. Connect to DB to find the real user_id associated with this email
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    user_doc = await db.users.find_one({"email": token_data.useremail})
    
    if not user_doc:
        raise HTTPException(status_code=404, detail="User account not found in database.")

    # 4. Return the full database document so chat.py gets the user_id!
    return user_doc