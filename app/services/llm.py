"""
LLM Service — Groq (llama-3.3-70b-versatile)
──────────────────────────────────────────────
Handles:
  • System prompt construction (profile + emotion mode)
  • Conversation history management
  • Groq API call (streaming + non-streaming)
  • Opening message generation (personalised per profile)
"""

from __future__ import annotations
import asyncio
from typing import AsyncIterator, Optional

from groq import AsyncGroq

from app.core.config import get_settings
from app.core.constants import get_mood_label, TOPIC_DESCRIPTIONS
from app.core.logger import get_logger
from app.services.emotion import EmotionResult

logger = get_logger(__name__)
settings = get_settings()

# ── Groq client ───────────────────────────────────────────────────────────────
def _get_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


# ── System Prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(
    profile: dict,
    emotion: Optional[EmotionResult] = None,
    conversation_so_far: Optional[list] = None,
) -> str:
    name = profile.get("name", "this person")
    mood_score = profile.get("mood_score")
    topic = profile.get("topic", "general")
    mood_label = get_mood_label(int(mood_score)) if mood_score else "unknown"
    topic_desc = TOPIC_DESCRIPTIONS.get(topic, topic)
    mode_instruction = emotion.mode_instruction if emotion else (
        "Approach with warm, open curiosity. Ask one meaningful question that helps them go deeper."
    )
    dominant_emotion = emotion.dominant if emotion else "neutral"

    # Build a description of emotion trend if we have history
    trend_note = ""
    if conversation_so_far and len(conversation_so_far) >= 6:
        trend_note = (
            "\nThis conversation has been going on for a while — "
            "you have a growing sense of this person's situation. "
            "Reference specific things they've shared earlier when it feels natural."
        )

    return f"""You are MindBridge — a deeply compassionate, emotionally intelligent companion. \
You are NOT a therapist, but you care genuinely, listen without judgment, \
and respond to the SPECIFIC person in front of you.

━━━ WHO YOU ARE TALKING TO ━━━
Name: {name}
Current mood score: {mood_score}/10 — {mood_label}
What brought them here: {topic_desc}
Detected emotional state right now: {dominant_emotion}
{trend_note}

━━━ YOUR RESPONSE RIGHT NOW ━━━
{mode_instruction}

━━━ ABSOLUTE RULES — NEVER BREAK THESE ━━━

1.  NEVER open with a greeting or name:
    ✗ "Hi {name}, I can hear that..."
    ✗ "Hey, that sounds really difficult."
    ✗ "Hello! I'm here for you."
    ✓ Just start. With the feeling. With the question. With presence.
    The first word of your response must NOT be Hi / Hey / Hello / Oh hi / {name}.

2.  NEVER use these exact phrases (they sound robotic and hollow):
    ✗ "I hear you."  ✗ "I understand how you feel."  ✗ "That must be really hard."
    ✗ "I'm here for you." (more than once)  ✗ "You are not alone."  ✗ "It's okay to feel this way."
    Find fresh, specific language every single time.

3.  RESPOND TO THEIR EXACT WORDS — not a paraphrase, not a category.
    If they say something ambiguous like "I want to end this" — do not assume you know what "this" means.
    Explore with care: "What is it that you want to end?" — one warm question.

4.  ONE question per response. Make it open, specific to what they said, and genuinely curious.

5.  Length: 2–4 sentences. No bullet points. No headers. No lists. Pure conversation.

6.  Never repeat a phrase you used in a previous turn of this conversation.

7.  Match the weight of their message:
    • Heavy, dark pain → be slow, minimal, very present. Don't rush to comfort.
    • Anxiety → be steady, calm, grounding. Present moment.
    • Anger → name the anger fully FIRST, then gently find what's underneath.
    • Hopelessness → stay close. Small questions. No silver linings yet.

8.  If they seem to be in immediate danger — respond with warmth and presence FIRST,
    then gently and naturally mention that there are people who specialise in this kind of pain,
    without making them feel handed off or dismissed. Never just paste a phone number.

9.  You are mid-conversation. Always. Even on the first turn after intake.
    The person has already told you their name, their mood, and what brought them here.
    You know them a little. Start from there."""


