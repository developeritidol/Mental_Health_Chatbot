"""
Safety & Consensus Service — Hybrid LLM Synthesizer v3
────────────────────────────────────────────────────────
Llama-3.1-8B reads raw user text + RoBERTa emotion + recent history
and returns a single structured JSON that llm.py uses for EVERYTHING:
  - Emotional classification (category, sentiment, intensity)
  - Crisis detection (is_crisis, crisis_type)
  - Response shaping (recommended_tone, message_class, token_budget)

v3 changes (critical safety fixes):
  • message_class and token_budget now come from the 8B LLM — NOT from
    brittle Python string matching in llm.py. This eliminates the case
    where "I don't know how I can go on" is classified as advice_request,
    and "goodbye" is classified as gratitude.
  • Crisis ALWAYS overrides message_class and token_budget regardless of
    what the LLM returns — enforced in Python after the API call.
  • token_budget validated against crisis minimum (400 tokens) before return.
  • All 7 fields validated and coerced to known-good values.
"""
from __future__ import annotations
import json
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


# ── Token budgets by message class ────────────────────────────────────────────
# These are the ONLY valid values. The 8B sets the class, Python sets the budget.
# Crisis always overrides — see _apply_crisis_override() below.

MESSAGE_CLASS_BUDGETS: dict[str, int] = {
    "gratitude":         100,
    "short_casual":      150,
    "first_disclosure":  380,   # GPT-4o needs room for genuine empathy
    "positive_update":   200,
    "advice_request":    350,
    "emotional_ongoing": 320,   # enough for a warm, full response without being a wall of text
    "crisis":            700,
}

