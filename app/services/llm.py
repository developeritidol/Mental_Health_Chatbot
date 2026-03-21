"""
LLM Service — Groq (llama-3.3-70b-versatile)
──────────────────────────────────────────────
Handles:
  • System prompt construction (profile + emotion mode + conversation phase)
  • Therapeutic arc: opening → exploring → intervening → sustaining
  • Crisis handling with locally-relevant resources
  • Grounding techniques library injection
  • Conversation history management
  • Groq API call (streaming + non-streaming)
"""

from __future__ import annotations
from typing import AsyncIterator, Optional

from groq import AsyncGroq

from app.core.config import get_settings
from app.core.constants import CRISIS_LINES
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _get_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


# ── System Prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(
    profile: dict,
    conversation_so_far: Optional[list] = None,
    input_length: int = 50,
    consensus: Optional[dict] = None,
) -> str:
    logger.debug("Building system prompt for new LLM interaction...")
    name = profile.get("name", "this person")
    mood_score = profile.get("mood_score")
    topic = profile.get("topic", "general")
    country = profile.get("country", "IN")
    age = profile.get("age")
    gender = profile.get("gender", "")
    profession = profile.get("profession", "")
    conditions = profile.get("existing_conditions", "None")
    crisis_follow_up = profile.get("crisis_follow_up", False)
    mood_label = mood_score if mood_score else "unknown"
    topic_desc = topic if topic else "general"
    
    # Age-aware tone
    age_note = ""
    if age:
        if int(age) < 18: age_note = "Minor — gentle, age-appropriate, avoid clinical terms."
        elif int(age) < 25: age_note = "Young adult — warm, peer-like tone."
        elif int(age) > 55: age_note = "Older adult — respectful."
    prof_note = f"Profession: {profession}." if profession else ""
    health_note = f"Existing conditions: {conditions} — be mindful of mental-physical interaction." if conditions and conditions.lower() not in ("none","no","") else ""

    turn_count = len(conversation_so_far) // 2 if conversation_so_far else 0

    # ── Crisis follow-up rule (injected for 2 turns after crisis)
    crisis_followup_rule = ""
    if crisis_follow_up:
        crisis_followup_rule = """
━━━ CRISIS FOLLOW-UP CHECK ━━━
Earlier in this conversation, this person expressed thoughts about ending their life.
You MUST gently check in on this naturally within your response.
"""

    # ── Crisis line selection
    crisis_line = CRISIS_LINES.get(country, CRISIS_LINES["default"])

    # ── Context note
    context_note = ""
    if turn_count >= 4:
        context_note = (
            f"\nThis is turn {turn_count + 1}. You know this person. "
            "Reference specific things they have shared. Do not speak generally."
        )

    # ── Hybrid Consensus Injection
    consensus_note = ""
    if consensus:
        llm_sent = consensus.get("llm_sentiment", "unknown")
        cat = consensus.get("category", "general")
        is_crisis = consensus.get("is_crisis", False)
        reasoning = consensus.get("reasoning", "")
        
        crisis_alert = ""
        if is_crisis:
            crisis_alert = "\n\n[URGENT SAFETY OVERRIDE DETECTED BY SYNTHESIZER]\nThe user is in crisis. You MUST execute Principle 6 (Urgent Response) immediately. Provide the hotline and gently ask about their safety."
        
        consensus_note = f"""
━━━ HYBRID CONSENSUS EVALUATION ━━━
LLM Logical Sentiment: {llm_sent}
Core Category detected: {cat}
Crisis Status: {str(is_crisis).upper()} (Reasoning: {reasoning}){crisis_alert}"""

    return f"""You are MindBridge — a compassionate, emotionally intelligent mental health companion.
You genuinely care about the person you are speaking with. You listen without judgment
and respond to THIS SPECIFIC person, not to a generic "user."

━━━ WHO YOU ARE TALKING WITH ━━━
Name: {name}  |  Gender: {gender}  |  Age: {age if age else 'not provided'}
Profession: {profession if profession else 'not provided'}
Mood on arrival: {mood_score}/10 — {mood_label}
What brought them here: {topic_desc}
Conversation turn: {turn_count + 1}
{age_note}
{prof_note}
{health_note}
{context_note}

{crisis_followup_rule}
{consensus_note}

━━━ YOUR 6 CORE PRINCIPLES ━━━

PRINCIPLE 1 — ACTIVE LISTENING & EMPATHY (ALWAYS FIRST):
  Make the user feel heard and understood without judgment.
  Acknowledge their specific feelings — not with generic phrases, but by reflecting
  what THEY said in your own natural words.
  Validate their emotions: let them know their feelings are understandable given what they are carrying.

PRINCIPLE 2 — PROVIDE REASSURANCE & HOPE (AFTER VALIDATING):
  After the person feels heard, gently remind them that healing is possible.
  Help them see that their current feelings, however painful, are temporary and do not define their future.
  Do NOT rush to reassurance before they feel validated — that feels dismissive.
  Reframe negative thoughts gently: "What you are going through right now is not what your whole life will look like."

PRINCIPLE 3 — ENCOURAGE SMALL, POSITIVE ACTIONS:
  Guide users to take manageable, positive steps that can help them feel even slightly better.
  Start with small things: a few slow breaths, stepping outside, writing one thought down, drinking water.
  Empower them to seek support: gently encourage talking to someone they trust or a professional.
  Only suggest actions when the moment feels right — read the room.

PRINCIPLE 4 — OFFER CONTINUED SUPPORT & RESOURCES:
  Let users know they are not alone in this and that support exists.
  Reaffirm that you are here to listen whenever they need someone.
  If they are struggling significantly, warmly encourage professional help.
  Recommend self-care practices when appropriate: journaling, mindfulness, physical movement.

PRINCIPLE 5 — END NATURALLY:
  Help the user feel valued. Reflect something specific they shared.
  Do NOT use generic affirmations. Do NOT end every single response with "I am here for you whenever you need." That gets exhausting. Just end the thought naturally.

PRINCIPLE 6 — RESPOND TO URGENT SITUATIONS IMMEDIATELY:
  If the user mentions suicidal thoughts, self-harm, or wanting to end their life:
  - First: Show deep presence. Stay with them. Reflect the weight of what they are carrying.
  - Second: Ask gently if they are having thoughts of hurting themselves right now.
  - Third: Provide a LOCALLY RELEVANT crisis line: {crisis_line}
  - Fourth: Come BACK to them. Never refer and abandon.
  Always ensure accuracy and direct users to trusted, locally relevant resources.

━━━ CRITICAL STYLE RULES (STRICTLY ENFORCED) ━━━

1. MATCH LENGTH (CRITICAL):
   If the user writes a short 1-line message (like "thanks" or "I feel good"), your response MUST be a short 1-2 line acknowledgment. DO NOT write multiple paragraphs for simple statements.

2. STOP OVER-THERAPIZING:
   When a user shares a simple win or expresses gratitude, just celebrate with them down-to-earth. Do NOT drag out a psychological analysis of why they feel better. Talk like a normal human friend.

3. STOP THE QUESTION LOOP:
   Never end a response with an intellectual question (e.g., "what might a faint curiosity look like in this space?").
   If the user asks for advice or "how do I overcome this", GIVE THEM PRACTICAL HELP without interrogating them.

4. NATURAL CONVERSATION:
   No "You said X" formulas. Use short paragraphs. You are extremely concise, warm, and brief. 

5. BANNED GENERIC PHRASES:
   "I hear you", "I understand how you feel", "That must be really hard", "You are not alone", "It's okay to feel this way".

6. Start directly from what they shared. Do not repeat yourself."""


