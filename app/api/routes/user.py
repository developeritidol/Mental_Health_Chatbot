"""
User Routes
────────────
POST /api/users/register
POST /api/users/login
"""

import re
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.security import OAuth2PasswordRequestForm
from datetime import datetime, timedelta
from app.core.auth.oauth2 import get_current_user

from app.api.schemas.request import (
    ProfessionalRole,
    PracticeType,
    ConsultationMode,
)
from app.api.schemas.response import UserSignupResponse, UserLoginResponse, ForgotPasswordResponse, VerifyOtpResponse, ResetPasswordResponse
from app.models.db import UserModelDB
from app.core.database import get_database
from app.core.logger import get_logger
from app.core.auth.hashing import Hash
from app.core.auth.JWTtoken import create_access_token, create_refresh_token
from app.services.email_service import generate_otp, validate_email, send_otp_email

logger = get_logger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


# ── Helper Functions ───────────────────────────────────────────────────────

ALLOWED_ROLES = {"user", "admin"}

def detect_identifier_type(identifier: str) -> Literal["email", "phone", "username"]:
    """
    Detect the type of identifier provided (email, phone, or username).
    
    Args:
        identifier: The user-provided identifier string
        
    Returns:
        One of: "email", "phone", "username"
        
    Raises:
        ValueError: If identifier format is invalid
    """
    # Email pattern: contains @ and domain
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if re.match(email_pattern, identifier):
        return "email"
    
    # Phone pattern: starts with + followed by digits, or just digits
    phone_pattern = r"^\+?[1-9]\d{1,14}$"
    if re.match(phone_pattern, identifier):
        return "phone"
    
    # Username pattern: alphanumeric with underscores, 3-30 chars
    username_pattern = r"^[a-zA-Z0-9_]{3,30}$"
    if re.match(username_pattern, identifier):
        return "username"
    
    raise ValueError(f"Invalid identifier format: {identifier}")


async def find_user_by_identifier(db, identifier: str):
    """
    Find a user in the database by email, phone number, or username.
    
    Args:
        db: Database connection
        identifier: User-provided identifier (email, phone, or username)
        
    Returns:
        User document or None if not found
    """
    try:
        identifier_type = detect_identifier_type(identifier)
    except ValueError:
        return None
    
    query_map = {
        "email": {"email": identifier},
        "phone": {"phone_number": identifier},
        "username": {"username": identifier}
    }
    
    query = query_map[identifier_type]
    return await db.users.find_one(query)


def validate_user_role(role: str) -> None:
    """
    Validate that the user role is allowed.
    
    Args:
        role: The user's role
        
    Raises:
        HTTPException: If role is invalid
    """
    if not role:
        raise HTTPException(
            status_code=400,
            detail="Role is required"
        )
    
    normalized_role = role.strip().lower()
    
    if normalized_role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{role}'. Allowed roles: {', '.join(sorted(ALLOWED_ROLES))}"
        )
    
    return normalized_role


def validate_account_status(user_doc: dict) -> None:
    """
    Validate that the user account is active and can log in.
    
    Args:
        user_doc: User document from database
        
    Raises:
        HTTPException: If account is inactive or disabled
    """
    is_active = user_doc.get("is_active", True)
    
    if not is_active:
        raise HTTPException(
            status_code=403,
            detail="Account is disabled. Please contact support for assistance."
        )


