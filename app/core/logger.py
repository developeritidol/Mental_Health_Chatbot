"""
Logging configuration for the Mental Health Chatbot.
Provides a centralized logger with both console and file handlers.
Log files are stored in the /logs directory at the project root.
"""

import logging
import os
from datetime import datetime


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, f"app_{datetime.now().strftime('%Y-%m-%d')}.log")


def get_logger(name: str) -> logging.Logger:
    """
    Returns a configured logger instance for the given module name.
    
    Args:
        name: Typically __name__ from the calling module.
    
    Returns:
        A logging.Logger with console + file handlers.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # -- Formatter --
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # -- Console Handler (INFO and above) --
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # -- File Handler (DEBUG and above, daily rotation by filename) --
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