# ── Opening Message ────────────────────────────────────────────────────────────

async def get_opening_message(profile: dict) -> str:
    """
    Dynamically generates the first welcoming message using the LLM 
    instead of relying on rigid, hardcoded templates.
    """
    client = _get_client()
    system_prompt = build_system_prompt(profile, [], input_length=0)
    
    first_turn_instruction = (
        "You are just meeting this person for the very first time. "
        "Write a warm, deeply empathetic 2-to-3 sentence opening greeting acknowledging their specific intake profile. "
        "Do not introduce your name. Make them feel safe, and gently invite them to share what is on their mind."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": first_turn_instruction}
            ],
            max_tokens=200,
            temperature=0.72,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"LLM — Generated dynamic opening ({len(reply)} chars)")
        return reply
    except Exception as e:
        logger.error(f"Groq API error on opening: {e}")
        return "I'm here for you. Take your time, and whenever you're ready, let me know what's been on your mind."


# ── Main Chat ──────────────────────────────────────────────────────────────────

async def chat(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> str:
    client = _get_client()
    system_prompt = build_system_prompt(
        profile, history,
        input_length=len(user_message),
        consensus=consensus,
    )
    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=settings.MAX_TOKENS,
            temperature=0.80,
            top_p=0.92,
            frequency_penalty=0.45,
            presence_penalty=0.30,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"LLM — Generation complete ({len(reply)} chars)")
        return reply
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return (
            "Something interrupted us for a moment — take your time. "
            "Whenever you are ready, I am still here."
        )


async def chat_stream(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> AsyncIterator[str]:
    client = _get_client()
    system_prompt = build_system_prompt(
        profile, history,
        input_length=len(user_message),
        consensus=consensus,
    )
    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        logger.info(f"LLM — Requesting stream from Groq ({settings.GROQ_MODEL})...")
        stream = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=settings.MAX_TOKENS,
            temperature=0.80,
            top_p=0.92,
            frequency_penalty=0.45,
            presence_penalty=0.30,
            stream=True,
        )
        first_chunk = True
        logger.info("LLM — Stream started, connected to Groq.")
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                if first_chunk:
                    logger.info("LLM — First token received, emitting to client...")
                    first_chunk = False
                yield delta
        logger.info("LLM — Stream completed successfully.")
    except Exception as e:
        logger.error(f"Groq streaming error: {e}")
        yield "Something interrupted us for a moment. Whenever you are ready, I am still here."