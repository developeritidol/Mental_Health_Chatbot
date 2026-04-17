# app/api/routes/user.py
"""
User Routes
────────────
POST /api/users/register
POST /api/users/login
"""

import re
from typing import  Literal
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from datetime import datetime, timedelta
from app.core.auth.oauth2 import get_current_user

from app.api.schemas.request import (
    UserRole,
    VerifyOtpRequest,
    ResetPasswordRequest,
    UserCreateRequest,
    UserLoginRequest,
    ForgotPasswordRequest,
)
from app.api.schemas.response import (
    UserSignupResponse,
    UserLoginResponse,
    ForgotPasswordResponse,
    VerifyOtpResponse,
    ResetPasswordResponse,
)
from app.models.db import UserModelDB
from app.core.database import get_database
from app.core.logger import get_logger
from app.core.auth.hashing import Hash
from app.core.auth.JWTtoken import create_access_token, create_refresh_token
from app.services.email_service import generate_otp, validate_email, send_otp_email

logger = get_logger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


# ── Constants ──────────────────────────────────────────────────────────────

ADMIN_REQUIRED_FIELDS = [
    "professional_role", "license_number", "state_of_licensure",
    "npi_number", "practice_type", "city", "state", "consultation_mode"
]

USER_REQUIRED_FIELDS = ["city", "state"]


# ── Helper Functions ───────────────────────────────────────────────────────

def detect_identifier_type(identifier: str) -> Literal["email", "phone", "username"]:
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if re.match(email_pattern, identifier):
        return "email"

    phone_pattern = r"^\+?[1-9]\d{1,10}$"
    if re.match(phone_pattern, identifier):
        return "phone"

    username_pattern = r"^[a-zA-Z0-9_]{3,30}$"
    if re.match(username_pattern, identifier):
        return "username"

    raise ValueError(f"Invalid identifier format: {identifier}")


async def find_user_by_identifier(db, identifier: str):
    try:
        identifier_type = detect_identifier_type(identifier)
    except ValueError:
        return None

    query_map = {
        "email": {"email": identifier},
        "phone": {"phone_number": identifier},
        "username": {"username": identifier}
    }
    return await db.users.find_one(query_map[identifier_type])


def validate_user_role(role: str) -> str:
    if not role:
        raise HTTPException(status_code=400, detail="Role is required")

    normalized_role = role.strip().lower()

    if normalized_role not in {"user", "admin"}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{role}'. Allowed roles: admin, user"
        )

    return normalized_role