CRISIS_TOKEN_FLOOR = 800


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def synthesize_consensus(
    text: str,
    roberta_emotion: str,
    roberta_score: float,
    recent_history: str = "",
    turn_count: int = 0,
) -> dict:
    """
    Single source of truth for all message classification and emotional analysis.
    Returns a dict with ALL fields that llm.py needs — no Python string matching
    in llm.py, no secondary classification step anywhere.

    Returns:
        llm_sentiment    : verified emotional state in plain language
        category         : specific psychological category (free-form)
        intensity        : low | moderate | high | severe
        is_crisis        : bool — true only for explicit suicidal ideation / threat to life
        crisis_type      : null | suicidal_ideation | self_harm | acute_breakdown | other
        reasoning        : 1-sentence explanation
        recommended_tone : validating | grounding | gentle_challenge | crisis_support
        message_class    : gratitude | short_casual | first_disclosure | positive_update
                           | advice_request | emotional_ongoing | crisis
        token_budget     : int — resolved from message_class, crisis-overridden in Python
    """
    logger.info(f"Synthesizer v3 — analyzing: '{text[:60]}...'")
    client = _get_client()

    system_prompt = f"""You are an expert clinical sentiment analyzer and conversation turn classifier.

You receive:
1. A user's message to a mental health support chatbot
2. A statistical emotion from RoBERTa NLP model
3. Recent conversation history (if any)
4. The current turn number

Your job: return a single JSON object that classifies BOTH the emotional state
AND the conversation turn type, so the response system knows exactly how to reply.

────────────────────────────────────────────────────────────────
RESPOND ONLY IN VALID JSON. No preamble. No explanation outside JSON.
────────────────────────────────────────────────────────────────

Required keys:

{{
  "llm_sentiment": "string — verified emotional state in plain language. Examples: 'exhaustion', 'anticipatory grief', 'emotional masking', 'post-breakup identity loss', 'quiet hopelessness'",

  "category": "string — discover the psychological category FREELY from the text. Be specific, not generic. 'depression' is too generic. 'chronic loneliness with social withdrawal' is correct. Examples: 'severe_burnout', 'caregiver_fatigue', 'high_functioning_anxiety', 'post_divorce_grief', 'workplace_isolation'",

  "intensity": "string — exactly one of: low | moderate | high | severe",

  "is_crisis": "boolean — true ONLY when the message contains explicit suicidal ideation, a plan to end their life, or active self-harm. See rules below.",

  "crisis_type": "string or null — if is_crisis true: suicidal_ideation | self_harm | acute_breakdown | other. If false: null",

  "reasoning": "string — one sentence: why is_crisis is true or false, and what the core emotional situation is",

  "recommended_tone": "string — exactly one of: validating | grounding | gentle_challenge | crisis_support",

  "message_class": "string — classify THIS message using EXACTLY these rules (see below)",

  "token_budget": "integer — the budget for this message class (see table below)"
}}

────────────────────────────────────────────────────────────────
MESSAGE CLASS RULES — read every rule before classifying:
────────────────────────────────────────────────────────────────

"gratitude"
  → User is clearly wrapping up, expressing thanks, or reporting they feel better.
  → Examples: "thanks so much", "that really helped", "i feel much better now", "i'm good now"
  → ⚠️ WARNING: Do NOT classify as gratitude if message contains farewell with distress.
     "goodbye, nobody will miss me" = emotional_ongoing or crisis, NOT gratitude.
     "bye, feeling better now" = gratitude. The difference is presence of distress.
  → token_budget: 80

"short_casual"
  → Very short message (1–5 words) with no emotional content detected.
  → Examples: "ok", "hey", "what do you mean", "i see"
  → Do NOT use this if the short message contains emotional signal.
     "i'm done" is NOT short_casual — it is emotional_ongoing or crisis.
  → token_budget: 100

"first_disclosure"
  → Turn 0–2 AND user is sharing an emotional problem, life event, or difficult situation for the first time.
  → Current turn is: {turn_count}
  → ⚠️ CRITICAL: Statements about life events ARE first_disclosure, NOT advice_request.
     "I lost the job" = first_disclosure ✓ (sharing a painful event, NOT asking for advice)
     "I lost my job, what should I do?" = advice_request ✓ (explicitly asking)
     "my relationship ended" = first_disclosure ✓
     "I failed my exam" = first_disclosure ✓
     "I feel lonely" = first_disclosure ✓
  → The test: is the user SHARING something that just happened or how they feel?
    If yes → first_disclosure. They are not asking for help yet. They are telling you what happened.
  → token_budget: 150

"positive_update"
  → User is sharing that something they tried worked, or that they feel better
    as a RESULT of a specific action they took.
  → Examples: "i talked to someone today", "i tried what you said and it worked",
    "i made a friend at work", "went outside and felt a bit better"
  → token_budget: 130

"advice_request"
  → User is EXPLICITLY and clearly asking for guidance, how-to, or what to do.
  → ⚠️ CRITICAL: Only classify as advice_request if the user uses a QUESTION or explicit request.
     "how can I make friends" = advice_request ✓ (question)
     "what should I do about my job" = advice_request ✓ (question + "what should")
     "I lost the job" = first_disclosure ✗ NOT advice_request (statement, not question)
     "I don't know how I can go on like this" = emotional_ongoing ✗ (NOT advice_request)
     "I don't know what to do anymore" = emotional_ongoing ✗ (NOT advice_request)
  → The ONLY test: does the message contain a question mark OR the words "should I", "how do I",
    "what should", "how can I", "give me advice", "what do I do"?
    If NO question or explicit request → it is NOT advice_request. Use first_disclosure or emotional_ongoing.
  → token_budget: 180

"emotional_ongoing"
  → Default class for any message that is emotional in nature and doesn't fit above.
  → When in doubt, use this. It is always safer than advice_request or gratitude.
  → token_budget: 200

"crisis"
  → is_crisis is true.
  → Always set message_class to "crisis" when is_crisis is true.
  → token_budget: 500

────────────────────────────────────────────────────────────────
IS_CRISIS RULES — THE MOST IMPORTANT SECTION. READ EVERY WORD.
────────────────────────────────────────────────────────────────

⚠️ CRITICAL RULE: Evaluate is_crisis ONLY from the CURRENT USER MESSAGE.
Do NOT use the conversation history to escalate crisis classification.
A person can be in severe emotional distress (high intensity) WITHOUT being in crisis.
Emotional pain ≠ Crisis. Financial stress ≠ Crisis. Job loss ≠ Crisis. Humiliation ≠ Crisis.

is_crisis = TRUE ONLY when the CURRENT message contains ONE of these:
  • Explicit suicidal ideation with intent: "I want to kill myself", "I want to die",
    "I am going to end my life", "I don't want to be here anymore"
  • A specific plan: "I have pills ready", "I bought a rope", "I know exactly how I'll do it"
  • Active self-harm happening now: "I am cutting myself right now", "I hurt myself tonight"
  • A farewell with explicit stated intent: "this is my goodbye, I won't be here tomorrow"

is_crisis = FALSE for ALL of these — no exceptions, regardless of context:
  • Job loss, being fired, career failure → NOT crisis. High intensity emotional_ongoing.
  • Financial stress, EMI pressure, debt → NOT crisis. High intensity emotional_ongoing.
  • Being told "you are useless", workplace humiliation → NOT crisis. emotional_ongoing.
  • "I don't know what to do" → NOT crisis. emotional_ongoing or advice_request.
  • "what should I do now to earn money?" → NOT crisis. This is advice_request.
  • "I feel like a failure" → NOT crisis. emotional_ongoing.
  • "I can't go on like this" → NOT crisis. High intensity emotional_ongoing.
  • "I wish I could disappear" → NOT crisis. Passive ideation = emotional_ongoing.
  • "I want to give up" → NOT crisis. Emotional exhaustion = emotional_ongoing.
  • "things are overwhelming" → NOT crisis. emotional_ongoing.
  • "I have no money and bills due" → NOT crisis. Financial stress = emotional_ongoing.
  • "I want to kill my boss" → NOT crisis. Colloquial frustration.
  • Any question asking for practical help or advice → NEVER crisis.

THE TEST: Does the CURRENT message contain the words "kill myself", "end my life",
"want to die", "hurt myself", or describe an active plan/means? If NO → is_crisis = false.
If the emotional context is severe but the current message lacks explicit crisis language → is_crisis = false.
High intensity emotional pain is NOT the same as crisis. Treat them differently.

────────────────────────────────────────────────────────────────
TONE GUIDE:
────────────────────────────────────────────────────────────────
  validating       → user needs to feel heard (most emotional disclosures)
  grounding        → user is spiraling, needs anchoring in present moment
  gentle_challenge → user stuck in negative belief loop, ready for gentle reframe
  crisis_support   → is_crisis is true — always use this when is_crisis is true
"""

    user_prompt = (
        f'User message: "{text}"\n'
        f"RoBERTa statistical emotion: {roberta_emotion} (confidence: {roberta_score:.2f})\n"
        f"Current turn: {turn_count}\n"
    )
    if recent_history:
        user_prompt += f"\nRecent conversation:\n{recent_history}"

    try:
        response = await client.chat.completions.create(
            model=settings.SYNTHESIZER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=350,
        )

        content = response.choices[0].message.content
        result  = json.loads(content)

        # ── Validate and coerce all fields ────────────────────────────────────
        is_crisis   = bool(result.get("is_crisis", False))
        intensity   = result.get("intensity", "moderate")
        rec_tone    = result.get("recommended_tone", "validating")
        crisis_type = result.get("crisis_type", None)
        msg_class   = result.get("message_class", "emotional_ongoing")
        token_budget = result.get("token_budget", 200)

        # Coerce enums to valid values
        if intensity not in ("low", "moderate", "high", "severe"):
            intensity = "moderate"
        if rec_tone not in ("validating", "grounding", "gentle_challenge", "crisis_support"):
            rec_tone = "validating"
        if msg_class not in MESSAGE_CLASS_BUDGETS:
            msg_class = "emotional_ongoing"

        # Resolve token_budget from our authoritative table — never trust LLM integer
        token_budget = MESSAGE_CLASS_BUDGETS.get(msg_class, 200)

        # ── Crisis always wins — Python enforces this, never the LLM ─────────
        result = _apply_crisis_override(
            result=result,
            is_crisis=is_crisis,
            crisis_type=crisis_type,
            intensity=intensity,
            rec_tone=rec_tone,
            msg_class=msg_class,
            token_budget=token_budget,
        )

        if result["is_crisis"]:
            logger.warning(
                f"[SAFETY ALERT] Crisis detected! "
                f"Type: {result['crisis_type']} | "
                f"Class: {result['message_class']} | "
                f"Tokens: {result['token_budget']} | "
                f"Reason: {result['reasoning']}"
            )
        else:
            logger.info(
                f"Consensus OK — class: {result['message_class']} | "
                f"category: {result['category']} | "
                f"intensity: {result['intensity']} | "
                f"tone: {result['recommended_tone']} | "
                f"tokens: {result['token_budget']}"
            )

        return result

    except json.JSONDecodeError as e:
        logger.error(f"Synthesizer — JSON parse failed: {e}")
        return _fallback_consensus(roberta_emotion, turn_count)

    except Exception as e:
        logger.error(f"Synthesizer — API error: {e}")
        return _fallback_consensus(roberta_emotion, turn_count)


