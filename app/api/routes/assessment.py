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
    # This acts as the API 1 endpoint to receive the profile data.
    # In a real database, you would save it here based on device_id.
    logger.info(f"Received profile assessment for device: {data.get('device_id')}")
    return {
        "status": "success",
        "message": "Profile saved.",
        "device_id": data.get("device_id")
    }