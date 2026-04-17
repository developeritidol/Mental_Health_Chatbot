# MindBridge — Mental Health Chatbot API

MindBridge is an advanced, AI-powered mental health chatbot backend built with **FastAPI**. It leverages multiple state-of-the-art AI models to provide empathetic support, structured emotion tracking, and a robust real-time human intervention system for crisis situations.

The primary client for this backend is an Android mobile application, but it can be integrated with any platform supporting HTTP and WebSockets.

---

## 🌟 Core Architecture & Features

The system runs a **Multi-Model Pipeline** for every message:

1. **Emotion Analysis (Local HF Model)** 
   - Uses `SamLowe/roberta-base-go_emotions` (running locally via PyTorch/Transformers) to detect 28 distinct emotions and extract a `sadness` score.
2. **Safety Consensus Synthesizer (Llama 3 via Groq)**
   - Fast, low-latency evaluation of the user's message + the RoBERTa emotion score to detect if the user is in an **active crisis** (e.g., self-harm ideation).
3. **Empathetic Generation (GPT-4o via OpenAI)**
   - Generates contextual, empathetic responses using MongoDB's RAG memory to maintain long-term memory. The response is streamed back to the client via **Server-Sent Events (SSE)**.
4. **Real-time Human Escalation (WebSockets)**
   - If `is_crisis` is triggered, the AI is immediately blocked. The server routes the user to a real-time **WebSocket** room.
   - A human therapist can join the room from an Admin dashboard to conduct the intervention. If no human joins within a 3-minute timeout, a fallback crisis helpline message is delivered, and the AI is re-enabled to ensure the user is not abandoned.

---

## ⚙️ Tech Stack

- **Framework:** FastAPI (Python 3.11+)
- **Database:** MongoDB (Motor Async Driver for Python)
- **Live Chat:** WebSockets (FastAPI built-in / Starlette `websockets`)
- **AI Models:** 
  - OpenAI (GPT-4o for generation, Text-Embeddings for RAG)
  - Groq (Whisper for audio STT, Llama-3-8B for safety consensus)
  - HuggingFace (RoBERTa for emotion classification)

---

## 🛠️ Step-by-Step Setup Guide

### 1. Requirements

- Python 3.11 or newer
- MongoDB instance (local or Atlas)
- Docker Desktop (Optional, for containerized deployment)

### 2. Installation

Clone the project and set up a virtual environment:

```bash
# Set up virtual environment
python -m venv .venv
source .venv/bin/activate      # On Windows: .venv\Scripts\Activate

# Install python dependencies from the updated requirements.txt
pip install -r requirements.txt
```

### 3. Environment Variables

Create a `.env` file in the root of the project (copy from `.env_example` if available). Fill in your actual credentials:

```dotenv
APP_NAME="MindBridge"
DEBUG=True

# Server settings
SERVER_HOST="localhost"
SERVER_PORT=8000

# Database
MONGODB_URL="mongodb://localhost:27017"
DATABASE_NAME="mindbridge_db"

# AI API Keys
OPENAI_API_KEY="sk-..."       # For GPT-4o text and embeddings
GROQ_API_KEY="gsk-..."        # For Whisper audio & Llama-3 safety
HF_API_TOKEN="..."            # Optional for HuggingFace fallback

# Optional: Models
MAIN_MODEL="gpt-4o"
SYNTHESIZER_MODEL="gpt-4o-mini"
```

### 4. Running the Application Locally

Start the uvicorn development server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
*Note: On startup, the HuggingFace RoBERTa model will be downloaded automatically (~500MB) and pre-warmed for fast CPU inference.*

### 5. Docker Deployment

If deploying to production, use Docker Compose:

```bash
docker-compose up -d --build
```
This will containerize the application and start it alongside any configured database services in `docker-compose.yml`.

---

## 📡 API Overview

### Android Client APIs
- **`POST /api/chat/stream`**: Main entrypoint. Sends messages, returns SSE text chunks. Returns a `{"type": "escalation_required"}` JSON packet if a crisis triggers human handoff.
- **`GET /api/chat/sessions/{device_id}`**: Load all active/past sessions for a user.
- **`GET /api/chat/history/{session_id}`**: Load full message history to resume a chat.
- **`WS /ws/human/{session_id}?role=user`**: Real-time websocket when escalation occurs.

### Human Dashboard APIs
- **`GET /ws/escalated`**: See queue of active crisis sessions.
- **`GET /ws/escalated/{session_id}/messages`**: Pre-read history before handling the crisis.
- **`WS /ws/human/{session_id}?role=human_counselor&counselor_name=Dr.%20Smith`**: Join live crisis room.
- **`POST /ws/escalated/{session_id}/close`**: Conclude intervention and revert the session back to the AI.

---

## 🔐 Privacy & Security
All conversations are mapped dynamically to `device_id` and randomly generated session UUIDs. Embeddings generated for RAG memory are strictly partitioned using MongoDB Vector Search queries bounded by the same `device_id`.
