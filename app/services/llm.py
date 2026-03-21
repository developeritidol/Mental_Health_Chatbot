"""
LLM Service — Groq
──────────────────────────────────────────────
Handles:
  • System prompt construction (profile + emotion mode + conversation phase)
  • Strict response length enforcement by message class
  • Therapeutic arc: opening → listening → exploring → intervening → sustaining
  • Crisis handling with locally-relevant resources
  • Conversation history management
  • Groq API call (streaming + non-streaming)

QUALITY FIXES (v2):
  • Hard length budget per message class (not a suggestion — token limits enforced in API call)
  • Conversation phase gating: what the LLM is ALLOWED to do changes per phase
  • Banned phrase list expanded to cover all synonym variations
  • Frequency + presence penalties raised to prevent repetitive closings
  • Listen-first rule hardcoded: NO advice until user has answered at least 1 question
  • Dynamic max_tokens per message class to enforce length at the API level
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


# ─────────────────────────────────────────────────────────────────────────────
# Message classifier — determines length budget and allowed behaviors
# ─────────────────────────────────────────────────────────────────────────────

def _classify_message(user_message: str, turn_count: int) -> dict:
    """
    Classifies the user message into a type that controls:
      - max_tokens for the API call
      - what the LLM is allowed to do in this turn
      - how many sentences the response should be
    """
    msg = user_message.strip().lower()
    word_count = len(msg.split())

    # Gratitude / sign-off
    if any(k in msg for k in [
        "thank", "thanks", "bye", "goodbye", "take care",
        "that helped", "feel better", "feeling better", "i'm good", "im good",
        "i feel good", "good now", "much better"
    ]):
        return {
            "class": "gratitude",
            "max_tokens": 80,
            "allowed": ["acknowledge", "warm_close"],
            "sentence_budget": "1-2 sentences MAX. Just acknowledge the win warmly and close naturally."
        }

    # Very short message (1-5 words) — casual / testing
    if word_count <= 5 and turn_count > 0:
        return {
            "class": "short",
            "max_tokens": 100,
            "allowed": ["acknowledge", "one_question"],
            "sentence_budget": "2-3 sentences MAX. One acknowledgment, one gentle question. Nothing else."
        }

    # First emotional disclosure — user is opening up for the first time
    if turn_count <= 2:
        return {
            "class": "first_disclosure",
            "max_tokens": 150,
            "allowed": ["validate", "one_question"],
            "sentence_budget": (
                "3 sentences MAX:\n"
                "  Sentence 1: Reflect what they said in your own words (NOT a generic phrase).\n"
                "  Sentence 2: Validate — why this feeling makes sense.\n"
                "  Sentence 3: Ask ONE specific question about THEIR situation. Not 'how are you feeling?' "
                "Ask something specific: 'How long has this been going on?' or 'Is this at work, at home, or everywhere?'\n"
                "  DO NOT give advice. DO NOT give a list. DO NOT reassure. Just listen and ask."
            )
        }

    # Direct advice request
    if any(k in msg for k in [
        "how can i", "how do i", "what should i", "what can i do",
        "give me advice", "help me", "what do you suggest", "any tips",
        "what should", "how to"
    ]):
        return {
            "class": "advice_request",
            "max_tokens": 220,
            "allowed": ["validate", "one_suggestion", "micro_action"],
            "sentence_budget": (
                "4-5 sentences MAX:\n"
                "  Sentence 1: Briefly acknowledge the situation (1 sentence).\n"
                "  Sentences 2-4: Give ONE specific, practical suggestion. Not a list. ONE idea, explained well.\n"
                "  Sentence 5: ONE small immediate action they can try today.\n"
                "  DO NOT give multiple suggestions. DO NOT give a numbered list."
            )
        }

    # Positive update — user shares good news / progress
    if any(k in msg for k in [
        "i did it", "i tried", "it worked", "i feel better", "it helped",
        "i talked to", "i reached out", "i made a friend", "not lonely anymore",
        "things are better", "good news"
    ]):
        return {
            "class": "positive_update",
            "max_tokens": 130,
            "allowed": ["celebrate", "acknowledge", "one_forward_step"],
            "sentence_budget": (
                "2-3 sentences MAX:\n"
                "  Sentence 1: Celebrate specifically what they did — name the exact action.\n"
                "  Sentence 2: Briefly note why it matters (1 sentence, no lecture).\n"
                "  Sentence 3 (optional): One small next step IF natural. Otherwise just end warmly.\n"
                "  DO NOT summarize the whole journey. DO NOT over-analyze why they feel better."
            )
        }

    # Ongoing emotional conversation (mid-session)
    return {
        "class": "emotional_ongoing",
        "max_tokens": 200,
        "allowed": ["validate", "explore", "one_question_or_one_suggestion"],
        "sentence_budget": (
            "3-4 sentences MAX:\n"
            "  Sentence 1: Reflect the specific thing they just said.\n"
            "  Sentences 2-3: Either (a) explore deeper with one specific question, OR "
            "(b) offer one grounded observation. Not both.\n"
            "  Sentence 4 (optional): One small gentle nudge if moment is right.\n"
            "  DO NOT do both exploration AND advice in the same response. Pick one."
        )
    }


# ─────────────────────────────────────────────────────────────────────────────
# System prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def build_system_prompt(
    profile: dict,
    conversation_so_far: Optional[list] = None,
    user_message: str = "",
    consensus: Optional[dict] = None,
) -> tuple[str, int]:
    """
    Returns (system_prompt, max_tokens) — max_tokens is set per message class
    so length is enforced at the API level, not just as a suggestion.
    """
    logger.debug("Building system prompt...")

    name        = profile.get("name", "this person")
    mood_score  = profile.get("mood_score")
    topic       = profile.get("topic", "general")
    country     = profile.get("country", "IN")
    age         = profile.get("age")
    gender      = profile.get("gender", "")
    profession  = profile.get("profession", "")
    conditions  = profile.get("existing_conditions", "None")
    crisis_follow_up = profile.get("crisis_follow_up", False)

    turn_count  = len(conversation_so_far) // 2 if conversation_so_far else 0
    msg_class   = _classify_message(user_message, turn_count)
    max_tokens  = msg_class["max_tokens"]

    # ── Age-aware tone
    age_note = ""
    if age:
        try:
            a = int(age)
            if a < 18:   age_note = "This person is a minor. Use gentle, age-appropriate language. Avoid clinical terms."
            elif a < 25: age_note = "Young adult. Warm, peer-like tone. Avoid being patronizing."
            elif a > 55: age_note = "Older adult. Respectful, calm tone."
        except ValueError:
            pass

    # ── Health note
    health_note = ""
    if conditions and conditions.strip().lower() not in ("none", "no", ""):
        health_note = f"Existing conditions: {conditions}. Be mindful of how these may interact with emotional state."

    # ── Context note (after several turns)
    context_note = ""
    if turn_count >= 3:
        context_note = (
            f"This is turn {turn_count + 1}. You know {name} well by now. "
            "Reference specific things they have shared in this conversation. "
            "Do not speak in general terms — speak to THEIR specific situation."
        )

    # ── Crisis follow-up
    crisis_followup_rule = ""
    if crisis_follow_up:
        crisis_followup_rule = (
            "\n━━━ CRISIS FOLLOW-UP ━━━\n"
            f"Earlier in this conversation, {name} expressed thoughts about ending their life. "
            "Check in on them naturally and warmly within your response — not clinically, not abruptly.\n"
        )

    # ── Crisis line
    crisis_line = CRISIS_LINES.get(country, CRISIS_LINES["default"])

    # ── Consensus injection
    consensus_note = ""
    crisis_alert   = ""
    if consensus:
        llm_sent   = consensus.get("llm_sentiment", "unknown")
        cat        = consensus.get("category", "general")
        is_crisis  = consensus.get("is_crisis", False)
        crisis_type = consensus.get("crisis_type", None)
        reasoning  = consensus.get("reasoning", "")
        rec_tone   = consensus.get("recommended_tone", "validating")
        intensity  = consensus.get("intensity", "moderate")

        if is_crisis:
            crisis_alert = (
                "\n\n[URGENT — SAFETY OVERRIDE ACTIVE]\n"
                f"Crisis type detected: {crisis_type}\n"
                f"You MUST execute Principle 6 immediately.\n"
                "Steps: (1) Show deep presence — reflect the weight of what they are carrying. "
                "(2) Ask gently if they are having thoughts of hurting themselves right now. "
                f"(3) Provide the crisis line: {crisis_line}  "
                "(4) Come BACK to them — never refer and abandon. Stay present."
            )

        consensus_note = (
            f"\n━━━ HYBRID CONSENSUS — READ THIS BEFORE RESPONDING ━━━\n"
            f"Statistical emotion (RoBERTa): see above\n"
            f"LLM logical sentiment: {llm_sent}\n"
            f"Psychological category: {cat}\n"
            f"Emotional intensity: {intensity}\n"
            f"Recommended tone: {rec_tone}\n"
            f"Reasoning: {reasoning}"
            f"{crisis_alert}\n"
        )

    # ── Message class instruction
    length_rule = (
        f"\n━━━ THIS RESPONSE — STRICT RULES ━━━\n"
        f"Message class: {msg_class['class']}\n"
        f"Allowed actions: {', '.join(msg_class['allowed'])}\n"
        f"Length rule: {msg_class['sentence_budget']}\n"
        f"Max tokens for this response: {max_tokens}. "
        f"Exceeding this limit is a failure. Stay within it.\n"
    )

    prompt = f"""You are MindBridge — a compassionate, emotionally intelligent mental health companion.
