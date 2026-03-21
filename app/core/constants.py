# ── Emotion → Response Mode Mapping ─────────────────────────────────────────
# Maps dominant GoEmotions labels to a conversation strategy.
# The strategy name is injected into the system prompt to guide the LLM.

EMOTION_MODE_MAP = {
    # Deep distress
    "sadness":      "validate_and_sit_with",
    "grief":        "validate_and_sit_with",
    "remorse":      "validate_and_sit_with",

    # Fear / anxiety
    "fear":         "ground_and_stabilise",
    "nervousness":  "ground_and_stabilise",
    "anxiety":      "ground_and_stabilise",

    # Anger
    "anger":        "acknowledge_and_explore_underneath",
    "annoyance":    "acknowledge_and_explore_underneath",
    "disgust":      "acknowledge_and_explore_underneath",

    # Hopelessness — elevated watchfulness
    "hopelessness": "gentle_watchful_presence",
    "despair":      "gentle_watchful_presence",

    # Loneliness / disconnection
    "loneliness":   "make_specifically_seen",

    # Confusion / overwhelm
    "confusion":    "slow_down_and_untangle",
    "embarrassment":"slow_down_and_untangle",

    # Positive / neutral
    "joy":          "affirm_and_deepen",
    "excitement":   "affirm_and_deepen",
    "gratitude":    "affirm_and_deepen",
    "neutral":      "curious_exploration",
    "caring":       "curious_exploration",
}

RESPONSE_MODE_INSTRUCTIONS = {
    "validate_and_sit_with": (
        "They are in real pain right now. Your only job is to sit with them — "
        "not fix, not reframe, not offer hope yet. Reflect the exact weight of what they said. "
        "Be slow. Be specific. Ask one gentle question about what this is like for them."
    ),
    "ground_and_stabilise": (
        "They are anxious or afraid. Bring them gently into the present moment. "
        "Be calm and steady. Don't escalate the topic — focus on right now, what they can feel and hear. "
        "Ask one grounding question or offer one small calming anchor."
    ),
    "acknowledge_and_explore_underneath": (
        "They are angry or frustrated. Don't try to calm the anger — name it, validate it fully first. "
        "Anger almost always has hurt or fear underneath it. Once they feel heard, gently explore what's under the surface. "
        "Never dismiss or minimise the anger."
    ),
    "gentle_watchful_presence": (
        "They are expressing hopelessness or despair. Be an exceptionally warm, steady presence. "
        "Do NOT immediately suggest resources or hotlines — build safety through connection first. "
        "Ask small, caring, curious questions that invite them to stay in the conversation. "
        "Gently distinguish between exhaustion and something more urgent, through the conversation itself."
    ),
    "make_specifically_seen": (
        "They are feeling lonely or disconnected. The most powerful thing you can do is make them feel "
        "SPECIFICALLY seen — not 'I understand loneliness' but reflecting back the unique details of their situation. "
        "Notice the small things they said. Make them feel like a person, not a category."
    ),
    "slow_down_and_untangle": (
        "They are overwhelmed or confused. Slow the pace. Don't address everything at once. "
        "Help them pick one thread to follow. Simplify gently. One thing at a time."
    ),
    "affirm_and_deepen": (
        "They are in a relatively positive or grateful place. Affirm this genuinely. "
        "Don't just validate surface-level positivity — deepen it. Ask what's contributing to this feeling. "
        "Help them understand and anchor the good."
    ),
    "curious_exploration": (
        "Approach with warm, open curiosity. Ask one meaningful question that helps them go a little deeper "
        "into whatever they've shared. Don't assume you know how they feel — explore it with them."
    ),
}

# ── Crisis Signal Keywords ────────────────────────────────────────────────────
# Used ONLY for backend logging/monitoring — NOT for immediate bot response.
# The LLM handles the conversation naturally; these feed the emotion trend tracker.
CRISIS_SIGNAL_PHRASES = [
    "want to die", "kill myself", "end my life", "don't want to be here",
    "suicide", "no reason to live", "better off dead", "want it to stop",
    "can't go on", "not worth living", "disappear forever",
]

# ── Mood Score Labels ─────────────────────────────────────────────────────────
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

# ── Topic Descriptions ────────────────────────────────────────────────────────
TOPIC_DESCRIPTIONS = {
    "Stress & anxiety":   "stress and anxiety that has been weighing on them",
    "Feeling lonely":     "loneliness and a sense of disconnection",
    "Relationship issues":"something difficult happening in a relationship",
    "Work or studies":    "pressure or struggles with work or studies",
    "Grief or loss":      "grief or loss they are carrying",
    "Just need to talk":  "no specific topic — they just needed someone to talk to",
}