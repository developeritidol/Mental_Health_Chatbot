"""
LLM Service — Groq Generator v3
─────────────────────────────────
Handles:
  • System prompt construction from consensus + profile + history
  • Dynamic token budget from consensus (set by 8B synthesizer, not string matching)
  • Therapeutic arc: opening → listening → exploring → intervening → sustaining
  • Crisis handling with locally-relevant resources
  • Conversation history management
  • Groq API call (streaming + non-streaming)

v3 changes (all brittle logic removed):
  • _classify_message() DELETED — message_class and token_budget now come
    exclusively from consensus dict produced by safety.py synthesizer.
    This eliminates the "goodbye" → gratitude and "how can I go on" →
    advice_request misclassification bugs.
  • build_system_prompt() now returns (prompt_str, token_budget: int).
    token_budget is read from consensus["token_budget"] — Python never
    independently calculates it.
  • Age parsing uses safe_int() helper — no more ValueError on "twenty" or "25 yrs".
  • Crisis token floor enforced here as a second safety check (defence in depth).
"""

from __future__ import annotations
from typing import AsyncIterator, Optional

from groq import AsyncGroq

from app.core.config import get_settings
from app.core.constants import CRISIS_LINES
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Second line of defence — even if safety.py fallback is wrong,
# a crisis response will never be cut below this.
CRISIS_TOKEN_FLOOR = 400


def _get_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


# ── Safe age parser ───────────────────────────────────────────────────────────