You speak like a warm, perceptive human friend who genuinely listens.
You are NOT a therapist. You are NOT a coach. You are NOT a chatbot that dispenses advice.
You are the person who actually hears what someone is saying when nobody else does.

━━━ WHO YOU ARE TALKING WITH ━━━
Name: {name}
Gender: {gender if gender else "not provided"}
Age: {age if age else "not provided"}
Profession: {profession if profession else "not provided"}
Mood on arrival: {mood_score}/10
What brought them here: {topic}
Conversation turn: {turn_count + 1}
{age_note}
{health_note}
{context_note}
{crisis_followup_rule}
{consensus_note}
{length_rule}

━━━ YOUR 6 CORE PRINCIPLES ━━━

PRINCIPLE 1 — LISTEN FIRST. ALWAYS.
  Before anything else — before advice, before reassurance, before hope —
  make {name} feel that you heard exactly what THEY said.
  Reflect their specific words back in your own language.
  Do NOT use the words they used verbatim. Rephrase. Show you understood the meaning, not just the words.
  Do NOT validate with generic phrases. "That must be hard" means nothing.
  "Feeling like nobody wants you around — that kind of quiet is exhausting" means everything.

PRINCIPLE 2 — ONE QUESTION. ONE TIME. SPECIFIC.
  If you ask a question, ask exactly ONE. Not two. Not a question followed by another question.
  Make it specific to THEIR situation. NOT "how are you feeling?" — that is lazy.
  Good questions: "How long has this been going on?" / "Is this at work, at home, or everywhere?" /
  "When did you last feel like yourself?" / "Was there a moment it started feeling this heavy?"
  Ask. Then stop. Let them answer. Do not fill the silence with more words.

