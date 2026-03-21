from app.core.logger import get_logger

logger = get_logger(__name__)
logger.debug("Loading core constants")

# Country-specific crisis lines
CRISIS_LINES = {
    "IN": "iCall (India): 9152987821",
    "US": "988 Suicide & Crisis Lifeline: call or text 988",
    "UK": "Samaritans: 116 123",
    "AU": "Lifeline Australia: 13 11 14",
    "CA": "Crisis Services Canada: 1-833-456-4566",
    "default": "your local crisis line (search 'crisis helpline [your country]')",
}