@router.post("/register", response_model=UserSignupResponse)
async def user_register(
    full_name: str = Query(..., min_length=1, max_length=100),
    username: str = Query(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$"),
    email: str = Query(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"),
    password: str = Query(..., min_length=8, max_length=128),
    phone_number: str = Query(..., pattern=r"^\+?[1-9]\d{1,14}$"),
    role: str = Query("user", min_length=1, max_length=50),
    professional_role: Optional[ProfessionalRole] = Query(None),
    license_number: Optional[str] = Query(None, min_length=1, max_length=50),
    state_of_licensure: Optional[str] = Query(None, min_length=1, max_length=50),
    npi_number: Optional[str] = Query(None, min_length=10, max_length=10),
    practice_type: Optional[PracticeType] = Query(None),
    city: Optional[str] = Query(None, min_length=1, max_length=50),
    state: Optional[str] = Query(None, min_length=1, max_length=50),
    consultation_mode: Optional[ConsultationMode] = Query(None),
):
    """Register a normal user or admin using Query Parameters."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed. Please check MongoDB connection.")

        existing = await db.users.find_one({"$or": [{"email": email}, {"phone_number": phone_number}, {"username": username}]})
        if existing:
            if existing.get("email") == email:
                raise HTTPException(status_code=400, detail="Email already registered")
            elif existing.get("phone_number") == phone_number:
                raise HTTPException(status_code=400, detail="Phone number already registered")
            elif existing.get("username") == username:
                raise HTTPException(status_code=400, detail="Username already taken")

        password_hash = Hash.bcrypt(password)
        role_value = role.strip().lower()

        if role_value == "admin":
            missing_fields = []
            if professional_role is None:
                missing_fields.append("professional_role")
            if license_number is None:
                missing_fields.append("license_number")
            if state_of_licensure is None:
                missing_fields.append("state_of_licensure")
            if npi_number is None:
                missing_fields.append("npi_number")
            if practice_type is None:
                missing_fields.append("practice_type")
            if city is None:
                missing_fields.append("city")
            if state is None:
                missing_fields.append("state")
            if consultation_mode is None:
                missing_fields.append("consultation_mode")

            if missing_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing admin fields for role 'admin': {', '.join(missing_fields)}"
                )

            professional_role_value = professional_role.value
            practice_type_value = practice_type.value
            consultation_mode_value = consultation_mode.value
        else:
            professional_role_value = None
            license_number = None
            state_of_licensure = None
            npi_number = None
            practice_type_value = None
            city = None
            state = None
            consultation_mode_value = None

        user = UserModelDB(
            full_name=full_name,
            username=username,
            email=email,
            password_hash=password_hash,
            phone_number=phone_number,
            professional_role=professional_role_value,
            license_number=license_number,
            state_of_licensure=state_of_licensure,
            npi_number=npi_number,
            practice_type=practice_type_value,
            city=city,
            state=state,
            consultation_mode=consultation_mode_value,
            role=role_value,
        )

        result = await db.users.insert_one(user.dict(by_alias=True, exclude_none=True))
        user_id = str(result.inserted_id)

        logger.info(f"✅ User Registered Successfully: {email} | Role: {role_value} | ID: {user_id}")

        return UserSignupResponse(
            status="success",
            message="Registration successful",
            user_id=user_id,
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"❌ Register Error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Registration failed: {str(e)}"
        )


@router.post("/login", response_model=UserLoginResponse)
async def user_login(
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    Login using username, email, or phone number.
    
    Supports three identifier types:
    - Username: alphanumeric with underscores (3-30 characters)
    - Email: standard email format
    - Phone: international format with optional + prefix
    
    Validates:
    - User exists with provided identifier
    - Password is correct
    - Role is valid (user or admin)
    - Account is active (not disabled)
    """
    try:
        # Database connection check
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection failed. Please try again later."
            )

        # Extract credentials from OAuth2 form
        login_identifier = form_data.username  # Can be username, email, or phone
        password = form_data.password

        # Validate password is provided
        if not password or len(password) < 1:
            raise HTTPException(
                status_code=400,
                detail="Password is required"
            )

        # Find user by identifier (username, email, or phone)
        user_doc = await find_user_by_identifier(db, login_identifier)
        
        # User not found - return generic error for security
        if not user_doc:
            logger.warning(f"Login attempt with non-existent identifier: {login_identifier}")
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )

        # Check if user has password hash
        if not user_doc.get("password_hash"):
            logger.error(f"User {login_identifier} has no password hash")
            raise HTTPException(
                status_code=500,
                detail="Account configuration error. Please contact support."
            )

        # Verify password
        if not Hash.verify(user_doc['password_hash'], password):
            logger.warning(f"Invalid password attempt for identifier: {login_identifier}")
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )

        # Validate user role
        user_role = user_doc.get("role", "user")
        try:
            validate_user_role(user_role)
        except HTTPException:
            logger.error(f"User {login_identifier} has invalid role: {user_role}")
            raise HTTPException(
                status_code=403,
                detail="Account configuration error. Please contact support."
            )

        # Validate account status (active/disabled)
        try:
            validate_account_status(user_doc)
        except HTTPException as e:
            logger.warning(f"Login attempt for disabled account: {login_identifier}")
            raise e

        # Update last login timestamp
        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"last_login": datetime.utcnow()}}
        )

        # Prepare user data for response (exclude sensitive fields)
        user_data = {k: v for k, v in user_doc.items() if k not in ['password_hash', '_id']}
        user_data['user_id'] = str(user_doc['_id'])

        # Generate JWT tokens
        token_subject = user_doc.get("email") or user_doc.get("username") or str(user_doc["_id"])
        access_token = create_access_token(data={"sub": token_subject, "role": user_role})
        refresh_token = create_refresh_token(data={"sub": token_subject})

        # Log successful login
        identifier_type = detect_identifier_type(login_identifier)
        logger.info(
            f"✅ Successful login | Type: {identifier_type} | ID: {login_identifier} | "
            f"Role: {user_role} | User ID: {user_data['user_id']}"
        )

        return UserLoginResponse(
            status="success",
            message="Login successful",
            user=user_data,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log unexpected errors
        logger.error(f"❌ Login Error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred during login. Please try again later."
        )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    email: str = Query(..., description="User email address"),
    # user = Depends(get_current_user)
):
    """Initiate password reset by sending OTP to email."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        # Find user by email
        user_doc = await db.users.find_one({"email": email})
        if not user_doc:
            logger.warning(f"Password reset attempt for non-existent email: {email}")
            raise HTTPException(status_code=404, detail="User not found")

        # Validate email is deliverable
        if not validate_email(email):
            logger.warning(f"Invalid email address: {email}")
            raise HTTPException(status_code=400, detail="Invalid email address")

        # Generate OTP
        otp = generate_otp()
        expires_at = datetime.utcnow() + timedelta(minutes=30)

        # Save OTP and expiry to database
        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {
                "$set": {
                    "password_reset_token": otp,
                    "password_reset_expires": expires_at,
                }
            },
        )

        # Send OTP via email
        email_sent = await send_otp_email(email, otp)
        if not email_sent:
            logger.error(f"Failed to send OTP email to {email}")
            raise HTTPException(status_code=500, detail="Failed to send OTP email")

        logger.info(f"Password reset OTP sent to {email}")
        return ForgotPasswordResponse(
            status="success",
            message="OTP sent to your email. Please check your inbox.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/verify-otp", response_model=VerifyOtpResponse)
async def verify_otp(
    email: str = Query(..., description="User email address"),
    otp: str = Query(..., min_length=6, max_length=6, description="6-digit OTP"),
    user = Depends(get_current_user)
):
    """Verify OTP for password reset."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        # Find user by email
        user_doc = await db.users.find_one({"email": email})
        if not user_doc:
            logger.warning(f"OTP verification attempt for non-existent email: {email}")
            raise HTTPException(status_code=404, detail="User not found")

        # Check if OTP exists
        if not user_doc.get("password_reset_token"):
            logger.warning(f"No OTP found for {email}")
            raise HTTPException(status_code=400, detail="No password reset request found")

        # Check if OTP matches
        if user_doc["password_reset_token"] != otp:
            logger.warning(f"Invalid OTP attempt for {email}")
            raise HTTPException(status_code=401, detail="Invalid OTP")

        # Check if OTP has expired
        expires_at = user_doc.get("password_reset_expires")
        if expires_at and datetime.utcnow() > expires_at:
            logger.warning(f"Expired OTP attempt for {email}")
            await db.users.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"password_reset_token": None, "password_reset_expires": None}},
            )
            raise HTTPException(status_code=408, detail="OTP has expired")

        logger.info(f"OTP verified successfully for {email}")
        return VerifyOtpResponse(
            status="success",
            message="OTP verified successfully. You can now reset your password.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"OTP verification error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    email: str = Query(..., description="User email address"),
    otp: str = Query(..., min_length=6, max_length=6, description="6-digit OTP"),
    new_password: str = Query(..., min_length=8, max_length=128, description="New password"),
    user = Depends(get_current_user)
):
    """Reset password using OTP."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        # Find user by email
        user_doc = await db.users.find_one({"email": email})
        if not user_doc:
            logger.warning(f"Password reset attempt for non-existent email: {email}")
            raise HTTPException(status_code=404, detail="User not found")

        # Check if OTP matches
        if user_doc.get("password_reset_token") != otp:
            logger.warning(f"Invalid OTP in reset for {email}")
            raise HTTPException(status_code=401, detail="Invalid OTP")

        # Check if OTP has expired
        expires_at = user_doc.get("password_reset_expires")
        if expires_at and datetime.utcnow() > expires_at:
            logger.warning(f"Expired OTP in reset for {email}")
            await db.users.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"password_reset_token": None, "password_reset_expires": None}},
            )
            raise HTTPException(status_code=408, detail="OTP has expired")

        # Hash new password
        new_password_hash = Hash.bcrypt(new_password)

        # Update password and clear OTP
        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {
                "$set": {
                    "password_hash": new_password_hash,
                    "password_reset_token": None,
                    "password_reset_expires": None,
                    "last_login": datetime.utcnow(),
                }
            },
        )

        logger.info(f"Password reset successfully for {email}")
        return ResetPasswordResponse(
            status="success",
            message="Password reset successfully. You can now login with your new password.",
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Password reset error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")