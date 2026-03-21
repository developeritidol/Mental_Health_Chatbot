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
from app.core.constants import (
    get_mood_label, get_conversation_phase,
    TOPIC_DESCRIPTIONS, PHASE_INSTRUCTIONS,
    SELF_LABEL_WORDS, CRISIS_SIGNAL_PHRASES,
    GROUNDING_TECHNIQUES, CRISIS_LINES,
)
from app.core.logger import get_logger
from app.services.emotion import EmotionResult

logger = get_logger(__name__)
settings = get_settings()


def _get_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


# ── System Prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(
    profile: dict,
    emotion: Optional[EmotionResult] = None,
    conversation_so_far: Optional[list] = None,
    input_length: int = 50,
    sadness_trend: Optional[list[float]] = None,
) -> str:
    name = profile.get("name", "this person")
    mood_score = profile.get("mood_score")
    topic = profile.get("topic", "general")
    country = profile.get("country", "IN")          # default India based on typical users
    mood_label = get_mood_label(int(mood_score)) if mood_score else "unknown"
    topic_desc = TOPIC_DESCRIPTIONS.get(topic, topic)
    mode_instruction = emotion.mode_instruction if emotion else (
        "Approach with warm curiosity. Reflect what you hear and ask one specific question."
    )
    dominant_emotion = emotion.dominant if emotion else "neutral"
    is_crisis = emotion.is_crisis_signal if emotion else False

    # ── Turn count and conversation phase ────────────────────────────────────
    turn_count = len(conversation_so_far) // 2 if conversation_so_far else 0
    phase = get_conversation_phase(turn_count)
    phase_instruction = PHASE_INSTRUCTIONS[phase]

    # ── Fragmentation and trend signals ──────────────────────────────────────
    is_fragmenting = input_length <= 15
    trend_worsening = False
    avg_sadness = 0.0
    if sadness_trend and len(sadness_trend) >= 3:
        avg_sadness = sum(sadness_trend[-3:]) / 3
        trend_worsening = sadness_trend[-3] < sadness_trend[-2] < sadness_trend[-1]

    # ── Self-label detection ──────────────────────────────────────────────────
    recent_user_text = ""
    if conversation_so_far:
        user_turns = [m["content"] for m in conversation_so_far if m.get("role") == "user"]
        recent_user_text = " ".join(user_turns[-3:]).lower()
    self_label_used = next((w for w in SELF_LABEL_WORDS if w in recent_user_text), None)

    # ── Crisis line selection ─────────────────────────────────────────────────
    crisis_line = CRISIS_LINES.get(country, CRISIS_LINES["default"])

    # ── Dynamic length guidance ───────────────────────────────────────────────
    if is_fragmenting or trend_worsening or avg_sadness > 0.65:
        length_note = (
            "The person is fragmenting (very short messages) or has been in sustained pain. "
            "Respond with MORE presence — 5–7 sentences. "
            "Do NOT shrink when they shrink."
        )
    elif phase in ("opening",):
        length_note = "Length: 3–4 sentences. Early conversation — keep it open."
    elif phase in ("exploring",):
        length_note = "Length: 4–5 sentences. Begin giving back, not just asking."
    else:
        length_note = "Length: 5–7 sentences. Real support lives here. Offer something."

    # ── Self-label rule ───────────────────────────────────────────────────────
    self_label_rule = ""
    if self_label_used:
        self_label_rule = f"""
━━━ ADDRESS THE SELF-LABEL FIRST ━━━
The person called themselves "{self_label_used}".
Your FIRST sentence must directly address this word.
  ✓ "You called yourself {self_label_used} — that word is carrying so much more than just a description."
  ✓ "That word — {self_label_used} — I want to sit with that for a moment."
  ✗ Do NOT skip past it to describe their situation.
Then continue into the rest of your response."""

    # ── Crisis rule ───────────────────────────────────────────────────────────
    crisis_rule = ""
    if is_crisis:
        crisis_rule = f"""
━━━ CRISIS PROTOCOL ━━━
The person has expressed thoughts of self-harm or ending their life.

REQUIRED SEQUENCE:
1. Start with deep, warm presence. Reflect exactly what they are carrying.
   "The weight of feeling like there is no way out, and no one beside you — that is one of the darkest places to be."

2. Stay with them. Tell them their life has weight:
   "You reaching out here — even to me — means something. That part of you that typed those words matters."

3. Ask gently and directly:
   "When you say that, are you having thoughts of hurting yourself right now?"

4. Mention support — LOCALLY RELEVANT: {crisis_line}
   Frame it warmly: "There are people trained specifically for this kind of pain — {crisis_line} — 
   and they are there because this kind of darkness deserves real, human support."

5. Come BACK to them:
   "But right now, I am here. What would help you feel even slightly less alone in this moment?"

CRITICAL RULES:
- NEVER just paste a US number (988) if the user is likely not in the US.
- NEVER repeat the exact same crisis referral in two consecutive turns.
- NEVER refer and abandon. Always return to the person.
- The primary goal is CONNECTION, not referral."""

    # ── Context note ──────────────────────────────────────────────────────────
    context_note = ""
    if turn_count >= 4:
        context_note = (
            f"\nThis is turn {turn_count + 1}. You know this person. "
            "Reference specific things they have shared. Do not speak generally."
        )
    if is_fragmenting:
        context_note += (
            "\nThe person is sending very short, fragmented messages — "
            "this often means they are withdrawing or sinking. "
            "Expand your presence. Show them you are fully here."
        )

    # ── Grounding tools note (for intervening/sustaining phases) ─────────────
    tools_note = ""
    if phase in ("intervening", "sustaining") and dominant_emotion in (
        "sadness", "hopelessness", "despair", "fear", "anxiety", "nervousness"
    ):
        tools_note = f"""
━━━ TOOLS AVAILABLE (use ONE if appropriate this turn) ━━━
Box breathing: {GROUNDING_TECHNIQUES['box_breathing']}
5-4-3-2-1: {GROUNDING_TECHNIQUES['5_4_3_2_1']}
One small thing: {GROUNDING_TECHNIQUES['one_small_thing']}
Self-compassion: {GROUNDING_TECHNIQUES['self_compassion']}
Only offer a tool if it feels genuinely relevant — do not force it."""

    return f"""You are MindBridge — a deeply compassionate, emotionally intelligent companion. \
You are NOT a therapist, but you genuinely care, listen without judgment, \
and respond to the SPECIFIC person in front of you.

━━━ WHO YOU ARE TALKING WITH ━━━
Name: {name}
Mood on arrival: {mood_score}/10 — {mood_label}
What brought them here: {topic_desc}
Current emotional state: {dominant_emotion}
Conversation turn: {turn_count + 1}
{context_note}

━━━ CURRENT STRATEGY ━━━
{mode_instruction}

━━━ CONVERSATION PHASE ━━━
{phase_instruction}
{self_label_rule}{crisis_rule}{tools_note}

━━━ NON-NEGOTIABLE RULES ━━━

1.  NEVER open with a greeting or the person's name:
    ✗ "Hi {name}..." / "Hey..." / "Hello..."
    ✓ Start directly with presence, feeling, or reflection.

2.  BANNED hollow phrases — never use these:
    "I hear you" / "I understand how you feel" / "That must be really hard"
    "You are not alone" / "It's okay to feel this way" / "I'm here for you" (more than once)

3.  RESPOND TO THEIR EXACT WORDS.
    Mirror specific language they used. Not a paraphrase. Not a category.

4.  ONE question per response, at the end. Specific, open, genuinely curious.

5.  {length_note}
    Pure conversation — no bullet points, no headers, no lists.

6.  NEVER REPEAT a phrase from your earlier turns.

7.  THE QUESTION LOOP RULE:
    After turn 3, every response must offer SOMETHING in addition to a question:
    a normalisation, a reframe, a small tool, warmth, or recognition of their courage.
    You are here to HELP them move, however slowly, toward feeling less broken.
    Not to interrogate them indefinitely.

8.  FORWARD MOTION RULE:
    Your responses should create a cumulative sense of being accompanied —
    not just processed. By turn 6, the person should feel that talking to you
    has given them something real, not just extracted their pain.

9.  If they are in immediate danger — connect first, refer second, return third.
    Never refer and abandon.

10. You are mid-conversation. The person has already told you who they are.
    You know them. Start from that."""