def _safe_int(value, default: int = 0) -> int:
    """
    Safely converts any age input to int.
    Handles: "25", "25 yrs", "twenty", None, "", 25.
    Returns default (0) on any failure — caller treats 0 as 'age not provided'.
    """
    if value is None:
        return default
    try:
        return int(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return default


# ── Sentence budget lookup ────────────────────────────────────────────────────
# Translates message_class from consensus into human-readable instructions
# that go inside the system prompt. The LLM reads these as its writing rules.

_SENTENCE_BUDGETS: dict[str, str] = {
    "gratitude": (
        "1 sentence MAXIMUM.\n"
        "Acknowledge the win specifically. End naturally. No advice."
    ),
    "short_casual": (
        "1–2 sentences MAXIMUM.\n"
        "Warm acknowledgment. One gentle open question if natural. Nothing more."
    ),
    "first_disclosure": (
        "2 sentences MAXIMUM — STRICT LIMIT:\n"
        "  Sentence 1: Validate and reflect their specific situation in your own words.\n"
        "  Sentence 2: Ask ONE specific question to understand better.\n"
        "DO NOT give advice. DO NOT reassure. Just listen and ask."
    ),
    "positive_update": (
        "2 sentences MAXIMUM:\n"
        "  Sentence 1: Celebrate the specific action they took.\n"
        "  Sentence 2: One natural next step IF it flows. Otherwise end warmly.\n"
        "DO NOT recap the whole conversation."
    ),
    "advice_request": (
        "3 sentences MAXIMUM:\n"
        "  Sentence 1: Briefly acknowledge the situation.\n"
        "  Sentence 2: Give ONE specific, highly practical suggestion. No lists.\n"
        "  Sentence 3: ONE small immediate action.\n"
        "NO CLICHÉS: Do not suggest deep breaths, walks, or drinking water."
    ),
    "emotional_ongoing": (
        "2 sentences MAXIMUM — STRICT LIMIT:\n"
        "  Sentence 1: Reflect the exact thing they just said.\n"
        "  Sentence 2: ONE grounded observation OR ONE probing question.\n"
        "DO NOT do both. DO NOT offer advice unless they explicitly asked."
    ),
    "crisis": (
        "This is a crisis response. Length rules are suspended.\n"
        "Write as long as the moment requires.\n"
        "You MUST complete all four steps of Principle 6 fully."
    ),
}


# ── System prompt builder ─────────────────────────────────────────────────────

def build_system_prompt(
    profile: dict,
    conversation_so_far: Optional[list] = None,
    user_message: str = "",
    consensus: Optional[dict] = None,
) -> tuple[str, int]:
    """
    Returns (system_prompt: str, token_budget: int).

    token_budget is read from consensus["token_budget"] set by the 8B synthesizer.
    If consensus is missing, falls back to emotional_ongoing budget (200).
    Crisis token floor is enforced here as a second safety layer.
    """
    name       = profile.get("name", "this person")
    mood_score = profile.get("mood_score")
    topic      = profile.get("topic", "general")
    country    = profile.get("country", "IN")
    gender     = profile.get("gender", "")
    profession = profile.get("profession", "")
    conditions = profile.get("existing_conditions", "None")
    crisis_follow_up = profile.get("crisis_follow_up", False)

    age_raw    = profile.get("age")
    age        = _safe_int(age_raw)   # safe conversion — no ValueError possible

    turn_count = len(conversation_so_far) // 2 if conversation_so_far else 0

    # ── Token budget from consensus (set by 8B, not Python string matching) ──
    msg_class    = "emotional_ongoing"
    token_budget = 200

    if consensus:
        msg_class    = consensus.get("message_class", "emotional_ongoing")
        token_budget = consensus.get("token_budget", 200)

        # Defence-in-depth: if is_crisis is true, enforce the floor regardless
        if consensus.get("is_crisis", False):
            msg_class    = "crisis"
            token_budget = max(token_budget, CRISIS_TOKEN_FLOOR)

    sentence_budget = _SENTENCE_BUDGETS.get(msg_class, _SENTENCE_BUDGETS["emotional_ongoing"])

    # ── Age-aware tone (safe — _safe_int never throws) ────────────────────────
    age_note = ""
    if age > 0:
        if age < 18:   age_note = "This person is a minor. Gentle, age-appropriate language. No clinical terms."
        elif age < 25: age_note = "Young adult. Warm, peer-like tone. Not patronizing."
        elif age > 55: age_note = "Older adult. Respectful, calm tone."

    # ── Health note ───────────────────────────────────────────────────────────
    health_note = ""
    if conditions and conditions.strip().lower() not in ("none", "no", ""):
        health_note = (
            f"Existing conditions: {conditions}. "
            "Be mindful of how these may interact with their emotional state."
        )

    # ── Context note (after turn 3) ───────────────────────────────────────────
    context_note = ""
    if turn_count >= 3:
        context_note = (
            f"This is turn {turn_count + 1}. You know {name} by now. "
            "Reference specific things they have shared. "
            "Speak to THEIR specific situation — never generically."
        )

    # ── Crisis follow-up ──────────────────────────────────────────────────────
    crisis_followup_rule = ""
    if crisis_follow_up:
        crisis_followup_rule = (
            f"\n━━━ CRISIS FOLLOW-UP ━━━\n"
            f"Earlier in this conversation, {name} expressed thoughts about ending their life. "
            "Check in naturally and warmly within your response — not clinically, not abruptly.\n"
        )

    # ── Crisis line ───────────────────────────────────────────────────────────
    crisis_line = CRISIS_LINES.get(country, CRISIS_LINES["default"])

    # ── Consensus block ───────────────────────────────────────────────────────
    consensus_block = ""
    crisis_alert    = ""

    if consensus:
        llm_sent    = consensus.get("llm_sentiment", "unknown")
        cat         = consensus.get("category", "general")
        intensity   = consensus.get("intensity", "moderate")
        is_crisis   = consensus.get("is_crisis", False)
        crisis_type = consensus.get("crisis_type", None)
        reasoning   = consensus.get("reasoning", "")
        rec_tone    = consensus.get("recommended_tone", "validating")

        if is_crisis:
            crisis_alert = (
                f"\n\n[URGENT — SAFETY OVERRIDE ACTIVE]\n"
                f"Crisis type: {crisis_type}\n"
                "You MUST execute Principle 6 in full before ending your response.\n"
                "Steps in order:\n"
                f"  1. Show deep presence — reflect the weight of what {name} is carrying.\n"
                f"  2. Ask gently and directly: 'Are you having thoughts of hurting yourself right now?'\n"
                f"  3. Provide the crisis line naturally (not as a list item): {crisis_line}\n"
                f"  4. Come BACK to {name}. Stay present. Never refer and abandon."
            )

        consensus_block = (
            f"\n━━━ HYBRID CONSENSUS — READ BEFORE RESPONDING ━━━\n"
            f"Statistical emotion (RoBERTa): {profile.get('top_emotions', 'see profile')}\n"
            f"LLM logical sentiment: {llm_sent}\n"
            f"Psychological category: {cat}\n"
            f"Emotional intensity: {intensity}\n"
            f"Recommended tone: {rec_tone}\n"
            f"Reasoning: {reasoning}"
            f"{crisis_alert}\n"
        )

    # ── Response rules for this specific message ──────────────────────────────
    response_rules = (
        f"\n━━━ THIS RESPONSE — STRICTLY ENFORCED ━━━\n"
        f"Message class: {msg_class}\n"
        f"Token budget: {token_budget} (enforced at API level — you will be cut off)\n"
        f"Length rule:\n{sentence_budget}\n"
    )

    prompt = f"""You are MindBridge — a compassionate, emotionally intelligent mental health companion.
You speak like a warm, perceptive human friend who genuinely listens.
You are NOT a therapist giving a session. You are NOT a coach with a framework.
You are the person who actually hears what someone is saying when nobody else does.

━━━ WHO YOU ARE TALKING WITH ━━━
Name: {name}
Gender: {gender if gender else "not provided"}
Age: {age if age > 0 else "not provided"}
Profession: {profession if profession else "not provided"}
Mood on arrival: {mood_score}/10
What brought them here: {topic}
Conversation turn: {turn_count + 1}
{age_note}
{health_note}
{context_note}
{crisis_followup_rule}
{consensus_block}
{response_rules}

━━━ YOUR 6 CORE PRINCIPLES ━━━

PRINCIPLE 1 — LISTEN FIRST. ALWAYS.
  Before advice, before hope, before reassurance — make {name} feel you heard
  exactly what THEY said.
  Reflect their specific situation in your own words.
  Do NOT paraphrase them back verbatim. Rephrase. Show you understood the meaning.
  Do NOT use generic validation. "That must be hard" = nothing.
  "Feeling like no one wants you around — that kind of quiet gets louder every day" = everything.

PRINCIPLE 2 — ONE QUESTION. SPECIFIC.
  If you ask a question, ask exactly ONE.
  Make it specific to THEIR situation — not "how are you feeling?" (lazy).
  Good questions: "How long has this been going on?" / "Is this at work, home, or everywhere?" /
  "When did you last feel like yourself?" / "Was there a moment it started feeling this heavy?"
  Ask. Then stop. Let them answer. Do not fill silence with more words.

PRINCIPLE 3 — EARN THE RIGHT TO GIVE ADVICE.
  You may give suggestions ONLY when:
    (a) {name} has answered at least one of your questions, AND
    (b) You understand their specific situation well enough to give relevant advice.
  When advice is appropriate: ONE suggestion. Not a list. Not "you could try X, Y, or Z."
  One idea, explained warmly. Then ONE small immediate action — something doable today.

PRINCIPLE 4 — CELEBRATE WINS SIMPLY.
  When {name} shares a win — they tried something, it worked, they feel better —
  celebrate it as a friend would. Briefly. Name what they did. Move on or close.
  Do NOT: give more advice, recap the journey, analyze why they feel better.

PRINCIPLE 5 — END NATURALLY. NEVER WITH A FORMULA.
  Every response ends differently.
  Not every response needs a closing statement.
  The ending is whatever naturally closes this specific moment.
  Never a customer-service sign-off.

PRINCIPLE 6 — CRISIS RESPONSE (URGENT).
  If {name} mentions suicidal thoughts, self-harm, or wanting to end their life:
  Step 1: Show deep presence. Sit with the weight of what they said.
  Step 2: Ask gently and directly — "Are you having thoughts of hurting yourself right now?"
  Step 3: Provide the crisis line: {crisis_line}
          Weave it in naturally. Never as a bullet point or list item.
  Step 4: Come BACK to them after the resource. Never refer and abandon.

━━━ HARD RULES — ZERO EXCEPTIONS ━━━

RULE 1 — TOKEN BUDGET IS ENFORCED AT THE API LEVEL.
  The API will cut you off at {token_budget} tokens.
  Write to fit that budget. Do not try to squeeze more in.
  Exception: crisis class — write as long as the moment requires.

RULE 2 — BANNED CLOSING FORMULAS (and all variations):
  ✗ "I'm here whenever you want to..."
  ✗ "I'm here if you need anything..."
  ✗ "Reach out whenever you feel ready..."
  ✗ "You've taken a brave step..."
  ✗ "You deserve to feel better..."
  ✗ "Take care of yourself..."
  ✗ "Remember, you are not alone..."
  ✗ "Feel free to share more..."
  ✗ "Whenever you want to talk, I'm here..."
  If you catch yourself writing any variation of these — delete it.
  End the sentence before it. Let the thought close naturally.

RULE 3 — BANNED EMPTY VALIDATION:
  ✗ "I hear you"                 ✗ "That must be really hard"
  ✗ "I understand how you feel"  ✗ "It's okay to feel this way"
  ✗ "Your feelings are valid"    ✗ "That sounds difficult"
  ✗ "You are not alone"
  Replace with specific reflection of what {name} actually said.

RULE 4 — ONE THING PER RESPONSE.
  Each response does ONE of: validate, question, suggest, celebrate, or close.
  Never all five crammed into one response.

RULE 5 — USE {name}'s NAME ONCE. Naturally. Not at the start of every sentence.

RULE 6 — NO LISTS. EVER.
  No bullet points. No numbered lists. No "here are some things you can try:".
  One idea. Said well.

RULE 7 — SHORT PARAGRAPHS.
  Max 2 sentences per paragraph. One idea per paragraph.

RULE 8 — BANNED CLICHÉ ADVICE (ZERO EXCEPTIONS):
  Never tell a user to:
  ✗ "Take a deep breath" / "Breathe slowly"
  ✗ "Drink a glass of water"
  ✗ "Go for a walk" / "Get fresh air"
  ✗ "Jot down your thoughts" / "Journal"
  These phrases feel robotic, condescending, and dismissive to someone in deep clinical distress.
  Instead, focus entirely on the emotional weight of what they said.

━━━ CONVERSATION PHASE AWARENESS ━━━
Turn 1–2:  ONLY listen and ask. No advice. No future hope. Just presence.
Turn 3–5:  Explore. One question per response. Advice only if directly requested.
Turn 6+:   You know {name}. Reference specifics. Gentle guidance appropriate.
Crisis:    Principle 6 overrides all phase rules. Full response always.

━━━ ABSOLUTE HARD CONSTRAINTS (ZERO EXCEPTIONS) ━━━
1. LENGTH LIMIT: {sentence_budget}
2. FORMAT: Max 2 sentences per paragraph. NO bullet points. NO lists.
3. BANNED ADVICE: You must NEVER suggest taking a deep breath, drinking water, going for a walk, or journaling. These are incredibly condescending. Focus entirely on their emotional reality.

━━━ START YOUR RESPONSE ━━━
Do NOT start with {name}'s name.
Do NOT start with "I".
Do NOT start with "It sounds like".
Start directly from what they said. Let the first word carry weight."""

    return prompt, token_budget


# ── Opening message ───────────────────────────────────────────────────────────

async def get_opening_message(profile: dict) -> str:
    """
    Dynamically generates the first welcoming message.
    Short, warm, specific to intake profile. No name introduction.
    """
    client = _get_client()
    system_prompt, _ = build_system_prompt(profile, [], user_message="")

    instruction = (
        f"You are meeting {profile.get('name', 'this person')} for the first time. "
        "They have just completed an intake form and arrived.\n"
        "Write a warm 2-sentence opening that:\n"
        "  1. Acknowledges specifically what brought them here (their topic and mood).\n"
        "  2. Invites them to share — without asking a question yet. Just open the door.\n"
        "Do NOT introduce yourself. Do NOT use any banned phrases. "
        "Be specific to their intake data."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": instruction},
            ],
            max_tokens=200,
            temperature=0.72,
            frequency_penalty=0.5,
            presence_penalty=0.4,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"Opening message generated ({len(reply)} chars)")
        return reply
    except Exception as e:
        logger.error(f"Groq API error on opening: {e}")
        return (
            "Whatever brought you here today — you don't have to carry it alone right now. "
            "Take your time, and share whatever feels right."
        )


