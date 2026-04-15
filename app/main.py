import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logger import get_logger
from app.api.routes import chat, audio, assessment, human, user

logger = get_logger("main")
settings = get_settings()


# ── Lifespan: pre-warm the emotion model on startup ──────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MindBridge starting up...")
    
    # 1. Connect to MongoDB
    from app.core.database import connect_to_mongo, close_mongo_connection
    await connect_to_mongo()
    
    # 2. Warm up the HuggingFace emotion model in a background thread
    loop = asyncio.get_event_loop()
    try:
        from app.services.emotion import warmup
        await loop.run_in_executor(None, warmup)
    except Exception as e:
        logger.warning(f"Model warmup skipped: {e}")
        
    # 3. Start Global 35-minute Inactivity Watchdog
    try:
        loop.create_task(human.inactivity_watchdog())
    except Exception as e:
        logger.error(f"Failed to start watchdog: {e}")

    logger.info("MindBridge ready.")
    yield
    logger.info("MindBridge shutting down.")
    await close_mongo_connection()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routers ───────────────────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(audio.router)
app.include_router(assessment.router)
app.include_router(human.router)
app.include_router(user.router)

# ── Static files (UI) ─────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/", include_in_schema=False)
async def serve_ui():
    logger.info("Serving UI index.html")
    return FileResponse("app/static/index.html")

@app.get("/health")
async def health():
    logger.info("Health check endpoint hit")
    return {"status": "ok", "app": settings.APP_NAME}