from fastapi import APIRouter
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/assessment", tags=["assessment"])

@router.get("/topics")
async def get_topics():
    logger.info("Fetching assessment topics")
    return {
        "topics": [
            "Stress & anxiety",
            "Feeling lonely",
            "Relationship issues",
            "Work or studies",
            "Grief or loss",
        ]
    }

@router.post("/submit")
async def submit_assessment(data: dict):
    from app.services.db_service import upsert_user_profile
    await upsert_user_profile(data)
    logger.info(f"Received and saved profile assessment for device: {data.get('device_id')}")
    return {
        "status": "success",
        "message": "Profile saved.",
        "device_id": data.get("device_id")
    }