# ── Conversation Phase Tracker ────────────────────────────────────────────────
CONVERSATION_PHASES = {
    "opening":       (0, 2),
    "exploring":     (3, 5),
    "intervening":   (6, 10),
    "sustaining":    (11, 999),
}

def get_conversation_phase(turn_count: int) -> str:
    for phase, (start, end) in CONVERSATION_PHASES.items():
        if start <= turn_count <= end:
            return phase
    return "sustaining"


# ── Emotion → Response Mode Mapping ──────────────────────────────────────────
EMOTION_MODE_MAP = {
    "sadness":       "validate_then_offer",
    "grief":         "validate_then_offer",
    "remorse":       "validate_then_offer",
    "fear":          "ground_and_stabilise",
    "nervousness":   "ground_and_stabilise",
    "anxiety":       "ground_and_stabilise",
    "anger":         "acknowledge_and_explore_underneath",
    "annoyance":     "acknowledge_and_explore_underneath",
    "disgust":       "acknowledge_and_explore_underneath",
    "hopelessness":  "crisis_presence",
    "despair":       "crisis_presence",
    "loneliness":    "make_specifically_seen",
    "confusion":     "slow_down_and_untangle",
    "embarrassment": "slow_down_and_untangle",
    "joy":           "affirm_and_deepen",
    "excitement":    "affirm_and_deepen",
    "gratitude":     "affirm_and_deepen",
    "admiration":    "affirm_and_deepen",
    "love":          "affirm_and_deepen",
    "neutral":       "curious_exploration",
    "caring":        "curious_exploration",
}

RESPONSE_MODE_INSTRUCTIONS = {
    "validate_then_offer": (
        "Validate the pain first with specific, non-generic language. "
        "Then — depending on how many turns deep you are — begin offering something back: "
        "a normalisation, a gentle reframe, or a small grounding tool. "
        "Do NOT keep the response as pure exploration after turn 3."
    ),
    "ground_and_stabilise": (
        "Name the anxiety/fear explicitly first. Then offer one specific grounding technique: "
        "box breathing (4-4-4-4), 5-4-3-2-1 sensory check, or feet-on-floor awareness. "
        "Keep it simple. One question after."
    ),
    "acknowledge_and_explore_underneath": (
        "Name the anger fully first — do not soften it. "
        "Then gently ask what the anger is protecting: the hurt or fear underneath. "
        "Never rush to calm them."
    ),
    "crisis_presence": (
        "CRISIS MODE. Primary job: make them feel less alone RIGHT NOW — not refer them away.\n"
        "Step 1: Deeply reflect what they're carrying with warmth.\n"
        "Step 2: Ask directly but gently: 'When you say that, are you having thoughts of hurting yourself?'\n"
        "Step 3: If yes — stay with them. Say: 'Your life has weight. I'm not going anywhere.'\n"
        "Step 4: Mention a LOCALLY RELEVANT crisis line. If user is likely Indian: 'iCall (9152987821) has "
        "people trained for exactly this kind of pain.' If country unknown: 'A local crisis line — search "
        "crisis helpline + your country — connects you with someone trained in this specific pain.'\n"
        "Step 5: Come back: 'Right now, I am here. What would help you feel less alone in this moment?'\n"
        "NEVER just paste '988' for someone who is not in the US.\n"
        "NEVER repeat the same crisis referral twice in a row.\n"
        "NEVER refer and abandon — always return to the person after mentioning support."
    ),
    "make_specifically_seen": (
        "Reflect the SPECIFIC texture of their loneliness — not generic. "
        "Then acknowledge the act of reaching out: 'Coming here and saying this took something.' "
        "Ask what connection would look like for them right now."
    ),
    "slow_down_and_untangle": (
        "Name the overwhelm. Help them pick ONE thread. "
        "Don't address everything at once. Simplify and slow down."
    ),
    "affirm_and_deepen": (
        "Anchor the positive moment — don't just acknowledge it. "
        "If they opened up or did something difficult: name the ACT explicitly. "
        "'You just said out loud what you've carried silently — that takes real courage.' "
        "Help them feel the weight of the positive thing."
    ),
    "curious_exploration": (
        "Warm curiosity. Reflect what you heard, then ask one question "
        "that goes one layer deeper into the specifics."
    ),
}


