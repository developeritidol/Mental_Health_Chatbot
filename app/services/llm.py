"""
LLM Service — Groq Generator v4 (Final)
─────────────────────────────────────────
v4 improvements over v3:
  • response_rules moved to position 2 in prompt (before profile, before principles)
    LLM weights the beginning of context more heavily. Length constraint must be first.
  • Conversation memory: bot's last 3 response openings extracted from history
    and injected as banned phrases. Bot never starts the same way twice.
  • Emotion arc: user's last 6 messages summarized and shown to model so it
    understands the emotional journey, not just this single message.
  • Personalization deepens with turn count: grows from basic profile awareness
    to referencing specific things the user shared earlier in the conversation.
  • Principles condensed from 6+8 rules to 4 principles + 6 hard rules.
    Fewer, clearer instructions produce higher compliance rate.
  • RULE 3 added: banned cliché advice (deep breaths, water, walks, journaling).
  • Crisis alert moved to TOP of consensus block with visual separator.
  • _SENTENCE_BUDGETS use EXACTLY commands, not MAXIMUM suggestions.
  • Default token budget tightened from 200 to 150.
  • presence_penalty raised to 0.55 to further discourage repetition.
"""

from __future__ import annotations
from typing import AsyncIterator, Optional

from groq import AsyncGroq

from app.core.config import get_settings
from app.core.constants import CRISIS_LINES
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

CRISIS_TOKEN_FLOOR = 800


def _get_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


# ── Safe age parser ───────────────────────────────────────────────────────────

