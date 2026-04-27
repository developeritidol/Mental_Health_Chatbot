"""
User Routes
-----------
POST /api/users/register
POST /api/users/login
POST /api/users/forgot-password
POST /api/users/verify-otp
POST /api/users/reset-password
POST /api/users/logout
"""

import re
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool
from datetime import datetime, timedelta
from app.core.auth.oauth2 import get_current_user
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext


token_auth_scheme = HTTPBearer()

from app.api.schemas.request import (
    VerifyOtpRequest,
    ResetPasswordRequest,
    RefreshTokenRequest,
    ForgotPasswordRequest,
    UserCreateRequest,
    UserLoginRequest,
)
from app.api.schemas.response import (
    UserLoginResponse,
    ForgotPasswordResponse,
    VerifyOtpResponse,
    RefreshTokenResponse,
    LogoutResponse,
    ResetPasswordResponse,
    UserProfileData,
    UserProfileResponse,
)
from app.models.db import UserModelDB
from bson import ObjectId
from app.core.database import get_database
from app.core.logger import get_logger
from app.core.auth.hashing import Hash
from app.core.auth.password_policy import validate_password
from app.core.auth.JWTtoken import (
    create_access_token, 
    create_refresh_token, 
    verify_refresh_token,
)
from app.core.auth.token_blacklist import add_to_blacklist
from app.services.email_service import generate_otp, validate_email, send_otp_email
from app.services.admin_service import create_admin

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
logger = get_logger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


# ── Helper Functions ───────────────────────────────────────────────────────────

def detect_identifier_type(identifier: str) -> Literal["email", "phone"]:
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if re.match(email_pattern, identifier):
        return "email"

    phone_pattern = r"^\+?[1-9]\d{1,14}$"
    if re.match(phone_pattern, identifier):
        return "phone"

    raise ValueError(f"Invalid identifier format: {identifier}")


async def find_user_by_identifier(db, identifier: str):
    identifier = identifier.strip().lower()
    identifier_type = detect_identifier_type(identifier)
    query_map = {
        "email":    {"email": identifier},
        "phone":    {"phone_number": identifier},
    }
    query = query_map[identifier_type]

    user_doc = await db.users.find_one(query)
    if not user_doc:
        user_doc = await db.admins.find_one(query)

    # Validate database response
    if user_doc and not isinstance(user_doc, dict):
        logger.error("Database returned invalid user document format")
        return None
    return user_doc


def validate_user_role(role: str) -> str:
    if not role:
        raise HTTPException(status_code=400, detail="Role is required")
    normalized = role.strip().lower()
    if normalized not in {"user", "admin"}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{role}'. Allowed: admin, user",
        )
    return normalized


def validate_account_status(user_doc: dict) -> None:
    if not user_doc.get("is_active", True):
        raise HTTPException(
            status_code=403,
            detail="Account is disabled. Please contact support for assistance.",
        )


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserLoginResponse)
async def user_register(payload: UserCreateRequest):
    """Register a new user via JSON body."""
    try:
        # Role validation - single is_user field determines role
        # is_user = True -> regular user, is_user = False -> admin

        masked_email = payload.email[:2] + "***" + payload.email.split("@")[1] if "@" in payload.email else "***"
        logger.info(f" Registration attempt | Email: {masked_email}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please try again later.")

        # Input normalisation
        full_name    = payload.full_name.strip()
        email        = payload.email.strip().lower()
        phone_number = payload.phone_number.strip()

        # Duplicate check: email or phone already exists? Ensure strict separation across both collections.
        existing_user = await db.users.find_one({
            "$or": [{"email": email}, {"phone_number": phone_number}]
        })
        existing_admin = await db.admins.find_one({
            "$or": [{"email": email}, {"phone_number": phone_number}]
        })
        existing = existing_user or existing_admin
        if existing:
            if existing.get("email") == email:
                raise HTTPException(status_code=400, detail="Email already registered")
            elif existing.get("phone_number") == phone_number:
                raise HTTPException(status_code=400, detail="Phone number already registered")

        # Hash password and persist
        validate_password(payload.password)
        password_hash = await run_in_threadpool(Hash.bcrypt, payload.password)

        user = UserModelDB(
            full_name=full_name,
            email=email,
            phone_number=phone_number,
            password_hash=password_hash,
            is_user=payload.is_user,
            professional_role=payload.professional_role,
            license_number=payload.license_number,
            state_of_licensure=payload.state_of_licensure,
            npi_number=payload.npi_number,
            practice_type=payload.practice_type,
            city=payload.city,
            state=payload.state,
            consultation_mode=payload.consultation_mode,
        )

        if payload.is_user:
            result = await db.users.insert_one(user.dict(by_alias=True, exclude_none=True))
            role_log = "user"
        else:  # payload.is_user = False means admin
            result = await db.admins.insert_one(user.dict(by_alias=True, exclude_none=True))
            role_log = "admin"

        user_id = str(result.inserted_id)

        masked_email_log = email[:2] + "***" + email.split("@")[1] if "@" in email else "***"
        logger.info(f" User Registered: {masked_email_log} | Role: {role_log}")

        # Generate tokens for immediate login after registration
        token_subject = email
        user_role = "admin" if not user.is_user else "user"  # is_user=False means admin
        access_token = create_access_token(data={"sub": token_subject, "role": user_role, "user_id": user_id})
        refresh_token = create_refresh_token(data={"sub": token_subject, "role": user_role, "user_id": user_id})

        # Create user profile data for response
        user_data = UserProfileData(
            user_id=user_id,
            full_name=user.full_name or "",
            email=user.email or "",
            phone_number=user.phone_number or "",
            is_user=user.is_user,
            professional_role=user.professional_role,
            license_number=user.license_number,
            state_of_licensure=user.state_of_licensure,
            npi_number=user.npi_number,
            practice_type=user.practice_type,
            city=user.city,
            state=user.state,
            consultation_mode=user.consultation_mode,
            created_at=user.created_at,
            last_login=user.last_login,
        )

        return UserLoginResponse(
            status="success",
            message="Registration successful",
            user=user_data,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"event=register_failed error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/login", response_model=UserLoginResponse)
