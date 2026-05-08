"""
Safety & Consensus Service
──────────────────────────
This acts as the Hybrid Consensus Synthesizer (GPT-4o-mini).
It reads the raw user text AND the RoBERTa statistical emotion,
cross-validates them, and generates a structured clinical JSON
containing the logical category, true sentiment, and an active crisis flag.
"""
from __future__ import annotations
import json
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


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
        "RULES:\n\n"

        "1. CATEGORY: Identify the emotional theme freely (e.g. 'severe_burnout', 'relationship_conflict', "
        "'financial_stress', 'grief', 'anxiety', 'loneliness').\n\n"

        "2. IS_CRISIS — set true ONLY when the user makes a DIRECT, POSITIVE statement of intent to harm "
        "themselves or end their life RIGHT NOW.\n"
        "   TRUE (is_crisis=true):\n"
        "     - 'I want to kill myself'\n"
        "     - 'I am going to end my life'\n"
        "     - 'I have a plan to suicide'\n"
        "     - 'I want to hurt myself'\n"
        "     - 'I've been cutting myself'\n\n"
        "   FALSE — NEVER set is_crisis=true for these even though they mention death, pain, or ending:\n"
        "     NEGATIONS (desire to live): 'I don't want to die', 'I dont want to die', "
        "'I want to live', 'I want to keep living', 'I'm not suicidal', 'im not suicidal', "
        "'I'm not thinking of hurting myself', 'I don't want to hurt myself'\n"
        "     FEAR (not intent): 'I'm scared of dying', 'I'm afraid of death', 'afraid of dying', "
        "'scared of death', 'fear of death'\n"
        "     IDIOMS/METAPHORS: 'this is killing me', 'I could kill for...', "
        "'I'm dying of embarrassment', 'I'm dying of laughter'\n"
        "     VAGUE DISTRESS (no self-harm intent): 'I feel like dying', 'I feel dead inside', "
        "'I want to disappear', 'I want to escape', 'I want to run away', "
        "'I want to end this pain', 'I want to end this suffering', "
        "'I can't take this anymore', 'I'm exhausted of living like this'\n"
        "     PAST TENSE / HISTORICAL: 'I used to think about suicide' (past, not present plan)\n\n"
        "   AMBIGUOUS RULE: If the text contains both a crisis phrase AND a negation/fear qualifier, "
        "the negation wins — keep is_crisis=false.\n"
        "   DEFAULT: When in doubt, set is_crisis=false. A counselor can always escalate manually; "
        "a false alarm that disconnects a user from the AI every conversation is harmful.\n\n"

        "3. WANTS_COUNSELOR — set true ONLY when the user explicitly requests a named human role.\n"
        "   TRUE (wants_counselor=true):\n"
        "     - 'I want to talk to a real person / human'\n"
        "     - 'Can I speak to a counselor / therapist / doctor?'\n"
        "     - 'Connect me to a human'\n"
        "     - 'I want professional help'\n"
        "     - 'Is there a real person I can talk to?'\n"
        "     - 'Get me a human agent'\n\n"
        "   FALSE — NEVER set wants_counselor=true for:\n"
        "     TALKING TO AI: 'just listen to me', 'stay with me', 'be there for me', "
        "'talk for a bit', 'check in on me', 'could you talk with me'\n"
        "     AI PREFERENCE: 'I only want to talk to you', 'I want to talk to you only', "
        "'I don't want a human', 'not a real person', 'just you'\n"
        "     VAGUE REQUESTS: 'I need help', 'can someone help me', "
        "'I want to talk to someone' (someone ≠ human professional)\n"
        "     VENTING / ASKING AI: user describes struggles or asks the AI a question — "
        "no matter how distressed they sound\n"
        "     NO FIX NEEDED: 'I just need someone to listen', 'I don't need you to fix anything'\n\n"
        "   DEFAULT: Any ambiguity → wants_counselor=false.\n\n"

        "4. REASONING: One sentence explaining the is_crisis decision."
    )
    
    user_prompt = f"User Text: \"{text}\"\nRaw RoBERTa Emotion: {roberta_emotion} (score: {roberta_score:.2f})"
    
    try:
        response = await client.chat.completions.create(
            model=settings.SYNTHESIZER_MODEL,
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