def _apply_crisis_override(
    result: dict,
    is_crisis: bool,
    crisis_type,
    intensity: str,
    rec_tone: str,
    msg_class: str,
    token_budget: int,
) -> dict:
    """
    Python-level safety enforcement.
    Crisis overrides are NEVER left to the LLM to get right.
    If is_crisis is True, this function guarantees:
      - message_class = "crisis"
      - token_budget  >= CRISIS_TOKEN_FLOOR
      - recommended_tone = "crisis_support"
      - crisis_type is a valid non-null value
    """
    if is_crisis:
        if crisis_type not in ("suicidal_ideation", "self_harm", "acute_breakdown", "other"):
            crisis_type = "other"
        return {
            "llm_sentiment":    result.get("llm_sentiment", "distress"),
            "category":         result.get("category", "crisis"),
            "intensity":        "severe",           # crisis is always severe
            "is_crisis":        True,
            "crisis_type":      crisis_type,
            "reasoning":        result.get("reasoning", "Crisis detected."),
            "recommended_tone": "crisis_support",   # always override on crisis
            "message_class":    "crisis",           # always override on crisis
            "token_budget":     CRISIS_TOKEN_FLOOR, # always full budget on crisis
        }

    # Non-crisis: return cleaned validated result
    return {
        "llm_sentiment":    result.get("llm_sentiment", "unknown"),
        "category":         result.get("category", "general"),
        "intensity":        intensity,
        "is_crisis":        False,
        "crisis_type":      None,
        "reasoning":        result.get("reasoning", ""),
        "recommended_tone": rec_tone,
        "message_class":    msg_class,
        "token_budget":     token_budget,
    }


def _fallback_consensus(roberta_emotion: str = "neutral", turn_count: int = 0) -> dict:
    """
    Safe fallback when synthesizer fails completely.
    Always conservative — uses emotional_ongoing class and validating tone.
    Never returns is_crisis=True in fallback (cannot confirm without LLM).
    """
    high_intensity = {"grief", "despair", "fear", "anger", "disgust", "sadness"}
    intensity      = "high" if roberta_emotion in high_intensity else "moderate"
    msg_class      = "first_disclosure" if turn_count <= 2 else "emotional_ongoing"

    return {
        "llm_sentiment":    roberta_emotion,
        "category":         "general_distress",
        "intensity":        intensity,
        "is_crisis":        False,
        "crisis_type":      None,
        "reasoning":        "Fallback — synthesizer unavailable. Using RoBERTa emotion directly.",
        "recommended_tone": "validating",
        "message_class":    msg_class,
        "token_budget":     MESSAGE_CLASS_BUDGETS[msg_class],
    }