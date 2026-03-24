"""
LLM Service — OpenAI Generator v5
─────────────────────────────────────────
v5: Complete rewrite for GPT-4o.
  • Lean identity-first prompt (~800 tokens vs v4's ~3,500)
  • Removed rigid sentence counts, 4-phase system, 40+ banned phrases
  • 6 natural principles replace 9 hard rules + 5 principles
  • Token budgets increased to let GPT-4o write full, human responses
  • Crisis protocol remains strict and non-negotiable
  • Anti-repetition reduced from 3 banned openings to 1
"""

from __future__ import annotations
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.constants import CRISIS_LINES
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

CRISIS_TOKEN_FLOOR = 800


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# ── Safe age parser ───────────────────────────────────────────────────────────

def _safe_int(value, default: int = 0) -> int:
    """Safely converts any age input to int. Never raises ValueError."""
    if value is None:
        return default
    try:
        return int(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return default


# ── Response guidance per message class ───────────────────────────────────────
# These guide GPT-4o on how to shape each response type.
# No rigid sentence counts — just natural guidance that lets the model breathe.

_RESPONSE_GUIDANCE: dict[str, str] = {
    "gratitude": (
        "The user is wrapping up or saying thanks. Close warmly in 1–2 sentences.\n"
        "Do not ask a question. Do not extend the conversation. Just let it land."
    ),
    "short_casual": (
        "Short casual message. Match their energy — be brief, warm, natural.\n"
        "A question is fine if it fits. Don't overthink it."
    ),
    "first_disclosure": (
        "This person just opened up about something heavy for the first time.\n"
        "This is the most important moment in the conversation. Make them feel deeply heard.\n"
        "Reflect what they said in your own words — show you truly understood, not just heard.\n"
        "Sit with the weight of it. Don't rush to fix, advise, or ask questions.\n"
        "If a question comes naturally at the end, that's fine. But it's not required.\n"
        "Write enough to make them feel like someone actually cares. Usually 4–6 sentences."
    ),
    "positive_update": (
        "They shared a win or a step forward. Celebrate it genuinely.\n"
        "Name what they actually did — be specific, not generic. Share in their moment."
    ),
    "advice_request": (
        "They're asking for help. First acknowledge what they're dealing with.\n"
        "Then give ONE concrete, specific suggestion — not a list, not options.\n"
        "Make it immediately actionable and specific to their situation.\n"
        "Be direct: 'Do X' — not 'You might want to consider X.'\n"
        "Write enough to be genuinely helpful. Usually 4–6 sentences."
    ),
    "emotional_ongoing": (
        "You're in the middle of a deep conversation with someone who is hurting.\n"
        "Be present. Reflect what they just said specifically — not generically.\n"
        "You can sit with them, explore one aspect gently, or share an honest thought.\n"
        "A question is optional — only if it genuinely deepens the conversation.\n"
        "Write enough to feel like a real human responding. Usually 3–5 sentences."
    ),
    "crisis": (
        "ALL LENGTH LIMITS SUSPENDED. Write as much as the moment needs.\n"
        "Complete the full 4-step CRISIS PROTOCOL:\n"
        "  1. Reflect the weight of what they said — be deeply present\n"
        "  2. Ask the safety question directly\n"
        "  3. Include the CRISIS LINE phone number (NON-NEGOTIABLE)\n"
        "  4. Come BACK to them after the number. Stay. Do not abandon them."
    ),
}


# ── Conversation memory helpers ───────────────────────────────────────────────

def _extract_bot_last_opening(history: list[dict]) -> str:
    """
    Extracts the first sentence of the bot's last response.
    Used to prevent the bot from starting the same way twice in a row.
    """
    assistant_msgs = [
        m["content"] for m in history
        if m.get("role") == "assistant" and m.get("content", "").strip()
    ]
    if assistant_msgs:
        last = assistant_msgs[-1].strip().split(".")[0].strip()
        if last and len(last) > 10:
            return f'Do not start your response the same way as your last one: "{last}..."'
    return ""


def _build_emotion_arc(history: list[dict]) -> str:
    """
    Builds a plain-English summary of the emotional arc from the user's messages.
    Gives the model a picture of the journey, not just the current message.
    """
    user_msgs = [
        m["content"].strip() for m in history
        if m.get("role") == "user" and m.get("content", "").strip()
    ]
    if not user_msgs:
        return ""
    if len(user_msgs) == 1:
        return f"Only 1 turn so far: {user_msgs[0][:150]}"

    arc_lines = []
    for i, msg in enumerate(user_msgs[-6:], 1):
        arc_lines.append(f"  Turn {i}: {msg[:120]}")
    return "\n".join(arc_lines)


def _build_personalization_note(
    name: str, age: int, profession: str, conditions: str,
    topic: str, turn_count: int, history: list[dict]
) -> str:
    """ 
    Builds personalization context that deepens with turns.
    """
    if turn_count == 0:
        parts = []
        if profession:
            parts.append(f"{name} is a {profession}")
        if age > 0:
            parts.append(f"age {age}")
        if conditions and conditions.lower() not in ("none", "no", ""):
            parts.append(f"living with {conditions}")
        base = (", ".join(parts) + ".") if parts else ""
        return f"{base} They came here about: {topic}."

    if turn_count <= 2:
        return (
            f"You've just started talking with {name}. "
            f"They came here about {topic}. "
            "You don't know much yet — be curious, not assumptive."
        )

    user_msgs = [
        m["content"].strip() for m in history
        if m.get("role") == "user" and m.get("content", "").strip()
    ]

    if turn_count <= 5:
        shared = (" | ".join(user_msgs[-3:]))[:250] if user_msgs else ""
        return (
            f"You know {name} somewhat now. "
            f"They've shared: {shared}. "
            "Reference what they told you. Be specific, not generic."
        )

    # Turn 6+: deep personalization
    all_shared = (" | ".join(user_msgs[-6:]))[:400] if user_msgs else ""
# ── System prompt builder ─────────────────────────────────────────────────────

def build_system_prompt(
    profile: dict,
    conversation_so_far: Optional[list] = None,
    user_message: str = "",
    consensus: Optional[dict] = None,
) -> tuple[str, int]:
    """
    Returns (system_prompt: str, token_budget: int).

    v5: Lean identity-first prompt designed for GPT-4o.
    Trusts the model's native empathy instead of micromanaging with 40+ rules.
    Crisis protocol remains strict and non-negotiable.
    """
    conversation_so_far = conversation_so_far or []

    # ── Profile ───────────────────────────────────────────────────────────────
    name          = profile.get("name", "this person").strip("'\"\ ")
    mood_score    = profile.get("mood_score", "unknown")
    topic         = profile.get("topic", "general wellbeing")
    country       = profile.get("country", "IN")
    gender        = profile.get("gender", "")
    profession    = profile.get("profession", "")
    conditions    = profile.get("existing_conditions", "None")
    personality   = profile.get("personality_summary", "Not provided")
    crisis_follow_up = profile.get("crisis_follow_up", False)
    age           = _safe_int(profile.get("age"))
    turn_count    = len(conversation_so_far) // 2

    # ── Token budget ──────────────────────────────────────────────────────────
    msg_class    = "emotional_ongoing"
    token_budget = 320

    if consensus:
        msg_class    = consensus.get("message_class", "emotional_ongoing")
        token_budget = consensus.get("token_budget", 320)
        if consensus.get("is_crisis", False):
            msg_class    = "crisis"
            token_budget = max(token_budget, CRISIS_TOKEN_FLOOR)

    response_guidance = _RESPONSE_GUIDANCE.get(msg_class, _RESPONSE_GUIDANCE["emotional_ongoing"])

    # ── Age tone ──────────────────────────────────────────────────────────────
    age_tone = ""
    if age > 0:
        if age < 18:
            age_tone = "They are under 18. Be gentle, age-appropriate, and protective."
        elif age < 25:
            age_tone = "Young adult. Warm, peer-like. Don't be patronizing."
        elif age > 60:
            age_tone = "Older adult. Respectful and calm."

    # ── Crisis line ───────────────────────────────────────────────────────────
    crisis_line = CRISIS_LINES.get(country, CRISIS_LINES["default"])

    # ── Consensus values ──────────────────────────────────────────────────────
    llm_sent    = "unknown"
    cat         = "general"
    intensity   = "moderate"
    is_crisis   = False
    crisis_type = None
    reasoning   = ""
    rec_tone    = "validating"

    if consensus:
        llm_sent    = consensus.get("llm_sentiment", "unknown")
        cat         = consensus.get("category", "general")
        intensity   = consensus.get("intensity", "moderate")
        is_crisis   = consensus.get("is_crisis", False)
        crisis_type = consensus.get("crisis_type", None)
        reasoning   = consensus.get("reasoning", "")
        rec_tone    = consensus.get("recommended_tone", "validating")

    # ── Crisis alert (KEPT EXACTLY AS-IS — non-negotiable safety) ─────────────
    crisis_alert = ""
    if is_crisis:
        active_emergency_phrases = [
            "took some pills", "taken pills", "took pills",
            "already took", "already taken", "swallowed",
            "engine running", "sitting in my car with",
            "cut myself", "cutting myself right now",
            "bleeding", "hurt myself tonight"
        ]
        is_active_emergency = any(
            phrase in user_message.lower()
            for phrase in active_emergency_phrases
        )

        if is_active_emergency:
            crisis_alert = (
                f"\n⚠️ ACTIVE MEDICAL EMERGENCY — IMMEDIATE ACTION ONLY ⚠️\n"
                f"The person has already taken action to harm themselves.\n"
                f"YOUR ONLY PRIORITY: GET THEM EMERGENCY HELP NOW.\n\n"
                f"Write SHORT, URGENT, CLEAR sentences:\n"
                f"1. Tell them to call 112 (India emergency) immediately\n"
                f"2. Tell them to get someone near them right now\n"
                f"3. Include this line: {crisis_line}\n"
                f"4. Ask if anyone is nearby\n\n"
                f"DO NOT give therapy. DO NOT write empathetic paragraphs.\n"
                f"Every word must be about getting them help NOW.\n"
                f"CHECKLIST: Did you include {crisis_line} AND 112? If NO — add them before finishing."
              )
        else:
            crisis_alert = (
                f"\n⚠️ CRISIS OVERRIDE — ALL LENGTH RULES SUSPENDED ⚠️\n"
                f"Crisis type: {crisis_type or 'suicidal ideation'}\n\n"
                f"4-STEP PROTOCOL — complete ALL 4 steps:\n\n"
                f"STEP 1: Reflect the weight of what {name} said.\n"
                f"STEP 2: Ask directly — 'Are you having thoughts of hurting yourself right now?'\n"
                f"STEP 3: Include this crisis line in your response: {crisis_line}\n"
                f"        Write it naturally. This is NON-NEGOTIABLE.\n"
                f"STEP 4: Come BACK after the number. Stay present. Ask what is happening right now.\n\n"
                f"CHECKLIST: Did I include {crisis_line}? If NO — add it now."
            )

    # ── Crisis follow-up ──────────────────────────────────────────────────────
    crisis_followup = ""
    if crisis_follow_up:
        crisis_followup = (
            f"\nEarlier in this conversation {name} expressed thoughts about "
            "ending their life. Check in gently and naturally.\n"
        )

    # ── Crisis repeat prevention ──────────────────────────────────────────────
    crisis_already_asked = False
    if conversation_so_far:
        assistant_msgs = [
            m["content"] for m in conversation_so_far
            if m.get("role") == "assistant" and m.get("content", "").strip()
        ]
        last_few = " ".join(assistant_msgs[-2:]).lower()
        if "thoughts of hurting yourself" in last_few or "thoughts of harming yourself" in last_few:
            crisis_already_asked = True

    crisis_repeat_note = ""
    if is_crisis and crisis_already_asked:
        crisis_repeat_note = (
            f"\nYou already asked {name} if they are having thoughts of hurting themselves. "
            "Do NOT ask again. Stay present, acknowledge what they said, "
            "and gently encourage them to call the crisis line.\n"
        )

    # ── Personalization ───────────────────────────────────────────────────────
    personalization = _build_personalization_note(
        name, age, profession, conditions, topic, turn_count, conversation_so_far
    )

    # ── Emotion arc ───────────────────────────────────────────────────────────
    emotion_arc_section = ""
    if turn_count >= 2:
        arc = _build_emotion_arc(conversation_so_far)
        if arc:
            emotion_arc_section = f"\nTheir emotional journey so far:\n{arc}\n"

    # ── Anti-repetition (reduced to last 1 only) ─────────────────────────────
    anti_rep = _extract_bot_last_opening(conversation_so_far)

    # ─────────────────────────────────────────────────────────────────────────
    # FULL PROMPT — v5: Identity-first, lean, trusts GPT-4o
    # ─────────────────────────────────────────────────────────────────────────

    prompt = f"""You are MindBridge — a deeply compassionate mental health companion.

You talk like a real person who genuinely cares. Not a therapist reading from a manual. Not a wellness app generating motivational quotes. You are the friend who sits with someone at 2 AM and actually listens — the person who makes someone feel less alone just by being present.

Your voice is warm, natural, and conversational. You speak the way a caring human actually speaks — with empathy, honesty, and emotional intelligence. You can be gentle, you can be direct, you can sit in silence with someone's pain. Whatever the moment needs.
{age_tone}

━━━ RESPONSE GUIDANCE ━━━
{response_guidance}
{anti_rep}

━━━ ABOUT {name.upper()} ━━━
Name: {name} | Gender: {gender or 'not stated'} | Age: {age if age > 0 else 'not stated'}
Profession: {profession or 'not stated'} | Conditions: {conditions}
Personality: {personality}
Mood on arrival: {mood_score}/10 | Topic: {topic} | Turn: {turn_count + 1}

{personalization}
{emotion_arc_section}
{crisis_followup}
{crisis_repeat_note}

━━━ EMOTIONAL ANALYSIS ━━━
Sentiment: {llm_sent} | Category: {cat} | Intensity: {intensity} | Tone: {rec_tone}
Reasoning: {reasoning}
{crisis_alert}

━━━ HOW TO BE A GREAT COMPANION ━━━

1. LISTEN DEEPLY — Before anything else, show {name} you truly heard what they said. Reflect their specific words and feelings in your own words. Not generic — specific to what they just told you.

2. BE PRESENT — Sometimes the most powerful thing you can do is sit with someone's pain without trying to fix it. Not every response needs a question or advice. Sometimes "That sounds incredibly heavy" said with genuine feeling is worth more than any suggestion.

3. BE GENUINELY HELPFUL — When {name} needs guidance, be direct and specific. Don't hedge with "you might consider" — say what you actually think. One concrete suggestion is better than five vague ones. No bullet lists. No numbered steps. Just talk to them like a real person.

4. NEVER ABANDON IN CRISIS — If {name} expresses suicidal thoughts or self-harm, follow the crisis protocol above completely. After giving the crisis line, come BACK. Stay.

5. STAY NATURAL — Don't end with motivational sign-offs ("You're not alone!", "I believe in you!", "Remember to take care of yourself!"). Don't use therapy-speak ("completely understandable", "makes sense given", "I can see how that would"). Don't suggest clichés like deep breathing, journaling, or going for walks. Just be real.

6. NO LISTS — Never respond with bullet points or numbered lists. You're having a conversation, not giving a presentation. If you have multiple thoughts, weave them naturally into sentences.

Write your response now. Be the person {name} needs right now."""

    return prompt, token_budget


# ── Opening message ───────────────────────────────────────────────────────────

async def get_opening_message(profile: dict) -> str:
    """
    Generates the first message after intake. Short, warm, specific.

    IMPORTANT: Uses a MINIMAL system prompt — NOT the full build_system_prompt output.
    The full prompt contains crisis protocol content (╔══ boxes, self-harm language)
    which triggers Groq content filtering on gpt-oss-120b and returns 0 chars.
    A clean, simple system prompt avoids this entirely.
    """
    client = _get_client()

    name       = profile.get("name", "this person")
    topic      = profile.get("topic", "general wellbeing")
    mood       = profile.get("mood_score", "")
    profession = profile.get("profession", "")
    age        = _safe_int(profile.get("age"))
    country    = profile.get("country", "IN")

    # Age-appropriate tone
    tone = "warm and human"
    if age > 0 and age < 18:
        tone = "gentle, age-appropriate, no clinical terms"
    elif age > 0 and age < 25:
        tone = "warm, peer-like, not patronizing"

    prof_context = f"They work as a {profession}." if profession else ""
    mood_context = f"Their mood on arrival is {mood}/10." if mood else ""

    # Minimal system prompt — no crisis content, no heavy rules
    minimal_system = (
        f"You are a warm, compassionate mental health companion meeting {name} for the first time. "
        f"Tone: {tone}. "
        f"{prof_context} {mood_context} "
        f"They came here about: {topic}. "
        "Write naturally. Be specific to their situation. Do not be generic."
    )

    user_prompt = (
        f"Write exactly 2 complete sentences as an opening message for {name}:\n"
        f"Sentence 1: Acknowledge what brought them here ({topic}) in a specific, warm way. "
        "Reference their topic and mood. Not generic wellness language.\n"
        "Sentence 2: Invite them to share — without asking a question. "
        "Just open the space.\n\n"
        "Hard rules:\n"
        "- COMPLETE sentences only. Never cut off mid-sentence.\n"
        "- Do not say: 'I'm here for you' / 'brave step' / 'you deserve' / "
        "'reach out whenever' / 'I understand' / 'It sounds like'\n"
        "- Do not start with their name or with 'I'\n"
        "- Each sentence under 20 words\n"
        "- No sign-offs, no lists, no clinical terms"
    )

    try:
        response = await client.chat.completions.create(
            model=settings.MAIN_MODEL,
            messages=[
                {"role": "system", "content": minimal_system},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=120,
            temperature=0.72,
            frequency_penalty=0.5,
            presence_penalty=0.4,
        )
        reply = response.choices[0].message.content.strip()
        if not reply:
            raise ValueError("Empty response from model")
        logger.info(f"Opening message generated ({len(reply)} chars)")
        return reply
    except Exception as e:
        logger.error(f"Opening message error: {e}")
        # Fallback based on topic
        topic_lower = topic.lower()
        if "grief" in topic_lower or "loss" in topic_lower:
            return f"Grief doesn't follow a schedule, and whatever you're carrying right now — you don't have to sort it alone. Take your time, {name}."
        elif "anxiety" in topic_lower or "stress" in topic_lower:
            return f"Stress that builds up over time has a weight to it that's hard to explain to people who haven't felt it. Whatever brought you here today — this is a space for it."
        elif "relationship" in topic_lower:
            return f"Relationship pain has a way of touching everything else in life. Whatever's been happening — share as much or as little as you want."
        else:
            return f"Whatever brought you here today — this space doesn't require you to have it figured out. Start wherever feels right, {name}."


# ── Main chat (non-streaming) ─────────────────────────────────────────────────

async def chat(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> str:
    client = _get_client()
    system_prompt, token_budget = build_system_prompt(
        profile, history, user_message=user_message, consensus=consensus
    )

    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        response = await client.chat.completions.create(
            model=settings.MAIN_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=token_budget,
            temperature=0.78,
            top_p=0.92,
            frequency_penalty=0.65,
            presence_penalty=0.55,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(
            f"Chat — {len(reply)} chars | "
            f"class: {consensus.get('message_class') if consensus else 'none'} | "
            f"budget: {token_budget}"
        )
        return reply
    except Exception as e:
        logger.error(f"[OPENAI CHAT ERROR] {type(e).__name__}: {e}")
        return "Something interrupted us for a moment. Take your time — still here."


# ── Streaming chat ────────────────────────────────────────────────────────────

async def chat_stream(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> AsyncIterator[str]:
    client = _get_client()
    system_prompt, token_budget = build_system_prompt(
        profile, history, user_message=user_message, consensus=consensus
    )

    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        logger.info(
            f"Stream — model: {settings.MAIN_MODEL} | "
            f"class: {consensus.get('message_class') if consensus else 'none'} | "
            f"budget: {token_budget}"
        )
        stream = await client.chat.completions.create(
            model=settings.MAIN_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=token_budget,
            temperature=0.78,
            top_p=0.92,
            frequency_penalty=0.65,
            presence_penalty=0.55,
            stream=True,
        )
        first_chunk = True
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                if first_chunk:
                    logger.info("First token received.")
                    first_chunk = False
                yield delta
        logger.info("Stream completed.")
    except Exception as e:
        logger.error(f"[OPENAI STREAM ERROR] {type(e).__name__}: {e}")
        yield "Something interrupted us for a moment. Take your time — still here."