def validate_account_status(user_doc: dict) -> None:
    if not user_doc.get("is_active", True):
        raise HTTPException(
            status_code=403,
            detail="Account is disabled. Please contact support for assistance."
        )


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserSignupResponse)
async def user_register(payload: UserCreateRequest):
    """Register a user via JSON body with mandatory fields only."""
    try:
        # Log incoming payload for debugging
        logger.info(f" Registration attempt | Email: {payload.email} | Username: {payload.username}")
        
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please check MongoDB connection.")

        # ── Input normalization ───────────────────────────────────────
        full_name = payload.full_name.strip()
        username = payload.username.strip().lower()
        email = payload.email.strip().lower()
        phone_number = payload.phone_number.strip()

        # ── Duplicate check ──────────────────────────────────────────
        existing = await db.users.find_one({
            "$or": [
                {"email": email},
                {"phone_number": phone_number},
                {"username": username}
            ]
        })
        if existing:
            if existing.get("email") == email:
                raise HTTPException(status_code=400, detail="Email already registered")
            elif existing.get("phone_number") == phone_number:
                raise HTTPException(status_code=400, detail="Phone number already registered")
            elif existing.get("username") == username:
                raise HTTPException(status_code=400, detail="Username already taken")

        # ── Resolve role value ───────────────────────────────────────
        role_value = payload.role.value if isinstance(payload.role, UserRole) else payload.role

        # ── Resolve enum values for optional fields ──────────────────
        professional_role_value = payload.professional_role.value if payload.professional_role else None
        practice_type_value = payload.practice_type.value if payload.practice_type else None
        consultation_mode_value = payload.consultation_mode.value if payload.consultation_mode else None

        # ── Save to DB ───────────────────────────────────────────────
        password_hash = Hash.bcrypt(payload.password)

        user = UserModelDB(
            full_name=full_name,
            username=username,
            email=email,
            password_hash=password_hash,
            phone_number=phone_number,
            professional_role=professional_role_value,
            license_number=payload.license_number,
            state_of_licensure=payload.state_of_licensure,
            npi_number=payload.npi_number,
            practice_type=practice_type_value,
            city=payload.city,
            state=payload.state,
            consultation_mode=consultation_mode_value,
            role=role_value,
        )

        result = await db.users.insert_one(user.dict(by_alias=True, exclude_none=True))
        user_id = str(result.inserted_id)

        logger.info(f" User Registered: {email} | Role: {role_value} | ID: {user_id}")

        return UserSignupResponse(
            status="success",
            message="Registration successful",
            user_id=user_id,
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f" Register Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@router.post("/login", response_model=UserLoginResponse)
async def user_login(payload: UserLoginRequest):
    """Login using username, email, or phone number via JSON body."""
    try:
        # Log incoming payload for debugging
        logger.info(f" Login attempt | Username: {payload.username}")
        
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please try again later.")

        login_identifier = payload.username
        password = payload.password

        if not password:
            raise HTTPException(status_code=400, detail="Password is required")

        user_doc = await find_user_by_identifier(db, login_identifier)
        if not user_doc:
            logger.warning(f"Login attempt with non-existent username: {login_identifier}")
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not user_doc.get("password_hash"):
            logger.error(f"User {login_identifier} has no password hash")
            raise HTTPException(status_code=500, detail="Account configuration error. Please contact support.")

        if not Hash.verify(user_doc["password_hash"], password):
            logger.warning(f"Invalid password attempt for: {login_identifier}")
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user_role = user_doc.get("role", "user")
        try:
            validate_user_role(user_role)
        except HTTPException:
            logger.error(f"User {login_identifier} has invalid role: {user_role}")
            raise HTTPException(status_code=403, detail="Account configuration error. Please contact support.")

        validate_account_status(user_doc)

        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"last_login": datetime.utcnow()}}
        )

        user_data = {k: v for k, v in user_doc.items() if k not in ["password_hash", "_id"]}
        user_data["user_id"] = str(user_doc["_id"])

        token_subject = user_doc.get("email") or user_doc.get("username") or str(user_doc["_id"])
        access_token = create_access_token(data={"sub": token_subject, "role": user_role})
        refresh_token = create_refresh_token(data={"sub": token_subject})

        identifier_type = detect_identifier_type(login_identifier)
        logger.info(f" Login | Type: {identifier_type} | ID: {login_identifier} | Role: {user_role} | UID: {user_data['user_id']}")

        return UserLoginResponse(
            status="success",
            message="Login successful",
            user=user_data,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f" Login Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred during login.")


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(payload: ForgotPasswordRequest):
    """Initiate password reset by sending OTP to email via JSON body."""
    try:
        # Log incoming payload for debugging
        logger.info(f" Forgot password request | Email: {payload.email}")
        
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        user_doc = await db.users.find_one({"email": payload.email})
        if not user_doc:
            raise HTTPException(status_code=404, detail="User not found")

        if not validate_email(payload.email):
            raise HTTPException(status_code=400, detail="Invalid email address")

        otp = generate_otp()
        expires_at = datetime.utcnow() + timedelta(minutes=30)

        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"password_reset_token": otp, "password_reset_expires": expires_at}},
        )

        if not await send_otp_email(payload.email, otp):
            raise HTTPException(status_code=500, detail="Failed to send OTP email")

        logger.info(f" Password reset OTP sent to {payload.email}")
        return ForgotPasswordResponse(
            status="success",
            message="OTP sent to your email. Please check your inbox.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f" Forgot password error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/verify-otp", response_model=VerifyOtpResponse)
async def verify_otp(payload: VerifyOtpRequest):
    """Verify OTP for password reset."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        # Find user by email
        user_doc = await db.users.find_one({"email": payload.email})
        if not user_doc:
            logger.warning(f"OTP verification attempt for non-existent email: {payload.email}")
            raise HTTPException(status_code=404, detail="User not found")

        # Strict Expiry Check
        if not user_doc.get("password_reset_token") or not user_doc.get("password_reset_expires"):
            logger.warning(f"No active password reset request found for {payload.email}")
            raise HTTPException(status_code=400, detail="No active password reset request found.")

        # Check Expiration
        expires_at = user_doc.get("password_reset_expires")
        if expires_at and datetime.utcnow() > expires_at:
            logger.warning(f"Expired OTP attempt for {payload.email}")
            await db.users.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"password_reset_token": None, "password_reset_expires": None}},
            )
            raise HTTPException(status_code=408, detail="OTP has expired. Please request a new one.")

        # Validate Match
        if user_doc["password_reset_token"] != payload.otp:
            logger.warning(f"Invalid OTP attempt for {payload.email}")
            raise HTTPException(status_code=401, detail="Invalid OTP.")

        # Security Fix (Prevent Reuse)
        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {
                "$set": {
                    "is_otp_verified": True,
                    "password_reset_token": None,
                    "password_reset_expires": None
                }
            }
        )

        logger.info(f"OTP verified successfully for {payload.email}")
        return VerifyOtpResponse(
            status="success",
            message="OTP verified successfully",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"OTP verification error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(payload: ResetPasswordRequest):
    """Reset password using verified OTP status."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        # Find user by email
        user_doc = await db.users.find_one({"email": payload.email})
        if not user_doc:
            logger.warning(f"Password reset attempt for non-existent email: {payload.email}")
            raise HTTPException(status_code=404, detail="User not found")

        # Verify Authorization
        if user_doc.get("is_otp_verified") is not True:
            logger.warning(f"Unauthorized reset attempt (OTP not verified) for {payload.email}")
            raise HTTPException(status_code=403, detail="Access denied. Please verify your OTP first.")

        # Hash new password
        new_password_hash = Hash.bcrypt(payload.new_password)

        # Update Database & Lock Flow
        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {
                "$set": {
                    "password_hash": new_password_hash,
                    "last_login": datetime.utcnow(),
                    "is_otp_verified": False
                }
            }
        )

        logger.info(f"Password reset successfully for {payload.email}")
        return ResetPasswordResponse(
            status="success",
            message="Password reset successfully. You can now login with your new password.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Password reset error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/logout")
async def user_logout(user = Depends(get_current_user)):
    """Simple logout."""
    return {"message": "Logout successful"}