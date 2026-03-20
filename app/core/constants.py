"""
Application constants: assessment questions, crisis resources,
high-risk keywords, and intent labels.
"""

# ---------------------------------------------------------------------------
# Predefined Assessment Questions (PHQ-2 / GAD-2 inspired)
# These are shown at the start of the conversation to establish a baseline.
# ---------------------------------------------------------------------------
ASSESSMENT_QUESTIONS = [
    {
        "id": "q1_safety",
        "text": "Are you currently having thoughts of hurting yourself or ending your life?",
        "type": "choice",
        "options": ["No", "Sometimes", "Yes"],
        "is_critical": True,  # If "Yes" → immediate crisis escalation
    },
    {
        "id": "q2_depressed",
        "text": "Over the last 2 weeks, how often have you felt down, depressed, or hopeless?",
        "type": "choice",
        "options": ["Not at all", "Several days", "More than half the days", "Nearly every day"],
    },
    {
        "id": "q3_anxious",
        "text": "How often have you been feeling nervous, anxious, or on edge?",
        "type": "choice",
        "options": ["Not at all", "Several days", "More than half the days", "Nearly every day"],
    },
    {
        "id": "q4_source",
        "text": "What has been the primary source of your stress or concern recently?",
        "type": "text",
    },
]

# ---------------------------------------------------------------------------
# High-Risk Keywords for Safety Pre-Screening
# ---------------------------------------------------------------------------
HIGH_RISK_KEYWORDS = [
    "kill myself",
    "end my life",
    "want to die",
    "suicide",
    "suicidal",
    "overdose",
    "slit my wrists",
    "hang myself",
    "jump off",
    "no reason to live",
    "better off dead",
    "can't go on",
    "end it all",
    "not worth living",
    "take my own life",
    "self-harm",
    "hurt myself",
    "cutting myself",
]

# ---------------------------------------------------------------------------
# Intent Labels (used by the LLM Intent Classifier)
# ---------------------------------------------------------------------------
INTENT_LABELS = [
    "suicidal ideation",
    "self-harm",
    "depression",
    "anxiety",
    "panic attack",
    "loneliness",
    "grief",
    "trauma / PTSD",
    "burnout / work stress",
    "relationship issues",
    "substance abuse",
    "general stress",
    "positive / coping",
]

# ---------------------------------------------------------------------------
# Crisis Resources
# ---------------------------------------------------------------------------
CRISIS_RESOURCES = [
    {
        "name": "988 Suicide & Crisis Lifeline",
        "contact": "Call or text 988",
        "description": "Free, 24/7 support for people in distress.",
    },
    {
        "name": "Crisis Text Line",
        "contact": "Text HOME to 741741",
        "description": "Free crisis counseling via text message.",
    },
    {
        "name": "SAMHSA National Helpline",
        "contact": "1-800-662-4357",
        "description": "Free referrals and information, 24/7.",
    },
    {
        "name": "International Association for Suicide Prevention",
        "contact": "https://www.iasp.info/resources/Crisis_Centres/",
        "description": "Find a crisis center in your country.",
    },
]

# ---------------------------------------------------------------------------
# Harmful Output Keywords (for LLM Post-Screening)
# ---------------------------------------------------------------------------
HARMFUL_OUTPUT_KEYWORDS = [
    "here is how to",
    "steps to end",
    "method to kill",
    "way to die",
    "you should hurt yourself",
    "instructions for",
    "easy way to",
    "painless way",
]

# ---------------------------------------------------------------------------
# Safe Fallback Response (used when post-screen detects harmful LLM output)
# ---------------------------------------------------------------------------
SAFE_FALLBACK_RESPONSE = (
    "I want you to know that you are not alone, and your feelings matter deeply. "
    "I'm here for you and I'm listening. "
    "Would you like to talk about what you're going through right now?"
)