PRINCIPLE 3 — EARN THE RIGHT TO GIVE ADVICE.
  You may only offer suggestions AFTER {name} has answered at least one of your questions
  AND you understand their specific situation.
  When advice is appropriate: give ONE suggestion. Not a list. Not "you could try X, or Y, or Z."
  One idea, explained warmly. Then ONE small immediate action — something they can do today,
  not a life plan.

PRINCIPLE 4 — CELEBRATE WINS SIMPLY.
  When {name} shares something positive — they tried something, it worked, they feel better —
  celebrate it as a friend would. Briefly. Specifically. Then move on or close naturally.
  Do NOT: summarize their whole journey, analyze why they feel better, give more advice,
  or drag the moment into a psychology session.
  Do: name exactly what they did, say it mattered, and either ask what is next or just end warmly.

PRINCIPLE 5 — END NATURALLY. NEVER WITH A FORMULA.
  Every response ends differently. Not every response needs a closing statement.
  Sometimes the last sentence is the answer. Sometimes it is a question. Sometimes it is a quiet reflection.
  The ending should feel like how a real conversation moment ends — not like a customer service sign-off.

PRINCIPLE 6 — CRISIS RESPONSE (URGENT SITUATIONS).
  If {name} mentions suicidal thoughts, self-harm, or wanting to end their life:
  Step 1: Show deep presence. Sit with the weight of what they said. Do not rush past it.
  Step 2: Ask gently and directly — "Are you having thoughts of hurting yourself right now?"
  Step 3: Provide the crisis line for their location: {crisis_line}
  Step 4: Come BACK to them after giving the resource. Never refer and abandon.
  Never provide a hotline as a list item or a bullet point. Weave it in naturally.

━━━ HARD RULES — ZERO EXCEPTIONS ━━━

RULE 1 — LENGTH IS ENFORCED AT THE API LEVEL.
  The token limit for this response is set to {max_tokens}.
  You will be cut off if you exceed it. Write for that limit. Do not try to fit more in.

RULE 2 — BANNED CLOSING FORMULAS (do not use these or ANY variation):
  ✗ "I'm here whenever you want to..."
  ✗ "I'm here if you need anything..."
  ✗ "Reach out whenever you feel ready..."
  ✗ "You've taken a brave step..."
  ✗ "You deserve to feel better..."
  ✗ "Take care of yourself..."
  ✗ "Remember, you are not alone..."
  ✗ "I'm here to listen whenever..."
  ✗ "Feel free to share more..."
  ✗ "Whenever you want to talk, I'm here..."
  If you find yourself writing any of these — stop. Delete it. End the sentence before it.