async def user_login(payload: UserLoginRequest):
    """Login using email via JSON body."""
    try:
        masked_email_login = payload.email[:2] + "***" + payload.email.split("@")[1] if "@" in payload.email else "***"
        logger.info(f" Login attempt | Email: {masked_email_login}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please try again later.")

        login_identifier = payload.email.strip().lower()
        password = payload.password

        user_doc = await find_user_by_identifier(db, login_identifier)
        if not user_doc:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Verify password
        stored_hash = user_doc.get("password_hash")

        if not stored_hash or not await run_in_threadpool(pwd_context.verify, password, stored_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
        # Update last login
        if not user_doc or "_id" not in user_doc:
            raise HTTPException(status_code=500, detail="User data corrupted")
            
        collection = db.admins if not user_doc.get("is_user", True) else db.users

        await collection.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"last_login": datetime.utcnow()}}
        )

        # Determine role for JWT - is_user=False means admin
        user_role = "admin" if not user_doc.get("is_user", True) else "user"

        # Extract user_id BEFORE using it
        if not user_doc or "_id" not in user_doc:
            raise HTTPException(status_code=500, detail="User data corrupted")
        user_id_str = str(user_doc["_id"])
        token_subject = user_doc.get("email") or user_id_str

        user_data = UserProfileData(
            user_id=user_id_str,
            full_name=user_doc.get("full_name", ""),
            email=user_doc.get("email", ""),
            phone_number=user_doc.get("phone_number", ""),
            is_user=user_doc.get("is_user", True),
            professional_role=user_doc.get("professional_role"),
            license_number=user_doc.get("license_number"),
            state_of_licensure=user_doc.get("state_of_licensure"),
            npi_number=user_doc.get("npi_number"),
            practice_type=user_doc.get("practice_type"),
            city=user_doc.get("city"),
            state=user_doc.get("state"),
            consultation_mode=user_doc.get("consultation_mode"),
            created_at=user_doc.get("created_at"),
            last_login=user_doc.get("last_login"),
        )
        access_token = create_access_token(data={"sub": token_subject, "role": user_role, "user_id": user_id_str})
        refresh_token = create_refresh_token(data={"sub": token_subject, "role": user_role, "user_id": user_id_str})

        identifier_type = detect_identifier_type(login_identifier)
        logger.info(
            f" Login Successful | Type: {identifier_type} | Role: {user_role}"
        )

        return UserLoginResponse(
            status="success",
            message="Login successful",
            user=user_data,
            access_token=access_token,
            refresh_token=refresh_token,
        )
