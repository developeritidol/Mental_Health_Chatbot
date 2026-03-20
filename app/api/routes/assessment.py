"""
Assessment API route.
Serves the predefined assessment questionnaire.
"""

from fastapi import APIRouter
from app.core.constants import ASSESSMENT_QUESTIONS
from app.api.schemas.response import AssessmentResponse
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["Assessment"])


@router.get("/assessment/questions", response_model=AssessmentResponse)
async def get_assessment_questions():
    """Returns the predefined mental health assessment questionnaire."""
    logger.info("Assessment — Serving questionnaire questions")
    return AssessmentResponse(questions=ASSESSMENT_QUESTIONS)