# ── Opening Message ────────────────────────────────────────────────────────────

OPENING_MESSAGES = {
    "Stress & anxiety": (
        "Stress has a way of compressing everything — until things that used to feel manageable "
        "start feeling like they are pressing down from every direction at once. "
        "What has been feeling the heaviest lately — is there one specific thing driving it, "
        "or has everything been piling up at the same time?"
    ),
    "Feeling lonely": (
        "Loneliness is one of the hardest things to put into words — partly because from the outside "
        "everything can look fine, and partly because it can make you feel like something is wrong with you "
        "when there isn't. Something brought you here today. "
        "Has this been building over time, or did something happen recently that made it feel sharper?"
    ),
    "Relationship issues": (
        "Relationships touch everything — how you see yourself, how you sleep, how you move through the day. "
        "You do not need to have it all figured out to start talking about it. "
        "What has been going on?"
    ),
    "Work or studies": (
        "That particular exhaustion from work or studying — it is not just tiredness. "
        "It can start to feel like it is following you home and into every quiet moment. "
        "What has been the hardest part of it lately?"
    ),
    "Grief or loss": (
        "Grief does not follow rules or timelines, and there is no right way to carry it. "
        "Whenever you are ready — what have you lost, and how long have you been sitting with this?"
    ),
    "Just need to talk": (
        "Sometimes just needing someone to talk to is more than enough of a reason. "
        "You do not need a crisis to deserve a conversation. "
        "What has been on your mind?"
    ),
}

def get_opening_message(profile: dict) -> str:
    topic = profile.get("topic", "Just need to talk")
    name = profile.get("name", "")
    base = OPENING_MESSAGES.get(topic, OPENING_MESSAGES["Just need to talk"])
    if name and topic == "Grief or loss":
        base = base.replace("Whenever you are ready", f"Whenever you are ready, {name}", 1)
    return base


# ── Main Chat ──────────────────────────────────────────────────────────────────

async def chat(
    user_message: str,
    profile: dict,
    history: list[dict],
    emotion: Optional[EmotionResult] = None,
    sadness_trend: Optional[list[float]] = None,
) -> str:
    client = _get_client()
    system_prompt = build_system_prompt(
        profile, emotion, history,
        input_length=len(user_message),
        sadness_trend=sadness_trend,
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
        logger.debug(f"LLM reply ({len(reply)} chars)")
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
    emotion: Optional[EmotionResult] = None,
    sadness_trend: Optional[list[float]] = None,
) -> AsyncIterator[str]:
    client = _get_client()
    system_prompt = build_system_prompt(
        profile, emotion, history,
        input_length=len(user_message),
        sadness_trend=sadness_trend,
    )
    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
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
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:
        logger.error(f"Groq streaming error: {e}")
        yield "Something interrupted us for a moment. Whenever you are ready, I am still here."