# ── Phase-Based Intervention Instructions ─────────────────────────────────────
PHASE_INSTRUCTIONS = {
    "opening": (
        "PHASE: OPENING (turn 1-2). Focus entirely on making them feel safe and heard. "
        "Pure warm listening. No advice. No techniques yet. End with one open question. "
        "Length: 3-4 sentences."
    ),
    "exploring": (
        "PHASE: EXPLORING (turn 3-5). You understand the broad shape of what they are carrying. "
        "You may now include ONE small normalisation per response — something that helps them feel "
        "less alone or less broken. Examples: "
        "'What you are describing is one of the most exhausting things a person can carry.' "
        "'It makes complete sense you needed to say this out loud.' "
        "Still end with one question. But stop being ONLY questions. "
        "Length: 4-5 sentences."
    ),
    "intervening": (
        "PHASE: INTERVENING (turn 6-10). You know this person now. START GIVING BACK. "
        "Every response MUST contain at least ONE of: "
        "(A) Normalisation: 'This is not weakness. This is what carrying too much alone does to a person.' "
        "(B) Gentle reframe: 'The fact that you are still here, still talking, says something real about you.' "
        "(C) Small practical tool: box breathing (4-4-4-4), 5-4-3-2-1 grounding, or one tiny action for tonight. "
        "(D) Warmth acknowledgment: 'I want you to know that what you shared here matters.' "
        "(E) Strength recognition: noticing something courageous or resilient in what they did or said. "
        "Then still ask ONE question. Make the response feel like something was OFFERED, not just taken. "
        "Length: 5-7 sentences."
    ),
    "sustaining": (
        "PHASE: SUSTAINING (turn 11+). You have a real relationship with this person. Be their anchor. "
        "Every response must: "
        "1. Reference something specific they shared earlier in the conversation. "
        "2. Offer at least one thing they can hold onto — a thought, a small action, or a truth. "
        "3. Help them see any small movement they have made, however tiny. "
        "4. Feel like a warm, consistent, unwavering presence. "
        "End with ONE question that builds on everything they've shared. "
        "Length: 5-8 sentences."
    ),
}


# ── Crisis Signal Keywords ─────────────────────────────────────────────────────
CRISIS_SIGNAL_PHRASES = [
    "want to die", "kill myself", "end my life", "should just end",
    "don't want to be here", "suicide", "no reason to live",
    "better off dead", "want it to stop", "can't go on",
    "not worth living", "disappear forever", "end it all",
    "what's the point of living", "point of living",
    "should end my life", "just end my life",
]

# Self-label words that must always be addressed in the first sentence
SELF_LABEL_WORDS = [
    "useless", "worthless", "failure", "burden", "pathetic",
    "stupid", "loser", "waste", "nothing", "hopeless", "broken",
    "disgusting", "weak", "coward", "ugly", "unlovable",
]

# Country-specific crisis lines
CRISIS_LINES = {
    "IN": "iCall (India): 9152987821",
    "US": "988 Suicide & Crisis Lifeline: call or text 988",
    "UK": "Samaritans: 116 123",
    "AU": "Lifeline Australia: 13 11 14",
    "CA": "Crisis Services Canada: 1-833-456-4566",
    "default": "your local crisis line (search 'crisis helpline [your country]')",
}

# Grounding techniques library
GROUNDING_TECHNIQUES = {
    "box_breathing": (
        "Box breathing: breathe in for 4 counts, hold 4, out 4, hold 4. Repeat 3 times. "
        "This activates the body's calm response within minutes."
    ),
    "5_4_3_2_1": (
        "5-4-3-2-1 grounding: name 5 things you can see, 4 you can touch, "
        "3 you can hear, 2 you can smell, 1 you can taste. "
        "This pulls you into right now and interrupts the spiral."
    ),
    "one_small_thing": (
        "When everything feels impossible: just the next 10 minutes. "
        "Not fixing everything — just one tiny action. Get water. Sit outside. "
        "Text one word to someone. Movement in any direction interrupts the freeze."
    ),
    "self_compassion": (
        "Say to yourself: 'This is a moment of suffering. Suffering is part of being human. "
        "May I be kind to myself right now.' "
        "This is from evidence-based therapy and can be used whenever self-criticism gets loud."
    ),
}


# ── Mood Score Labels ──────────────────────────────────────────────────────────
MOOD_LABELS = {
    range(1, 4):   "really struggling right now",
    range(4, 6):   "going through something genuinely difficult",
    range(6, 8):   "carrying something but getting through",
    range(8, 10):  "doing okay with some things on their mind",
    range(10, 11): "feeling relatively good",
}

def get_mood_label(score: int) -> str:
    for r, label in MOOD_LABELS.items():
        if score in r:
            return label
    return "somewhere in the middle"


# ── Topic Descriptions ─────────────────────────────────────────────────────────
TOPIC_DESCRIPTIONS = {
    "Stress & anxiety":   "stress and anxiety that has been weighing on them",
    "Feeling lonely":     "loneliness and a sense of disconnection",
    "Relationship issues":"something difficult happening in a relationship",
    "Work or studies":    "pressure or struggles with work or studies",
    "Grief or loss":      "grief or loss they are carrying",
    "Just need to talk":  "no specific topic — they just needed someone to talk to",
}