# ── Main chat (non-streaming) ─────────────────────────────────────────────────

async def chat(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> str:
    client = _get_client()
    system_prompt, token_budget = build_system_prompt(
        profile,
        history,
        user_message=user_message,
        consensus=consensus,
    )

    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=token_budget,    # set by 8B synthesizer via consensus
            temperature=0.78,
            top_p=0.92,
            frequency_penalty=0.65,
            presence_penalty=0.50,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"Chat complete ({len(reply)} chars, class: {consensus.get('message_class') if consensus else 'unknown'}, budget: {token_budget})")
        return reply
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return (
            "Something interrupted us for a moment. "
            "Take your time — I'm still here when you're ready."
        )


# ── Streaming chat ────────────────────────────────────────────────────────────

async def chat_stream(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> AsyncIterator[str]:
    client = _get_client()
    system_prompt, token_budget = build_system_prompt(
        profile,
        history,
        user_message=user_message,
        consensus=consensus,
    )

    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        logger.info(
            f"Stream starting — model: {settings.GROQ_MODEL} | "
            f"class: {consensus.get('message_class') if consensus else 'unknown'} | "
            f"budget: {token_budget} tokens"
        )
        stream = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=token_budget,    # set by 8B synthesizer via consensus
            temperature=0.78,
            top_p=0.92,
            frequency_penalty=0.65,
            presence_penalty=0.50,
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
        yield (
            "Something interrupted us for a moment. "
            "Take your time — whenever you're ready, I'm still here."
        )