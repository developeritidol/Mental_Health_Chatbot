"""
Emotion Analysis Service
─────────────────────────
Uses SamLowe/roberta-base-go_emotions (28 labels) loaded locally via
HuggingFace Transformers. The pipeline is lazy-loaded once on first use
and cached — no cold-start penalty after the first call.

Why this model over j-hartmann (7 labels)?
  • Includes: hopelessness, grief, remorse, nervousness — critical for mental health
  • 28 fine-grained labels allow much richer response-mode selection
  • Still fast: ~80–100 ms inference on CPU after warmup
"""

from __future__ import annotations
import asyncio
from functools import lru_cache
from typing import Optional

from app.core.config import get_settings
from app.core.constants import EMOTION_MODE_MAP, RESPONSE_MODE_INSTRUCTIONS, CRISIS_SIGNAL_PHRASES
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# ── Lazy pipeline holder ──────────────────────────────────────────────────────
_pipeline = None


def _load_pipeline():
    """Load the HF pipeline once and cache it."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    try:
        from transformers import pipeline as hf_pipeline
        logger.info(f"Loading emotion model: {settings.HF_EMOTION_MODEL}")
        _pipeline = hf_pipeline(
            task="text-classification",
            model=settings.HF_EMOTION_MODEL,
            top_k=5,               # return top-5 labels with scores
            truncation=True,
            max_length=512,
        )
        logger.info("Emotion model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load emotion model: {e}")
        _pipeline = None
    return _pipeline


# ── Public API ────────────────────────────────────────────────────────────────

class EmotionResult:
    def __init__(
        self,
        dominant: str,
        scores: dict[str, float],
        mode: str,
        mode_instruction: str,
        is_crisis_signal: bool,
    ):
        self.dominant = dominant
        self.scores = scores
        self.mode = mode
        self.mode_instruction = mode_instruction
        self.is_crisis_signal = is_crisis_signal

    def to_dict(self) -> dict:
        return {
            "dominant_emotion": self.dominant,
            "top_scores": self.scores,
            "response_mode": self.mode,
            "is_crisis_signal": self.is_crisis_signal,
        }


async def analyse(text: str) -> EmotionResult:
    """
    Analyse `text` and return an EmotionResult.
    Falls back to neutral if the model is unavailable.
    """
    # Run in thread pool — transformers inference is CPU-blocking
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_inference, text)
    return result


def _run_inference(text: str) -> EmotionResult:
    # ── Crisis keyword check (fast, no model needed) ──────────────────────────
    text_lower = text.lower()
    crisis_signal = any(phrase in text_lower for phrase in CRISIS_SIGNAL_PHRASES)

    # ── Model inference ───────────────────────────────────────────────────────
    pipe = _load_pipeline()
    if pipe is None:
        return _fallback(crisis_signal)

    try:
        raw = pipe(text)
        # raw is a list of lists when top_k > 1
        items = raw[0] if isinstance(raw[0], list) else raw
        scores = {item["label"].lower(): round(item["score"], 4) for item in items}
        dominant = max(scores, key=scores.get)
    except Exception as e:
        logger.warning(f"Emotion inference error: {e}")
        return _fallback(crisis_signal)

    # ── Map to response mode ──────────────────────────────────────────────────
    mode = EMOTION_MODE_MAP.get(dominant, "curious_exploration")
    instruction = RESPONSE_MODE_INSTRUCTIONS.get(mode, "")

    logger.debug(f"Emotion: {dominant} ({scores.get(dominant):.3f}) → mode={mode}")

    return EmotionResult(
        dominant=dominant,
        scores=scores,
        mode=mode,
        mode_instruction=instruction,
        is_crisis_signal=crisis_signal,
    )


def _fallback(crisis_signal: bool) -> EmotionResult:
    mode = "gentle_watchful_presence" if crisis_signal else "curious_exploration"
    return EmotionResult(
        dominant="neutral",
        scores={"neutral": 1.0},
        mode=mode,
        mode_instruction=RESPONSE_MODE_INSTRUCTIONS[mode],
        is_crisis_signal=crisis_signal,
    )


# Pre-warm the model at import time (runs in background on startup)
def warmup():
    try:
        _load_pipeline()
        if _pipeline:
            _pipeline("I feel okay today")
            logger.info("Emotion model warm-up complete.")
    except Exception as e:
        logger.warning(f"Model warm-up skipped: {e}")