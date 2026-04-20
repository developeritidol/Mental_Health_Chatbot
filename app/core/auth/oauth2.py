from fastapi import Depends, HTTPException, status , Header
from fastapi import Security
from fastapi.security import HTTPBearer
from app.core.auth import JWTtoken
# from app.core.auth.token_blacklist import is_blacklisted
from app.core.auth.token_blacklist import is_blacklisted
from app.core.auth.JWTtoken import verify_token
from app.core.auth.token_blacklist import is_blacklisted


# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/users/login")

def get_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")

    return authorization.split(" ")[1]


# def get_current_user(data: str = Depends(get_token)):
#     credentials_exception = HTTPException(
#         status_code=status.HTTP_401_UNAUTHORIZED,
#         detail="Could not validate credentials",
#         headers={"WWW-Authenticate": "Bearer"},
#     )

#     return JWTtoken.verify_token(data, credentials_exception)

security = HTTPBearer()

# def get_current_user(credentials = Security(security)):
#     token = credentials.credentials
    
#     if is_blacklisted(token):
#         raise HTTPException(status_code=401, detail="Token has been revoked")

#     return token


# def get_current_user(credentials = Security(security)):
#     token = credentials.credentials

#     print("TOKEN RECEIVED:", token)

#     credentials_exception = HTTPException(
#         status_code=status.HTTP_401_UNAUTHORIZED,
#         detail="Invalid authentication credentials",
#         headers={"WWW-Authenticate": "Bearer"},
#     )

#     # ✅ Check blacklist
#     if is_blacklisted(token):
#         print("TOKEN IS BLACKLISTED")
#         raise HTTPException(status_code=401, detail="Token has been revoked")

#     # ✅ VERIFY TOKEN (THIS WAS MISSING)
#     token_data = verify_token(token, credentials_exception)

#     return token_data


def get_current_user(credentials = Security(security)):
    token = credentials.credentials

    print("TOKEN RECEIVED:", token)

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # ❌ REMOVE manual blacklist check from here

    # ✅ Single source of truth
    token_data = verify_token(token, credentials_exception)

    return token_data

def verify_token(token: str, credentials_exception):

    # ✅ ONLY place where blacklist is checked
    if is_blacklisted(token):
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked",
        )