"""
Safety Guardrail Node (LangGraph).
Pre-screens user input for crisis keywords and determines risk level.
Uses fuzzy matching to catch misspellings and partial phrases.
"""

import re
from difflib import SequenceMatcher
from app.core.constants import HIGH_RISK_KEYWORDS, CRISIS_RESOURCES, SAFE_FALLBACK_RESPONSE
from app.core.logger import get_logger

logger = get_logger(__name__)

# Core crisis stems — catch misspellings via fuzzy matching
CRISIS_STEMS = [
    "suicide", "suicidal", "suicude", "sucide", "suiside",
    "kill myself", "kill me", "end my life", "end it", "end this",
    "want to die", "wanna die", "gonna die", "going to die",
    "hurt myself", "harm myself", "cut myself", "cutting myself",
    "overdose", "hang myself", "jump off", "slit",
    "no reason to live", "not worth living", "better off dead",
    "can't go on", "cant go on", "don't want to be here",
    "dont want to exist", "don't want to exist",
    "take my life", "take my own life",
    "wish i was dead", "wish i were dead",
    "want it to stop", "make it stop",
    "nothing matters", "no point anymore", "no hope",
    "nobody cares", "no one cares",
]


def _fuzzy_keyword_match(text: str, keywords: list, threshold: float = 0.75) -> bool:
    """
    Checks if any keyword is present in the text via:
    1. Direct substring match.
    2. Fuzzy similarity match (catches misspellings).
    """
    text_lower = text.lower().strip()

    # -- Pass 1: Direct substring --
    for kw in keywords:
        if kw in text_lower:
            logger.warning(f"Guardrail — Direct keyword match: '{kw}'")
            return True

    # -- Pass 2: Fuzzy match on each word-window --
    words = text_lower.split()
    for kw in keywords:
        kw_word_count = len(kw.split())
        for i in range(len(words) - kw_word_count + 1):
            window = " ".join(words[i:i + kw_word_count])
            similarity = SequenceMatcher(None, window, kw).ratio()
            if similarity >= threshold:
                logger.warning(
                    f"Guardrail — Fuzzy keyword match: '{window}' ≈ '{kw}' "
                    f"(similarity: {similarity:.2f})"
                )
                return True

    return False


def guardrail_node(state: dict) -> dict:
    """
    LangGraph node: Pre-screens the user message for crisis indicators.

    Uses THREE layers of detection:
        1. Direct + Fuzzy keyword matching (catches misspellings like 'sucide')
        2. Pattern-based detection (regex for common crisis phrases)
        3. Emotion-based risk escalation

    Populates:
        - risk_level
        - is_crisis
        - crisis_response (only if is_crisis is True)
    """
    user_message = state["user_message"]
    user_message_lower = user_message.lower().strip()
    top_emotion = state.get("top_emotion_label", "neutral")
    top_score = state.get("top_emotion_score", 0.0)

    logger.info("Guardrail Node — Scanning for crisis indicators...")

    # -- Layer 1: Direct + Fuzzy keyword matching --
    all_keywords = list(set(HIGH_RISK_KEYWORDS + CRISIS_STEMS))
    if _fuzzy_keyword_match(user_message, all_keywords, threshold=0.75):
        logger.warning(f"Guardrail Node — HIGH RISK detected: '{user_message_lower[:80]}'")
        return {
            "risk_level": "HIGH",
            "is_crisis": False,  # Don't bypass LLM — let it respond empathetically WITH crisis context
            "crisis_response": None,
        }

    # -- Layer 2: Regex pattern matching for subtle crisis phrases --
    crisis_patterns = [
        r"\b(i\s+want\s+to\s+(end|finish|stop)\s+(it|this|everything))",
        r"\b(no\s+point|no\s+hope|no\s+purpose)",
        r"\b(can'?t\s+(take|handle|do)\s+(it|this)\s+(anymore|any\s+more))",
        r"\b(i'?m\s+done|i\s+give\s+up|giving\s+up)",
        r"\b(don'?t\s+want\s+to\s+(be\s+here|live|exist|wake\s+up))",
        r"\b(wish\s+i\s+(was|were|could\s+be)\s+dead)",
        r"\b(tired\s+of\s+(living|being\s+alive|everything))",
    ]
    for pattern in crisis_patterns:
        if re.search(pattern, user_message_lower):
            logger.warning(f"Guardrail Node — HIGH RISK regex pattern matched in: '{user_message_lower[:80]}'")
            return {
                "risk_level": "HIGH",
                "is_crisis": False,
                "crisis_response": None,
            }

    # -- Layer 3: Emotion-based risk escalation --
    high_risk_emotions = {"sadness", "fear", "anger", "disgust"}
    if top_emotion in high_risk_emotions and top_score >= 0.70:
        logger.warning(
            f"Guardrail Node — MEDIUM risk from emotion: {top_emotion} ({top_score:.2f})"
        )
        return {
            "risk_level": "MEDIUM",
            "is_crisis": False,
            "crisis_response": None,
        }

    # -- Default: Low risk --
    logger.info("Guardrail Node — No immediate risk detected. Risk level: LOW")
    return {
        "risk_level": "LOW",
        "is_crisis": False,
        "crisis_response": None,
    }
