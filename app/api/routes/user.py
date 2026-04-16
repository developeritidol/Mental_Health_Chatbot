"""
User Routes
────────────
POST /api/users/register
POST /api/users/login
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.security import OAuth2PasswordRequestForm
from datetime import datetime,timedelta
from app.core.auth.oauth2 import get_current_user

from app.api.schemas.request import (
    ProfessionalRole,
    PracticeType,
    ConsultationMode,
    VerifyOtpRequest,
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


@router.post("/register", response_model=UserSignupResponse)
async def user_register(
    full_name: str = Query(..., min_length=1, max_length=100),
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

        existing = await db.users.find_one({"$or": [{"email": email}, {"phone_number": phone_number}]})
        if existing:
            raise HTTPException(status_code=400, detail="Email or phone number already registered")

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
    """Login for admin or normal user."""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection failed.")

        identifier = form_data.username
        password = form_data.password

        user_doc = await db.users.find_one({"$or": [{"email": identifier}, {"phone_number": identifier}]})
        if not user_doc or not user_doc.get("password_hash"):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not Hash.verify(user_doc['password_hash'], password):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        await db.users.update_one({"_id": user_doc["_id"]}, {"$set": {"last_login": datetime.utcnow()}})

        user_data = {k: v for k, v in user_doc.items() if k not in ['password_hash', '_id']}
        user_data['user_id'] = str(user_doc['_id'])

        access_token = create_access_token(data={"sub": user_doc["email"]})
        refresh_token = create_refresh_token(data={"sub": user_doc["email"]})

        logger.info(f"User logged in: {identifier}")
        return UserLoginResponse(
            status="success",
            message="Login successful",
            user=user_data,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Login Error: {e}")
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    email: str = Query(..., description="User email address"),
    #user = Depends(get_current_user)
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

        # Check if OTP exists
        if not user_doc.get("password_reset_token"):
            logger.warning(f"No OTP found for {payload.email}")
            raise HTTPException(status_code=400, detail="No password reset request found")

        # Check if OTP matches
        if user_doc["password_reset_token"] != payload.otp:
            logger.warning(f"Invalid OTP attempt for {payload.email}")
            raise HTTPException(status_code=401, detail="Invalid OTP")

        # Check if OTP has expired
        expires_at = user_doc.get("password_reset_expires")
        if expires_at and datetime.utcnow() > expires_at:
            logger.warning(f"Expired OTP attempt for {payload.email}")
            await db.users.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"password_reset_token": None, "password_reset_expires": None}},
            )
            raise HTTPException(status_code=408, detail="OTP has expired")

        # Mark OTP as verified
        await db.users.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"is_otp_verified": True}}
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
async def reset_password(
    email: str = Query(..., description="User email address"),
    otp: str = Query(..., description="6-digit OTP"),
    new_password: str = Query(..., min_length=8, max_length=128, description="New password"),
    #user = Depends(get_current_user)
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


@router.post("/logout")
async def user_logout(user = Depends(get_current_user)):
    """Simple logout."""
    return {"message": "Logout successful"}