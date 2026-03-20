"""
Mental Health Chatbot — FastAPI Application Entry Point.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.routes.assessment import router as assessment_router
from app.api.routes.chat import router as chat_router
from app.api.routes.resources import router as resources_router
from app.api.routes.audio import router as audio_router
from app.api.schemas.response import HealthResponse
from app.core.logger import get_logger

logger = get_logger(__name__)

# -- Create App --
app = FastAPI(
    title="Mental Health Chatbot",
    description="AI-powered mental health companion with emotion analysis, safety guardrails, and empathetic conversational support.",
    version="1.0.0",
)

# -- CORS Middleware --
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Mount Static Files (Frontend) --
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# -- Register Routers --
app.include_router(assessment_router)
app.include_router(chat_router)
app.include_router(resources_router)
app.include_router(audio_router)


# -- Root: Serve Frontend --
@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Serves the main frontend page."""
    logger.info("Serving frontend index.html")
    return FileResponse("app/static/index.html")


# -- Health Check --
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Server health check endpoint."""
    logger.info("Health check — OK")
    return HealthResponse()


# -- Startup Event --
@app.on_event("startup")
async def startup_event():
    """Logs application startup."""
    logger.info("=" * 60)
    logger.info("Mental Health Chatbot starting up...")
    logger.info("=" * 60)
