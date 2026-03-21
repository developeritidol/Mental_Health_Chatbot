from fastapi import APIRouter

router = APIRouter(prefix="/api/assessment", tags=["assessment"])

@router.get("/topics")
async def get_topics():
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