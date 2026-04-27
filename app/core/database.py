import logging
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class DatabaseManager:
    client: AsyncIOMotorClient = None
    db = None

db_manager = DatabaseManager()

async def connect_to_mongo():
    logger.info("Connecting to MongoDB...")
    # Explicitly use certifi to resolve Docker/Atlas TLSV1_ALERT_INTERNAL_ERROR bugs
    db_manager.client = AsyncIOMotorClient(settings.MONGODB_URL, tlsCAFile=certifi.where())
    db_manager.db = db_manager.client[settings.DATABASE_NAME]
    
    # Ensure Indexes for performance
    try:
        # Remove any null user_id values so sparse unique index works correctly.
        await db_manager.db.users.update_many({"user_id": None}, {"$unset": {"user_id": ""}})

        try:
            await db_manager.db.users.drop_index("user_id_1")
        except Exception:
            pass

        await db_manager.db.users.create_index(
            "user_id",
            unique=True,
            sparse=True,
        )
        await db_manager.db.sessions.create_index("session_id", unique=True)
        await db_manager.db.sessions.create_index("user_id")
        await db_manager.db.messages.create_index("session_id")
        await db_manager.db.messages.create_index([("session_id", 1), ("timestamp", 1)])
        await db_manager.db.token_blacklist.create_index("token_hash", unique=True)
        await db_manager.db.token_blacklist.create_index("expires_at", expireAfterSeconds=0)
        logger.info("MongoDB connected and indexes verified.")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")

async def close_mongo_connection():
    logger.info("Closing MongoDB connection...")
    if db_manager.client:
        db_manager.client.close()
        logger.info("MongoDB connection closed.")

def get_database():
    return db_manager.db