# print("USER FROM DB:", user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"event=login_failed error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(payload: ForgotPasswordRequest):
    """Send OTP to user's email for password reset."""
    try:
        masked_email = payload.email[:2] + "***" + payload.email.split("@")[-1] if "@" in payload.email else "***"
        logger.info(f" Forgot password request | Email: {masked_email}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please try again later.")

        # Check if email exists
        user_doc = await find_user_by_identifier(db, payload.email)
        if not user_doc:
            # For security, don't reveal if email exists or not
            logger.warning(f"Forgot password attempt for non-existent email: {masked_email}")
            return ForgotPasswordResponse(
                status="success",
                message="If your email is registered, you will receive an OTP shortly.",
            )

        # Generate OTP
        otp = generate_otp()
        expires_at = datetime.utcnow() + timedelta(minutes=15)

        # Store OTP in database
        if not user_doc or "_id" not in user_doc:
            raise HTTPException(status_code=500, detail="User data corrupted")
            
        collection = db.admins if user_doc.get("is_admin") else db.users
        await collection.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"password_reset_token": otp, "password_reset_expires": expires_at}}
        )

        try:
            email_sent = await send_otp_email(payload.email, otp)
        except Exception as e:
            logger.error(f"event=send_otp_email_failed error={str(e)}")
            email_sent = False

        if not email_sent:
            logger.error(f"Failed to send OTP email to {masked_email}")
            raise HTTPException(status_code=500, detail="Failed to send OTP email. Please try again later.")

        logger.info(f" Password reset OTP sent to {masked_email}")
        return ForgotPasswordResponse(
            status="success",
            message="OTP sent to your email. Please check your inbox.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"event=forgot_password_failed error={str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/verify-otp", response_model=VerifyOtpResponse)
async def verify_otp(payload: VerifyOtpRequest):
    """Verify OTP for password reset."""
    try:
        masked_email = payload.email[:2] + "***" + payload.email.split("@")[-1] if "@" in payload.email else "***"
        logger.info(f" OTP verification attempt | Email: {masked_email}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please try again later.")

        user_doc = await find_user_by_identifier(db, payload.email)
        if not user_doc:
            raise HTTPException(status_code=404, detail="User not found")

        stored_otp = user_doc.get("password_reset_token")
        expires_at = user_doc.get("password_reset_expires")

        if not stored_otp or not expires_at:
            raise HTTPException(status_code=400, detail="No reset request found. Please request a new OTP.")

        if datetime.utcnow() > expires_at:
            raise HTTPException(status_code=400, detail="OTP has expired. Please request a new OTP.")

        if payload.otp != stored_otp:
            raise HTTPException(status_code=400, detail="Invalid OTP. Please check and try again.")

        # OTP is valid, clear it from database
        if not user_doc or "_id" not in user_doc:
            raise HTTPException(status_code=500, detail="User data corrupted")
            
        collection = db.admins if user_doc.get("is_admin") else db.users
        await collection.update_one(
            {"_id": user_doc["_id"]},
            {"$unset": {"password_reset_token": "", "password_reset_expires": ""}}
        )

        logger.info(f" OTP verified successfully for {masked_email}")
        return VerifyOtpResponse(
            status="success",
            message="OTP verified successfully. You can now reset your password.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"event=verify_otp_failed error={str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(payload: ResetPasswordRequest):
    """Reset password after OTP verification."""
    try:
        masked_email = payload.email[:2] + "***" + payload.email.split("@")[-1] if "@" in payload.email else "***"
        logger.info(f" Password reset attempt | Email: {masked_email}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please try again later.")

        user_doc = await find_user_by_identifier(db, payload.email)
        if not user_doc:
            raise HTTPException(status_code=404, detail="User not found")

        # Check if there was a recent OTP verification (by checking if reset fields were cleared)
        if user_doc.get("password_reset_token") or user_doc.get("password_reset_expires"):
            raise HTTPException(status_code=400, detail="Please verify your OTP first before resetting password.")

        # Validate new password
        validate_password(payload.new_password)

        # Hash new password
        password_hash = await run_in_threadpool(Hash.bcrypt, payload.new_password)

        # Update password
        if not user_doc or "_id" not in user_doc:
            raise HTTPException(status_code=500, detail="User data corrupted")
            
        collection = db.admins if user_doc.get("is_admin") else db.users
        await collection.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"password_hash": password_hash, "last_active": datetime.utcnow()}}
        )

        logger.info(f" Password reset successfully for {masked_email}")
        return ResetPasswordResponse(
            status="success",
            message="Password reset successfully. You can now login with your new password.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"event=reset_password_failed error={str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(payload: RefreshTokenRequest):
    """Refresh access token using a valid refresh token."""
    try:
        logger.info(" Refresh token attempt")

        credentials_exception = HTTPException(
            status_code=401,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

        token_data = await run_in_threadpool(verify_refresh_token, payload.refresh_token, credentials_exception)

        # Get user from database to fetch role
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed")

        user_doc = await find_user_by_identifier(db, token_data.email)
        if not user_doc:
            raise HTTPException(status_code=401, detail="User not found")

        user_role = "admin" if user_doc.get("is_admin") else "user"
        if not user_doc or "_id" not in user_doc:
            raise HTTPException(status_code=500, detail="User data corrupted")
        token_subject = user_doc.get("email") or str(user_doc["_id"])
        user_id_str = str(user_doc["_id"])

        # Generate new access token
        access_token = create_access_token(data={"sub": token_subject, "role": user_role, "user_id": user_id_str})

        logger.info(f" Access token refreshed for: {token_data.email}")

        return RefreshTokenResponse(
            status="success",
            access_token=access_token,
            token_type="bearer"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"event=refresh_token_failed error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logout")
async def user_logout(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
    current_user: dict = Depends(get_current_user),
):
    try:
        await run_in_threadpool(add_to_blacklist, credentials.credentials)

        return {
            "status": "success", 
            "message": "Successfully logged out"
        }

    except Exception as e:
        logger.error(f"event=logout_failed error={str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
