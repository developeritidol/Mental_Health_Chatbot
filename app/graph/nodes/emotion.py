"""
Emotion Analysis Node (LangGraph).
Uses j-hartmann/emotion-english-distilroberta-base to detect emotions locally.
"""

from transformers import pipeline
from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)

# ---- Load model once at module level (singleton) ----
_emotion_pipeline = None


def _get_pipeline():
    """Lazily loads the HuggingFace emotion pipeline."""
    global _emotion_pipeline
    if _emotion_pipeline is None:
        settings = get_settings()
        logger.info(f"Loading emotion model: {settings.SENTIMENT_MODEL}")
        _emotion_pipeline = pipeline(
            "text-classification",
            model=settings.SENTIMENT_MODEL,
            top_k=None,  # Return all emotion scores
            truncation=True,
        )
        logger.info("Emotion model loaded successfully.")
    return _emotion_pipeline


def emotion_node(state: dict) -> dict:
    """
    LangGraph node: Analyzes the user's message for emotions.
    
    Populates:
        - current_emotion: dict of all emotion scores
        - top_emotion_label: highest scoring emotion
        - top_emotion_score: confidence of top emotion
    """
    user_message = state["user_message"]
    logger.info(f"Emotion Node — Analyzing: '{user_message[:80]}...'")

    pipe = _get_pipeline()
    results = pipe(user_message)[0]  # List of {label, score} dicts

    # Build full emotion map and find top emotion
    emotion_map = {r["label"]: round(r["score"], 4) for r in results}
    top = max(results, key=lambda x: x["score"])

    logger.info(f"Emotion Node — Top emotion: {top['label']} ({top['score']:.4f})")
    logger.debug(f"Emotion Node — Full scores: {emotion_map}")

    return {
        "current_emotion": emotion_map,
        "top_emotion_label": top["label"],
        "top_emotion_score": round(top["score"], 4),
    }
