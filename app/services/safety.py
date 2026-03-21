"""
Safety & Consensus Service — Hybrid LLM Synthesizer
─────────────────────────────────────────────────────
Llama-3.1-8B reads the raw user text + RoBERTa statistical emotion,
cross-validates them, and returns a structured clinical JSON.

v2 changes:
  • Added `intensity` field  (low / moderate / high / severe)
  • Added `recommended_tone` field (validating / grounding / gentle_challenge / crisis_support)
  • Added `crisis_type` field (null / suicidal_ideation / self_harm / acute_breakdown / other)
  • Expanded fallback to include all new fields so llm.py never gets KeyError
"""
from __future__ import annotations
import json
from groq import AsyncGroq
from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _get_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


async def synthesize_consensus(
    text: str,
    roberta_emotion: str,
    roberta_score: float,
    recent_history: str = "",
) -> dict:
    """
    Executes the LLM Sentiment & Crisis Synthesizer (Llama-3.1-8B on Groq).

    Returns a structured dict with ALL fields llm.py expects:
      - llm_sentiment   : verified emotional state in plain language
      - category        : dynamic psychological category (free-form, no predefined list)
      - intensity       : low | moderate | high | severe
      - is_crisis       : True only if active suicidal ideation / threat to life
      - crisis_type     : null | suicidal_ideation | self_harm | acute_breakdown | other
      - reasoning       : 1-sentence explanation
      - recommended_tone: validating | grounding | gentle_challenge | crisis_support
    """
    logger.info(f"Synthesizer — analyzing: '{text[:60]}...'")
    client = _get_client()

    system_prompt = """You are an expert clinical sentiment analyzer and crisis triage AI.
You read the user's message and the raw statistical emotion from an NLP model,
then synthesize them into a structured psychological assessment.

Respond ONLY in strictly valid JSON. No preamble. No explanation outside the JSON.
Use exactly these keys:

{
  "llm_sentiment": "string — the verified emotional state in plain human language (e.g. 'exhaustion', 'anticipatory grief', 'emotional masking')",
  "category": "string — discover the psychological category FREELY from the text. Do not use a predefined list. Examples: 'severe_burnout', 'post_breakup_identity_loss', 'caregiver_fatigue', 'chronic_loneliness', 'high_functioning_anxiety'",
  "intensity": "string — exactly one of: low | moderate | high | severe",
  "is_crisis": "boolean — true ONLY if the user explicitly mentions suicidal thoughts, wanting to die, self-harm, or an active threat to their life. False for everything else.",
  "crisis_type": "string or null — if is_crisis is true, classify as: suicidal_ideation | self_harm | acute_breakdown | other. If is_crisis is false, use null.",
  "reasoning": "string — one sentence explaining why is_crisis is true or false, and what the dominant psychological theme is",
  "recommended_tone": "string — exactly one of: validating | grounding | gentle_challenge | crisis_support"
}

TONE GUIDE:
- validating      : user needs to feel heard, not advised (most emotional disclosures)
- grounding       : user is spiraling, needs calm anchoring in the present moment
- gentle_challenge: user is stuck in a negative belief loop, ready for a gentle reframe
- crisis_support  : is_crisis is true — stay present, do not give advice

INTENSITY GUIDE:
- low    : mild emotional discomfort, curious or reflective mood
- moderate: noticeable distress but functioning
- high   : significant distress, struggling to cope
- severe : overwhelming distress, possible crisis indicators

CRITICAL RULES:
1. is_crisis MUST be false unless suicidal ideation or active self-harm is explicit.
2. "I want to disappear" or "I can't do this anymore" = high intensity, is_crisis = false (passive ideation only becomes is_crisis = true if there is explicit intent or plan)
3. "I want to kill myself" or "I have a plan to end my life" = is_crisis = true
4. The category must be specific to THIS message — not generic. "depression" is too generic. "post-divorce identity collapse" is correct."""

    user_prompt = (
        f'User message: "{text}"\n'
        f"RoBERTa statistical emotion: {roberta_emotion} (confidence: {roberta_score:.2f})\n"
    )
    if recent_history:
        user_prompt += f"Recent conversation context:\n{recent_history}"

    try:
        response = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,      # low — we want consistent structured output
            max_tokens=300,
        )

        content = response.choices[0].message.content
        result  = json.loads(content)

        # Validate and normalize all fields — never trust LLM output blindly
        intensity      = result.get("intensity", "moderate")
        rec_tone       = result.get("recommended_tone", "validating")
        crisis_type    = result.get("crisis_type", None)
        is_crisis      = bool(result.get("is_crisis", False))

        # Coerce to valid enum values if LLM drifted
        if intensity not in ("low", "moderate", "high", "severe"):
            intensity = "moderate"
        if rec_tone not in ("validating", "grounding", "gentle_challenge", "crisis_support"):
            rec_tone = "validating"
        if is_crisis and rec_tone != "crisis_support":
            rec_tone = "crisis_support"   # always override tone on crisis
        if is_crisis and crisis_type not in ("suicidal_ideation", "self_harm", "acute_breakdown", "other"):
            crisis_type = "other"
        if not is_crisis:
            crisis_type = None

        if is_crisis:
            logger.warning(
                f"[SAFETY ALERT] Crisis detected! "
                f"Type: {crisis_type} | Reasoning: {result.get('reasoning', '')}"
            )
        else:
            logger.info(
                f"Consensus OK — category: {result.get('category')} | "
                f"intensity: {intensity} | tone: {rec_tone}"
            )

        return {
            "llm_sentiment":    result.get("llm_sentiment", "unknown"),
            "category":         result.get("category", "general"),
            "intensity":        intensity,
            "is_crisis":        is_crisis,
            "crisis_type":      crisis_type,
            "reasoning":        result.get("reasoning", ""),
            "recommended_tone": rec_tone,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Synthesizer — JSON parse failed: {e}")
        return _fallback_consensus(roberta_emotion)

    except Exception as e:
        logger.error(f"Synthesizer — API error: {e}")
        return _fallback_consensus(roberta_emotion)


def _fallback_consensus(roberta_emotion: str = "neutral") -> dict:
    """
    Safe fallback returned when synthesizer fails.
    Maps RoBERTa emotion to a reasonable intensity so the LLM
    still gets a useful signal even in degraded mode.
    """
    high_intensity_emotions = {"grief", "despair", "fear", "anger", "disgust", "sadness"}
    intensity = "high" if roberta_emotion in high_intensity_emotions else "moderate"

    return {
        "llm_sentiment":    roberta_emotion,
        "category":         "general_distress",
        "intensity":        intensity,
        "is_crisis":        False,
        "crisis_type":      None,
        "reasoning":        "Fallback — synthesizer unavailable. Using RoBERTa emotion directly.",
        "recommended_tone": "validating",
    }