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
            "Just need to talk",
        ]
    }