def _safe_int(value, default: int = 0) -> int:
    """Safely converts any age input to int. Never raises ValueError."""
    if value is None:
        return default
    try:
        return int(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return default


# ── Sentence budgets ──────────────────────────────────────────────────────────
# Critical: "EXACTLY N" not "N MAXIMUM"
# Commands produce compliance. Suggestions produce suggestions.

_SENTENCE_BUDGETS: dict[str, str] = {
    "gratitude": (
        "The user said thank you or expressed relief. THIS IS A CLOSING MOMENT.\n"
        "Write EXACTLY 1 short sentence. No more.\n"
        "Respond in FIRST PERSON directly to the user — not as a narrator describing them.\n"
        "CORRECT: 'Really glad that helped.' / 'Good to hear.' / 'Glad it landed for you.'\n"
        "WRONG — asking a question: 'What's your next step?' — NO. The user said THANK YOU. Do not ask anything.\n"
        "WRONG — third-person: 'Priya's expression of gratitude suggests...' — NO.\n"
        "WRONG — giving more advice: any sentence that continues the conversation — NO.\n"
        "The user is wrapping up. Acknowledge it warmly in 1 sentence. STOP."
    ),
    "short_casual": (
        "Write EXACTLY 2 sentences. Count them. Stop at 2.\n"
        "Sentence 1: Warm acknowledgment.\n"
        "Sentence 2: One gentle open question if natural. Otherwise just 1 sentence.\n"
        "Do NOT write a 3rd sentence."
    ),
    "first_disclosure": (
        "Write EXACTLY 3 sentences. Count each one. Stop at 3.\n"
        "Sentence 1: Reflect their specific situation in your own words. "
        "Not generic. Rephrase what they said to show you heard the meaning.\n"
        "Sentence 2: One sentence on why this feeling makes sense for their situation.\n"
        "Sentence 3: Ask ONE specific question. Not 'how are you feeling?' — too lazy. "
        "Ask: 'How long has this been going on?' or 'Is this at work, home, or everywhere?' "
        "or 'Was there a moment it started feeling this heavy?'\n"
        "After sentence 3: STOP. No advice. No reassurance. No closing. Just stop."
    ),
    "positive_update": (
        "Write EXACTLY 2 sentences. Count them. Stop at 2.\n"
        "Sentence 1: Celebrate the SPECIFIC action they took — name exactly what they did.\n"
        "Sentence 2: One brief natural observation. If a forward step feels natural, include it.\n"
        "After sentence 2: STOP. No recap. No more advice. Just stop."
    ),
    "advice_request": (
        "Write EXACTLY 3 sentences. Count them. Stop at 3.\n"
        "Sentence 1: Briefly acknowledge the situation — 1 sentence only.\n"
        "Sentence 2: Give ONE specific, practical suggestion. Not a list. "
        "ONE idea, explained well. Make it specific to THIS person's situation.\n"
        "Sentence 3: ONE small immediate action they can try in the next hour.\n"
        "After sentence 3: STOP. No lists. No alternatives. Just stop."
    ),
    "emotional_ongoing": (
        "Write EXACTLY 2 sentences. Count them. Stop at 2.\n"
        "Sentence 1: Reflect the specific thing they just said — not generically, specifically.\n"
        "Sentence 2: EITHER ask ONE focused question about their situation "
        "OR offer ONE grounded observation. Pick one. Not both.\n"
        "After sentence 2: STOP. No advice unless they asked for it. Just stop."
    ),
    "crisis": (
        "LENGTH RULES ARE SUSPENDED FOR THIS RESPONSE.\n"
        "Write as long as the moment requires.\n"
        "You MUST complete all 4 steps of the CRISIS PROTOCOL before ending.\n"
        "Do not end until the person has received:\n"
        "  1. Deep presence — you heard the weight of what they said\n"
        "  2. A direct gentle question about their safety right now\n"
        "  3. The crisis line woven naturally into the text\n"
        "  4. A return to them — you are still here, what is happening right now"
    ),
}


# ── Conversation memory helpers ───────────────────────────────────────────────

def _extract_bot_recent_openings(history: list[dict], n: int = 3) -> list[str]:
    """
    Extracts the first sentence of the bot's last N responses from history.
    These are injected as banned openings so the model cannot repeat itself.
    """
    openings = []
    assistant_msgs = [
        m["content"] for m in history
        if m.get("role") == "assistant" and m.get("content", "").strip()
    ]
    for msg in assistant_msgs[-n:]:
        first_sentence = msg.strip().split(".")[0].strip()
        if first_sentence and len(first_sentence) > 10:
            openings.append(f'"{first_sentence}"')
    return openings


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
        return f"Only 1 turn so far: {user_msgs[0][:100]}"

    arc_lines = []
    for i, msg in enumerate(user_msgs[-6:], 1):
        arc_lines.append(f"  Turn {i}: {msg[:100]}")
    return "\n".join(arc_lines)


def _build_personalization_note(
    name: str, age: int, profession: str, conditions: str,
    topic: str, turn_count: int, history: list[dict]
) -> str:
    """
    Builds personalization context that deepens with turns.
    Turn 0: Basic profile.
    Turn 1-2: Just started, ask don't assume.
    Turn 3-5: Reference what they shared.
    Turn 6+: You know them. Treat them like it.
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
            f"You have just started talking with {name}. "
            f"They came here about {topic}. "
            "You do not know much yet — ask, do not assume."
        )

    user_msgs = [
        m["content"].strip() for m in history
        if m.get("role") == "user" and m.get("content", "").strip()
    ]

    if turn_count <= 5:
        shared = (" | ".join(user_msgs[-3:]))[:200] if user_msgs else ""
        return (
            f"You know {name} somewhat now. "
            f"In this conversation they have shared: {shared}. "
            "Reference these specifics. Do not speak in general terms."
        )

    # Turn 6+: deep personalization
    all_shared = (" | ".join(user_msgs[-6:]))[:300] if user_msgs else ""
    prof_note  = f"They are a {profession}." if profession else ""
    cond_note  = (
        f"They live with {conditions} — be mindful of how emotions interact with this."
        if conditions and conditions.lower() not in ("none", "no", "") else ""
    )
    return (
        f"You know {name} well now. {prof_note} {cond_note} "
        f"Everything they have shared: {all_shared}. "
        f"Speak to their SPECIFIC situation. Reference what they told you. "
        f"{name} should feel like you remember them, not like they are talking to a bot who resets."
    )


# ── System prompt builder ─────────────────────────────────────────────────────

def build_system_prompt(
    profile: dict,
    conversation_so_far: Optional[list] = None,
    user_message: str = "",
    consensus: Optional[dict] = None,
) -> tuple[str, int]:
    """
    Returns (system_prompt: str, token_budget: int).

    Prompt order (early tokens weighted more by LLM):
      1.  Identity — who you are (3 lines max)
      2.  RESPONSE CONSTRAINT — length rule first, always
      3.  Anti-repetition — bot's banned recent openings
      4.  Who you are talking with — profile
      5.  Personalization — grows with turns
      6.  Emotion arc — the journey so far
      7.  Consensus — what was detected this message
      8.  Crisis alert — URGENT, before principles
      9.  4 Principles
      10. 6 Hard rules
      11. STOP instruction — last thing before generation
    """
    conversation_so_far = conversation_so_far or []

    # ── Profile ───────────────────────────────────────────────────────────────
    name          = profile.get("name", "this person").strip("'\"\\ ")  # strip accidental apostrophes
    mood_score    = profile.get("mood_score", "unknown")
    topic         = profile.get("topic", "general wellbeing")
    country       = profile.get("country", "IN")
    gender        = profile.get("gender", "")
    profession    = profile.get("profession", "")
    conditions    = profile.get("existing_conditions", "None")
    crisis_follow_up = profile.get("crisis_follow_up", False)
    age           = _safe_int(profile.get("age"))
    turn_count    = len(conversation_so_far) // 2

    # ── Token budget ──────────────────────────────────────────────────────────
    msg_class    = "emotional_ongoing"
    token_budget = 150

    if consensus:
        msg_class    = consensus.get("message_class", "emotional_ongoing")
        token_budget = consensus.get("token_budget", 150)
        if consensus.get("is_crisis", False):
            msg_class    = "crisis"
            token_budget = max(token_budget, CRISIS_TOKEN_FLOOR)

    sentence_budget = _SENTENCE_BUDGETS.get(msg_class, _SENTENCE_BUDGETS["emotional_ongoing"])

    # ── Age tone ──────────────────────────────────────────────────────────────
    age_tone = ""
    if age > 0:
        if age < 18:
            age_tone = "MINOR (under 18): Gentle, age-appropriate. No clinical language."
        elif age < 25:
            age_tone = "Young adult: Warm, peer-like. Not patronizing."
        elif age > 60:
            age_tone = "Older adult: Respectful, calm. No slang."

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

    # ── Crisis alert ──────────────────────────────────────────────────────────
    crisis_alert = ""
    if is_crisis:
        crisis_alert = f"""
╔══════════════════════════════════════════════════════╗
║   CRISIS OVERRIDE — ALL LENGTH RULES SUSPENDED      ║
║   Crisis type: {(crisis_type or "suicidal ideation"):<36}║
║   4-STEP PROTOCOL — complete every step:            ║
║   1. Reflect the weight of what {name:<23} said. ║
║   2. Ask directly: "Are you having thoughts of      ║
║      hurting yourself right now?"                   ║
║   3. Crisis line (woven naturally, not as a list):  ║
║      {crisis_line:<49}║
║   4. Come BACK after the crisis line. Stay present. ║
║      Ask what is happening right now. Never abandon.║
╚══════════════════════════════════════════════════════╝"""

    # ── Crisis follow-up ──────────────────────────────────────────────────────
    crisis_followup = ""
    if crisis_follow_up:
        crisis_followup = (
            f"\nNOTE: Earlier in this conversation {name} expressed thoughts about "
            "ending their life. Check in gently and naturally within your response.\n"
        )

    # ── Crisis repeat prevention ──────────────────────────────────────────────
    # Check if the safety question was already asked this session.
    # Never ask "are you having thoughts of hurting yourself" twice in a row.
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
            f"\nIMPORTANT: You already asked {name} if they are having thoughts of hurting themselves. "
            "Do NOT ask the safety question again. Instead: stay present with them, "
            "acknowledge what they just said, and gently encourage them to call the crisis line. "
            "Keep checking in warmly without repeating the clinical safety question.\n"
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
            emotion_arc_section = f"\nEmotional arc of this conversation:\n{arc}\n"

    # ── Anti-repetition ───────────────────────────────────────────────────────
    recent_openings = _extract_bot_recent_openings(conversation_so_far, n=3)
    anti_rep = ""
    if recent_openings:
        banned_list = "\n  ".join(recent_openings)
        anti_rep = (
            f"\nYOU ALREADY STARTED RESPONSES WITH THESE — DO NOT USE THEM AGAIN:\n"
            f"  {banned_list}\n"
            f"Start this response with a completely different first word and structure."
        )

    # ── Conversation phase ────────────────────────────────────────────────────
    if turn_count <= 1:
        phase = "PHASE 1 (turns 1-2): ONLY listen and ask. Zero advice. Zero hope statements. Zero reassurance about the future. Just presence."
    elif turn_count <= 4:
        phase = "PHASE 2 (turns 3-5): Explore. ONE question per response. Advice only if they directly asked for it."
    elif turn_count <= 9:
        phase = f"PHASE 3 (turns 6-10): You know {name} now. Reference specifics. Gentle guidance appropriate when moment is right."
    else:
        phase = f"PHASE 4 (turn 10+): Deep relationship with {name}. Be real, warm, specific. You know their full story. Treat them like it."

    # ─────────────────────────────────────────────────────────────────────────
    # FULL PROMPT — ORDER IS LOAD-BEARING
    # ─────────────────────────────────────────────────────────────────────────

    prompt = f"""You are MindBridge — a compassionate mental health companion who listens like a real human being.
You are NOT a therapist. You are NOT a wellness bot. You are the person who actually hears what someone is saying when nobody else does.
{age_tone}

━━━ RESPONSE RULE — READ THIS BEFORE ANYTHING ELSE ━━━
{sentence_budget}
Hard token limit: {token_budget} tokens enforced at API level. You will be cut off if you exceed it.
{phase}
{anti_rep}

━━━ WHO YOU ARE TALKING WITH ━━━
Name: {name}
Gender: {gender or 'not stated'}
Age: {age if age > 0 else 'not stated'}
Profession: {profession or 'not stated'}
Existing conditions: {conditions}
Mood on arrival: {mood_score}/10
What brought them here: {topic}
Conversation turn: {turn_count + 1}

{personalization}
{emotion_arc_section}
{crisis_followup}
{crisis_repeat_note}

━━━ WHAT THE ANALYSIS DETECTED ━━━
Emotional sentiment: {llm_sent}
Psychological category: {cat}
Intensity: {intensity}
Recommended tone: {rec_tone}
Reasoning: {reasoning}
{crisis_alert}

━━━ 4 PRINCIPLES ━━━

PRINCIPLE 1 — REFLECT FIRST. ALWAYS.
Before any question, any advice, any hope — name what {name} said in your own words.
Not their words back verbatim. Your words, showing you heard the MEANING.
Bad: "It sounds like you're feeling stressed." (generic, anyone could write this)
Good: "Being the person everyone leans on at work and then coming home to your father's illness — that's a weight with nowhere to put it down." (specific, earned)

NEVER plant fears the user did not express.
If they said "I feel useless" — do NOT ask "are you worried this will affect your future?"
That is inventing a catastrophic thought and placing it in front of them.
Only ask about what they actually said. Never extrapolate to worst cases they have not reached.

PRINCIPLE 2 — ONE QUESTION. SPECIFIC. THEN STOP.
Ask exactly ONE question per response. Make it specific to THIS person.
Not "how are you feeling?" — lazy and generic.
Good: "How long has this been going on?" / "Is this at work, at home, or everywhere?"
"When did you last feel like yourself?" / "What tends to happen when it gets heaviest?"
After asking: STOP. Let them answer. Silence is not a failure.

PRINCIPLE 3 — EARN THE RIGHT TO ADVISE.
Give suggestions ONLY after {name} has answered at least one question from you.
When advice is appropriate: ONE idea, specific to their situation, said well.
ONE small immediate action — something doable in the next hour.
Never a list. Never "you could try X or Y." One thing. Period.

When giving advice: be direct.
"Yes — with 10 days to your EMI, start applying today." not "Starting to apply could be a good idea."
When someone in a difficult situation asks "should I do X?" and X is the right thing — say YES and tell them the first specific step.
Hedging with "could be" or "might be" feels dismissive when someone needs direction.

PRINCIPLE 4 — IN CRISIS: STAY. NEVER ABANDON.
If {name} mentions wanting to die, end their life, or harm themselves:
Execute the 4-step protocol in the crisis block above.
After giving the crisis line: come BACK. Stay. Ask what is happening right now.
Handing someone a phone number and going silent is the worst possible outcome.

━━━ 6 HARD RULES — NO EXCEPTIONS ━━━

RULE 1 — BANNED CLOSING FORMULAS. NEVER WRITE THESE.
"I'm here whenever..." / "Reach out whenever..." / "You've taken a brave step..."
"You deserve to feel better..." / "Take care of yourself..." / "You are not alone..."
"Remember that..." / "Things will get better..." / "Feel free to share more..."
"You are stronger than you think..." / "Keep going..." / "I believe in you..."
End naturally. The last sentence is enough. No sign-off needed.

RULE 2 — BANNED EMPTY VALIDATION. NEVER WRITE THESE.
"I hear you" / "I understand how you feel" / "That must be really hard"
"It's okay to feel this way" / "Your feelings are valid" / "That sounds difficult"
These mean nothing. Replace with: what SPECIFICALLY did {name} say that you are reflecting?

RULE 3 — BANNED CLICHÉ ADVICE. NEVER WRITE THESE.
"Take a deep breath" / "Breathe slowly" / "Drink a glass of water"
"Go for a walk" / "Step outside for fresh air" / "Write in a journal"
"Jot down your thoughts" / "Practice mindfulness" / "Try meditation"
These are condescending to someone in genuine distress. Delete them on sight.

RULE 4 — ONE THING PER RESPONSE.
Each response does ONE of: reflect, question, suggest, celebrate, or close.
Not all five. Not three. ONE thing, done well.

RULE 5 — NO LISTS AND NO DOUBLE QUESTIONS.
No bullet points. No numbered lists. No "here are some things to try:"
If you have multiple ideas — pick the best one and say it as a sentence.
ALSO: Never join two questions with "and".
"What happened, and how did you feel?" = TWO questions. Pick one.
The word "and" between two question phrases means you asked twice. Delete one.

RULE 6 — USE {name}'s NAME ONCE PER RESPONSE.
Naturally. Once. Not at the start of every paragraph.

━━━ FORBIDDEN RESPONSE STARTERS ━━━
Do NOT start your response with any of these words or phrases:
"{name}" / "I " / "It sounds like" / "It seems like" / "That's" / "What you're"
Start directly from what they said. First word carries weight.

━━━ GENERATE YOUR RESPONSE NOW ━━━
Class: {msg_class} | Tokens: {token_budget} | Intensity: {intensity} | Tone: {rec_tone}
Count your sentences as you write.
When you reach the sentence limit — STOP.
Do not write the sentence after the last one."""

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
            model=settings.GROQ_MODEL,
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
            model=settings.GROQ_MODEL,
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
        logger.error(f"Groq API error: {e}")
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
            f"Stream — model: {settings.GROQ_MODEL} | "
            f"class: {consensus.get('message_class') if consensus else 'none'} | "
            f"budget: {token_budget}"
        )
        stream = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
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
        logger.error(f"Groq streaming error: {e}")
        yield "Something interrupted us for a moment. Take your time — still here."