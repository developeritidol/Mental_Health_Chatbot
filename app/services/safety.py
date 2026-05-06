"""
Safety & Consensus Service
──────────────────────────
This acts as the Hybrid Consensus Synthesizer (Llama-3-8b).
It reads the raw user text AND the RoBERTa statistical emotion,
cross-validates them, and generates a structured clinical JSON
containing the logical category, true sentiment, and an active crisis flag.
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


async def synthesize_consensus(text: str, roberta_emotion: str, roberta_score: float) -> dict:
    """
    Executes the LLM Sentiment & Crisis Synthesizer (Llama-3-8B).
    Returns a structured dictionary with:
      - llm_sentiment: The verified emotional state
      - category: The dynamic conversational theme (e.g. 'burnout', 'grief')
      - is_crisis: True if active suicidal ideation or threat to life
      - reasoning: Explanation for the synthesis
    """
    client = _get_client()
    
    system_prompt = (
        "You are an expert clinical sentiment analyzer and crisis triage AI.\n"
        "Your job is to read the user's text and the raw statistical emotion provided by an NLP model, "
        "and synthesize them into a logical consensus.\n"
        "You must respond in strictly valid JSON with exactly these keys:\n"
        '{"llm_sentiment": "string", "category": "string", "is_crisis": boolean, "wants_counselor": boolean, "reasoning": "string"}\n\n'
        "RULES:\n"
        "1. Dynamic Category: Discover the category freely based on the text (e.g., 'severe_burnout', 'relationship_conflict', 'financial_stress').\n"
        "2. is_crisis MUST be exactly false unless the user explicitly mentions self-harm, wanting to die, or an active threat to life.\n"
        "3. wants_counselor MUST be true ONLY when the user explicitly names a human professional or human intermediary by role. "
        "The ONLY phrases that set it true: 'I want to talk to a real person', 'can I speak to a counselor', 'I need a therapist', "
        "'connect me to a human', 'I want professional help', 'is there a real person I can talk to', 'I want to speak to a doctor', "
        "'get me a human agent'. "
        "CRITICAL — these MUST set wants_counselor to false:\n"
        "  - User asks the AI to listen, talk, or stay with them ('could you talk for a bit', 'just listen to me', 'stay with me', 'check in on me', 'be there for me').\n"
        "  - User explicitly prefers the AI over a human ('I want to talk to you only', 'I only want to talk to you', 'I don't want a human', 'just you', 'not a real person').\n"
        "  - User vents emotions, describes struggles, or asks the AI a question — no matter how distressed they sound.\n"
        "  - User says they don't need help being fixed or referred ('I don't need you to fix anything', 'I just need someone to listen').\n"
        "If there is ANY ambiguity, default wants_counselor to false.\n"
        "4. Reasoning: Provide a brief 1-sentence explanation of why is_crisis is true or false."
    )
    
    user_prompt = f"User Text: \"{text}\"\nRaw RoBERTa Emotion: {roberta_emotion} (score: {roberta_score:.2f})"
    
    try:
        response = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=250,
        )
        
        content = response.choices[0].message.content
        result = json.loads(content)
        
        is_crisis = result.get("is_crisis", False)
        wants_counselor = bool(result.get("wants_counselor", False))

        if is_crisis:
            logger.warning(f"[SAFETY] Crisis detected! Reasoning: {result.get('reasoning')}")
        if wants_counselor:
            logger.info("[SAFETY] User explicitly requested a human counselor.")

        return {
            "llm_sentiment":    result.get("llm_sentiment", "unknown"),
            "category":         result.get("category", "general"),
            "is_crisis":        is_crisis,
            "wants_counselor":  wants_counselor,
            "reasoning":        result.get("reasoning", ""),
            "intensity":        "high" if is_crisis else "moderate",
            "message_class":    "crisis" if is_crisis else "emotional_ongoing",
            "recommended_tone": "validating",
            "token_budget":     200 if is_crisis else 320,
            "crisis_type":      result.get("category") if is_crisis else None,
        }
            
    except Exception as e:
        logger.error(f"Consensus Synthesizer failed: {e}")
        # Conservative fail-safe: unknown safety state → treat as crisis.
        # A false-positive escalation is recoverable; a false-negative during
        # an API outage is not. The counselor can assess and dismiss if needed.
        return {
            "llm_sentiment":   "unknown",
            "category":        "technical_error",
            "is_crisis":       True,
            "wants_counselor": False,
            "intensity":       "high",
            "recommended_tone": "validating",
            "message_class":   "crisis",
            "token_budget":    200,
            "reasoning":       "Safety check unavailable — escalating out of caution.",
        }