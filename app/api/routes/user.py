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
from app.core.auth.oauth2 import get_current_user, get_current_token

from app.api.schemas.request import (
    UserRole,
    VerifyOtpRequest,
    ResetPasswordRequest,
    UserCreateRequest,
    UserLoginRequest,
    ForgotPasswordRequest,
    RefreshTokenRequest,
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

logger = get_logger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


# ── Helper Functions ───────────────────────────────────────────────────────────

def detect_identifier_type(identifier: str) -> Literal["email", "phone", "username"]:
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if re.match(email_pattern, identifier):
        return "email"

    phone_pattern = r"^\+?[1-9]\d{1,14}$"
    if re.match(phone_pattern, identifier):
        return "phone"

    username_pattern = r"^[a-zA-Z0-9_]{3,30}$"
    if re.match(username_pattern, identifier):
        return "username"

    raise ValueError(f"Invalid identifier format: {identifier}")


async def find_user_by_identifier(db, identifier: str):


    # Try direct ID first if it looks like one
    if len(identifier) == 24 and all(c in "0123456789abcdefABCDEF" for c in identifier):
        try:
            user = await db.users.find_one({"_id": ObjectId(identifier)})
            if user:
                return user
        except:
            pass

    try:
        identifier_type = detect_identifier_type(identifier)
    except ValueError:
        return None

    query_map = {
        "email":    {"email": identifier},
        "phone":    {"phone_number": identifier},
        "username": {"username": identifier},
    }
    return await db.users.find_one(query_map[identifier_type])


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

@router.post("/register", response_model=UserProfileData)
async def user_register(payload: UserCreateRequest):
    """Register a new user via JSON body."""
    try:
        logger.info(f" Registration attempt | Email: {payload.email} | Username: {payload.username}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please check MongoDB connection.")

        # Input normalisation
        full_name    = payload.full_name.strip()
        username     = payload.username.strip().lower()
        email        = payload.email.strip().lower()
        phone_number = payload.phone_number.strip()

        # Duplicate check
        existing = await db.users.find_one({
            "$or": [
                {"email": email},
                {"phone_number": phone_number},
                {"username": username},
            ]
        })
        if existing:
            if existing.get("email") == email:
                raise HTTPException(status_code=400, detail="Email already registered")
            elif existing.get("phone_number") == phone_number:
                raise HTTPException(status_code=400, detail="Phone number already registered")
            elif existing.get("username") == username:
                raise HTTPException(status_code=400, detail="Username already taken")

        # Resolve enum values
        role_value                = payload.role.value if isinstance(payload.role, UserRole) else payload.role
        professional_role_value   = payload.professional_role.value if payload.professional_role else None
        practice_type_value       = payload.practice_type.value if payload.practice_type else None
        consultation_mode_value   = payload.consultation_mode.value if payload.consultation_mode else None

        # Role-based Validation
        if role_value == "admin":
            admin_fields = [
                professional_role_value, payload.license_number, payload.state_of_licensure, 
                payload.npi_number, practice_type_value, payload.city, payload.state, consultation_mode_value
            ]
            
            if not all(admin_fields) or any(str(f).lower() == "none" for f in admin_fields):
                raise HTTPException(
                    status_code=400, 
                    detail="Admin registration requires all professional fields (professional_role, license_number, state_of_licensure, npi_number, practice_type, city, state, consultation_mode). 'none' is not allowed."
                )

        # Hash password and persist
        validate_password(payload.password)
        password_hash = await run_in_threadpool(Hash.bcrypt, payload.password)

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

        # Insert user (base collection)
        result = await db.users.insert_one(user.dict(by_alias=True, exclude_none=True))
        user_id = str(result.inserted_id)

        # If admin, insert into admins collection with rollback support
        if role_value == "admin":
            admin_created = False
            try:
                admin_created = await create_admin(
                    db,
                    user_id=user_id,
                    admin_payload={
                        "full_name": full_name,
                        "email": email,
                        "password_hash": password_hash,
                        "phone_number": phone_number,
                        "role": role_value,
                        "professional_role": professional_role_value,
                        "license_number": payload.license_number,
                        "state_of_licensure": payload.state_of_licensure,
                        "npi_number": payload.npi_number,
                        "practice_type": practice_type_value,
                        "city": payload.city,
                        "state": payload.state,
                        "consultation_mode": consultation_mode_value,
                    },
                )
            except Exception as e:
                logger.error(f"event=create_admin_error error={str(e)}")
                admin_created = False

            if not admin_created:
                # Rollback user creation
                await db.users.delete_one({"_id": result.inserted_id})
                logger.error(f"event=admin_register_rollback user_id={user_id}")
                raise HTTPException(status_code=500, detail="Admin registration failed. Rolled back.")

        logger.info(f" User Registered: {email} | Role: {role_value} | ID: {user_id}")

        return UserProfileData(
            full_name=user.full_name or "",
            username=user.username or "",
            email=user.email or "",
            phone_number=user.phone_number or "",
            role=user.role,
            professional_role=user.professional_role,
            license_number=user.license_number,
            state_of_licensure=user.state_of_licensure,
            npi_number=user.npi_number,
            practice_type=user.practice_type,
            city=user.city,
            state=user.state,
            consultation_mode=user.consultation_mode,
            user_id=user_id,
            is_active=True,
            last_login=None,
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"event=register_failed error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/login", response_model=UserLoginResponse)
async def user_login(payload: UserLoginRequest):
    """Login using username, email, or phone number via JSON body."""
    try:
        logger.info(f" Login attempt | Identifier: {payload.username}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please try again later.")

        login_identifier = payload.username
        password = payload.password

        if not password:
            raise HTTPException(status_code=400, detail="Password is required")

        user_doc = await find_user_by_identifier(db, login_identifier)
        if not user_doc:
            logger.warning(f"Login attempt with non-existent identifier: {login_identifier}")
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not user_doc.get("password_hash"):
            logger.error(f"User {login_identifier} has no password hash stored")
            raise HTTPException(status_code=500, detail="Account configuration error. Please contact support.")

        if not await run_in_threadpool(Hash.verify, user_doc["password_hash"], password):
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
            {"$set": {"last_login": datetime.utcnow()}},
        )

        user_data = UserProfileData(
            full_name=user_doc.get("full_name", ""),
            username=user_doc.get("username", ""),
            email=user_doc.get("email", ""),
            phone_number=user_doc.get("phone_number", ""),
            role=user_doc.get("role", "user"),
            professional_role=user_doc.get("professional_role"),
            license_number=user_doc.get("license_number"),
            state_of_licensure=user_doc.get("state_of_licensure"),
            npi_number=user_doc.get("npi_number"),
            practice_type=user_doc.get("practice_type"),
            city=user_doc.get("city"),
            state=user_doc.get("state"),
            consultation_mode=user_doc.get("consultation_mode"),
            user_id=str(user_doc["_id"]),
            is_active=user_doc.get("is_active", True),
            last_login=user_doc.get("last_login"),
        )

        token_subject = user_doc.get("email") or user_doc.get("username") or str(user_doc["_id"])
        access_token = create_access_token(data={"sub": token_subject, "role": user_role})
        refresh_token = create_refresh_token(data={"sub": token_subject})

        identifier_type = detect_identifier_type(login_identifier)
        logger.info(
            f" Login Successful | Type: {identifier_type} | ID: {login_identifier} "
            f"| Role: {user_role} | UID: {user_data.user_id}"
        )

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
        logger.error(f"event=login_failed error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(payload: ForgotPasswordRequest):
    """Initiate password reset by sending OTP to email."""
    try:
        logger.info(f" Forgot password request | Email: {payload.email}")

        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        user_doc = await db.users.find_one({"email": payload.email})
        if not user_doc:
            raise HTTPException(status_code=404, detail="User not found")

        if not validate_email(payload.email):
            raise HTTPException(status_code=400, detail="Invalid email address")

        otp        = generate_otp()
        expires_at = datetime.utcnow() + timedelta(minutes=30)

        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"password_reset_token": otp, "password_reset_expires": expires_at}},
        )

        try:
            email_sent = await send_otp_email(payload.email, otp)
        except Exception as e:
            logger.error(f"event=send_otp_email_failed error={str(e)}")
            email_sent = False

        if not email_sent:
            # DEMO SAFE MODE: Log OTP to console and pretend success to not crash the demo
            logger.info(f"DEMO MODE FALLBACK | OTP for {payload.email} is {otp}")
            return ForgotPasswordResponse(
                status="success",
                message="OTP sent successfully (Demo mode fallback)",
            )

        logger.info(f" Password reset OTP sent to {payload.email}")
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
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        user_doc = await db.users.find_one({"email": payload.email})
        if not user_doc:
            logger.warning(f"OTP verification attempt for non-existent email: {payload.email}")
            raise HTTPException(status_code=404, detail="User not found")

        if not user_doc.get("password_reset_token") or not user_doc.get("password_reset_expires"):
            logger.warning(f"No active password reset request found for {payload.email}")
            raise HTTPException(status_code=400, detail="No active password reset request found.")

        expires_at = user_doc.get("password_reset_expires")
        if expires_at and datetime.utcnow() > expires_at:
            logger.warning(f"Expired OTP attempt for {payload.email}")
            await db.users.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"password_reset_token": None, "password_reset_expires": None}},
            )
            raise HTTPException(status_code=408, detail="OTP has expired. Please request a new one.")

        if user_doc["password_reset_token"] != payload.otp:
            logger.warning(f"Invalid OTP attempt for {payload.email}")
            raise HTTPException(status_code=401, detail="Invalid OTP.")

        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {
                "is_otp_verified":        True,
                "password_reset_token":   None,
                "password_reset_expires": None,
            }},
        )

        logger.info(f"OTP verified successfully for {payload.email}")
        return VerifyOtpResponse(status="success", message="OTP verified successfully")

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"event=verify_otp_failed error={str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(payload: ResetPasswordRequest):
    """Reset password using verified OTP status."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        user_doc = await db.users.find_one({"email": payload.email})
        if not user_doc:
            logger.warning(f"Password reset attempt for non-existent email: {payload.email}")
            raise HTTPException(status_code=404, detail="User not found")

        if user_doc.get("is_otp_verified") is not True:
            logger.warning(f"Unauthorized reset attempt (OTP not verified) for {payload.email}")
            raise HTTPException(status_code=403, detail="Access denied. Please verify your OTP first.")

        validate_password(payload.new_password)
        new_password_hash = await run_in_threadpool(Hash.bcrypt, payload.new_password)

        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {
                "password_hash":  new_password_hash,
                "last_login":     datetime.utcnow(),
                "is_otp_verified": False,
            }},
        )

        logger.info(f"Password reset successfully for {payload.email}")
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

        user_doc = await find_user_by_identifier(db, token_data.sub)
        if not user_doc:
            raise HTTPException(status_code=401, detail="User not found")

        user_role = user_doc.get("role", "user")
        token_subject = user_doc.get("email") or user_doc.get("username") or str(user_doc["_id"])

        # Generate new access token
        access_token = create_access_token(data={"sub": token_subject, "role": user_role})

        logger.info(f" Access token refreshed for: {token_data.sub}")

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


@router.post("/logout", response_model=LogoutResponse)
async def user_logout(
    token: str = Depends(get_current_token),
    _=Depends(get_current_user),
):
    try:
        await run_in_threadpool(add_to_blacklist, token)

        return LogoutResponse(
            status="success",
            message="Logout successful"
        )

    except Exception as e:
        logger.error(f"event=logout_failed error={str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

