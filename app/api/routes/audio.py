"""
Audio API route.
Handles Speech-to-Text via Groq's whisper model.
"""

from fastapi import APIRouter, UploadFile, File, HTTPException
from app.core.config import get_settings
from app.core.logger import get_logger
import httpx

logger = get_logger(__name__)
router = APIRouter(prefix="/api/audio", tags=["Audio"])

@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Accepts an audio file upload from the frontend and sends it to Groq's
    whisper-large-v3 model for transcription. Returns the text.
    """
    settings = get_settings()

    if not file.filename.endswith(('.webm', '.m4a', '.mp3', '.mp4', '.mpeg', '.mpga', '.wav')):
        raise HTTPException(status_code=400, detail="Unsupported audio format")

    logger.info(f"Audio — Received transcription request (size: {file.size} bytes)")

    try:
        content = await file.read()
        
        # Send directly directly to Groq's Audio API using httpx
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}"
        }
        
        files = {
            "file": (file.filename, content, file.content_type)
        }
        
        data = {
            "model": "whisper-large-v3",
            "response_format": "json"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, files=files, data=data, timeout=30.0)
            response.raise_for_status()
            
            result = response.json()
            transcribed_text = result.get("text", "").strip()
            
            logger.info(f"Audio — Transcription success: '{transcribed_text[:40]}...'")
            return {"text": transcribed_text}
            
    except httpx.HTTPError as e:
        logger.error(f"Audio — Groq API error: {e}")
        raise HTTPException(status_code=502, detail="Failed to contact transcription service")
    except Exception as e:
        logger.error(f"Audio — Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