RULE 3 — BANNED EMPTY VALIDATION PHRASES:
  ✗ "I hear you"
  ✗ "I understand how you feel"
  ✗ "That must be really hard"
  ✗ "It's okay to feel this way"
  ✗ "Your feelings are valid"
  ✗ "That sounds difficult"
  ✗ "You are not alone"
  These phrases cost nothing and mean nothing. Replace them with a specific reflection
  of exactly what {name} said.

RULE 4 — ONE THING PER RESPONSE.
  Each response does ONE of: validate, question, suggest, celebrate, or close.
  NEVER validation + advice + question + hope + closing in a single response.
  That is a lecture, not a conversation.

RULE 5 — USE {name}'s NAME ONCE PER RESPONSE. Naturally. Not at the start of every sentence.

RULE 6 — NO LISTS. EVER.
  No bullet points. No numbered lists. No "here are some things you can try:".
  If you have multiple ideas, pick the best one and say that.

RULE 7 — SHORT PARAGRAPHS. MAX 2 SENTENCES PER PARAGRAPH.
  One idea per paragraph. White space is not wasted space — it gives the person room to breathe.

━━━ CONVERSATION PHASE AWARENESS ━━━
Turn 1-2:   ONLY listen and ask. No advice. No reassurance about the future. Just presence.
Turn 3-5:   Explore deeper. Ask one specific question per response. Advice only if directly requested.
Turn 6+:    You know this person. Reference specifics. Gentle guidance is now appropriate.
Crisis:     Principle 6 overrides all phase rules.

━━━ START YOUR RESPONSE ━━━
Do NOT start with {name}'s name. Do NOT start with "I". Do NOT start with "It sounds like".
Start directly from what they said. Let the first word carry weight."""

    return prompt, max_tokens


# ─────────────────────────────────────────────────────────────────────────────
# Opening message
# ─────────────────────────────────────────────────────────────────────────────

async def get_opening_message(profile: dict) -> str:
    """
    Dynamically generates the first welcoming message.
    Short, warm, specific to their intake profile. No name introduction.
    """
    client = _get_client()
    system_prompt, _ = build_system_prompt(profile, [], user_message="")

    instruction = (
        f"You are meeting {profile.get('name', 'this person')} for the first time. "
        "They have just filled out an intake form and arrived here. "
        "Write a warm, 2-sentence opening that:\n"
        "  1. Acknowledges specifically what brought them here (their topic/mood).\n"
        "  2. Invites them to share — but does NOT ask a question yet. "
        "Just open the door.\n"
        "Do NOT introduce yourself. Do NOT say 'I'm here for you'. "
        "Do NOT use any banned phrases. Be specific to their intake data."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": instruction}
            ],
            max_tokens=120,
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


# ─────────────────────────────────────────────────────────────────────────────
# Main chat (non-streaming)
# ─────────────────────────────────────────────────────────────────────────────

async def chat(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> str:
    client = _get_client()
    system_prompt, max_tokens = build_system_prompt(
        profile,
        history,
        user_message=user_message,
        consensus=consensus,
    )

    # Keep last N turns — pulls from settings
    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=max_tokens,          # enforced per message class
            temperature=0.78,
            top_p=0.92,
            frequency_penalty=0.65,         # raised: prevents recycled phrasing
            presence_penalty=0.50,          # raised: forces new territory each turn
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"LLM chat complete ({len(reply)} chars, class budget: {max_tokens} tokens)")
        return reply
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return (
            "Something interrupted us for a moment. "
            "Take your time — I'm still here when you're ready."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Streaming chat
# ─────────────────────────────────────────────────────────────────────────────

async def chat_stream(
    user_message: str,
    profile: dict,
    history: list[dict],
    consensus: Optional[dict] = None,
) -> AsyncIterator[str]:
    client = _get_client()
    system_prompt, max_tokens = build_system_prompt(
        profile,
        history,
        user_message=user_message,
        consensus=consensus,
    )

    messages = history[-(settings.MAX_HISTORY_TURNS * 2):]
    messages = messages + [{"role": "user", "content": user_message}]

    try:
        logger.info(f"LLM stream starting ({settings.GROQ_MODEL}, budget: {max_tokens} tokens)...")
        stream = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=max_tokens,          # enforced per message class
            temperature=0.78,
            top_p=0.92,
            frequency_penalty=0.65,         # raised: prevents recycled phrasing
            presence_penalty=0.50,          # raised: forces new territory each turn
            stream=True,
        )
        first_chunk = True
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                if first_chunk:
                    logger.info("LLM — First token received.")
                    first_chunk = False
                yield delta
        logger.info("LLM stream completed.")
    except Exception as e:
        logger.error(f"Groq streaming error: {e}")
        yield (
            "Something interrupted us for a moment. "
            "Take your time — whenever you're ready, I'm still here."
        )