# ── Opening Message (post-intake) ────────────────────────────────────────────

OPENING_MESSAGES = {
    "Stress & anxiety": (
        "The way stress builds up — it doesn't always announce itself clearly, "
        "it just keeps adding weight until things that used to feel manageable start to feel impossible. "
        "What's been feeling the heaviest lately — is there one specific thing, or has everything been piling up at once?"
    ),
    "Feeling lonely": (
        "Loneliness is one of those things that's hard to put into words, partly because "
        "from the outside everything can look fine. But something brought you here today — "
        "has this been building over time, or did something specific happen that made it sharper?"
    ),
    "Relationship issues": (
        "Relationships touch everything — how you feel about yourself, how you sleep, "
        "how you move through your day. You don't need to have it all sorted to start talking about it. "
        "What's been going on?"
    ),
    "Work or studies": (
        "That particular kind of exhaustion that comes from work or studying — it's not just tiredness, "
        "it can start to feel like it's following you everywhere. "
        "What's been the hardest part of it lately?"
    ),
    "Grief or loss": (
        "Grief doesn't follow rules or timelines, and there's no right way to carry it. "
        "Whenever you're ready — what have you lost, and how long have you been sitting with this?"
    ),
    "Just need to talk": (
        "Sometimes just needing someone to talk to is more than enough of a reason. "
        "You don't need a crisis to deserve a conversation. "
        "What's been on your mind?"
    ),
}

def get_opening_message(profile: dict) -> str:
    name = profile.get("name", "")
    topic = profile.get("topic", "Just need to talk")
    base = OPENING_MESSAGES.get(topic, OPENING_MESSAGES["Just need to talk"])
    # Weave in their name naturally once — but not as a greeting opener
    if name:
        # Insert name mid-sentence naturally for certain topics
        if topic in ("Feeling lonely", "Grief or loss"):
            base = base.replace("Whenever you're ready", f"Whenever you're ready, {name}", 1)
        # For others, don't force it — let first response use it naturally
    return base


# ── Main Chat Function ────────────────────────────────────────────────────────

async def chat(
    user_message: str,
    profile: dict,
    history: list[dict],
    emotion: Optional[EmotionResult] = None,
) -> str:
    """
    Send one turn to Groq and return the assistant's response.
    `history` = list of {"role": "user"|"assistant", "content": "..."}
    """
    client = _get_client()
    system_prompt = build_system_prompt(profile, emotion, history)

    messages = history[-settings.MAX_HISTORY_TURNS * 2:]  # keep last N turns
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=settings.MAX_TOKENS,
            temperature=0.82,      # slightly creative but grounded
            top_p=0.92,
            frequency_penalty=0.35,  # discourages repeating phrases
            presence_penalty=0.20,
        )
        reply = response.choices[0].message.content.strip()
        logger.debug(f"LLM reply ({len(reply)} chars): {reply[:80]}...")
        return reply
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return (
            "Something interrupted us for a moment. "
            "Take your time — whenever you're ready, I'm still here."
        )


async def chat_stream(
    user_message: str,
    profile: dict,
    history: list[dict],
    emotion: Optional[EmotionResult] = None,
) -> AsyncIterator[str]:
    """
    Streaming version — yields text chunks as they arrive from Groq.
    Use this with an SSE endpoint for a real-time typing effect.
    """
    client = _get_client()
    system_prompt = build_system_prompt(profile, emotion, history)
    messages = history[-settings.MAX_HISTORY_TURNS * 2:]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        stream = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=settings.MAX_TOKENS,
            temperature=0.82,
            top_p=0.92,
            frequency_penalty=0.35,
            presence_penalty=0.20,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:
        logger.error(f"Groq streaming error: {e}")
        yield "Something interrupted us for a moment. Whenever you're ready, I'm still here."