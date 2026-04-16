# Detailed Issues Analysis: MindBridge Mental Health Chatbot

---

## Major Issues

### Issue 1: Hardcoded JWT Secret Key

**Define the Major Issue**: JWT authentication secret key is hardcoded in source code, allowing anyone with repository access to forge authentication tokens and bypass security controls.

**Where is the Issue**: `app/core/config.py`, line 63

**Cause of the Issue**: The SECRET_KEY is defined as a string literal in the Settings class instead of being loaded from environment variables. This is a critical security vulnerability.

**How to Resolve**: Remove the hardcoded value and require the secret key to be provided via environment variable. Add validation to ensure the key is set before the application starts.

**Steps to Resolve**:
1. Open `app/core/config.py`
2. Import `os` at the top of the file
3. Change line 63 from:
   ```python
   SECRET_KEY: str = "RstMdMMoEvxlHLMPeCjCTDKChP_ikTuraTEaLgkiCUI"
   ```
   to:
   ```python
   SECRET_KEY: str = Field(default="", env="JWT_SECRET_KEY")
   ```
4. Add validation method after Settings class:
   ```python
   def __init__(self, **kwargs):
       super().__init__(**kwargs)
       if not self.SECRET_KEY:
           raise ValueError("JWT_SECRET_KEY environment variable must be set")
   ```
5. Update `.env_example` to include:
   ```env
   JWT_SECRET_KEY=generate-strong-random-secret-here
   ```
6. Add JWT_SECRET_KEY to your production environment variables
7. Rotate the existing compromised secret key immediately after deployment

---

### Issue 2: Exposed MongoDB Credentials

**Define the Major Issue**: MongoDB database credentials are exposed in docker-compose.yml with a default value, making them visible to anyone with repository access and potentially compromising all user data.

**Where is the Issue**: `docker-compose.yml`, line 13

**Cause of the Issue**: The MONGODB_URL environment variable has a default value containing actual credentials in the docker-compose configuration file, which is typically committed to version control.

**How to Resolve**: Remove the default value from docker-compose.yml and require explicit configuration via .env file. The credentials should never be in version control.

**Steps to Resolve**:
1. Open `docker-compose.yml`
2. Change line 13 from:
   ```yaml
   - MONGODB_URL=${MONGODB_URL:-mongodb+srv://tejashitidoltechnologies_db_user:somLsHQbY2RxXNGJ@cluster0.bdj4ff0.mongodb.net/?appName=Cluster0}
   ```
   to:
   ```yaml
   - MONGODB_URL=${MONGODB_URL}
   ```
3. Update `.env_example` with:
   ```env
   MONGODB_URL=mongodb+srv://username:password@cluster.mongodb.net/?appName=Cluster0
   ```
4. Rotate the exposed MongoDB credentials immediately:
   - Log into MongoDB Atlas
   - Change the database user password
   - Update the password in your production .env file
5. Ensure .env is in .gitignore (already present)
6. Never commit .env files to version control

---

### Issue 3: Silent Failures in Crisis Detection

**Define the Major Issue**: Critical crisis detection services (emotion analysis and safety consensus) fail silently without alerting, potentially allowing users in crisis to go undetected and unescalated.

**Where is the Issue**: 
- `app/services/emotion.py`, lines 96-97
- `app/services/safety.py`, lines 72-80

**Cause of the Issue**: Exception handlers return fallback values (neutral emotion, safe state) instead of raising errors or triggering alerts. This masks failures in safety-critical systems.

**How to Resolve**: Raise custom exceptions for critical failures and implement an alerting system to notify operators when crisis detection fails.

**Steps to Resolve**:
1. Create `app/core/exceptions.py`:
   ```python
   class CrisisDetectionError(Exception):
       """Raised when crisis detection pipeline fails"""
       pass
   ```
2. Modify `app/services/emotion.py`:
   - Import CrisisDetectionError
   - Replace the except block at line 105-107 with:
     ```python
     except Exception as e:
         logger.error(f"[CRITICAL] Emotion inference failed: {e}")
         # TODO: Send alert to monitoring system
         raise CrisisDetectionError(f"Emotion analysis failed: {e}")
     ```
3. Modify `app/services/safety.py`:
   - Import CrisisDetectionError
   - Replace the except block at lines 72-80 with:
     ```python
     except Exception as e:
         logger.error(f"[CRITICAL] Consensus synthesizer failed: {e}")
         # TODO: Send alert to monitoring system
         raise CrisisDetectionError(f"Safety consensus failed: {e}")
     ```
4. Update `app/api/routes/chat.py` to handle CrisisDetectionError:
   - Add try-except around emotion analysis and consensus calls
   - Return a safe error response to user while logging the failure
5. Set up monitoring alerts for CrisisDetectionError occurrences

---

### Issue 4: Race Condition in Session Management

**Define the Major Issue**: Concurrent requests can create duplicate sessions or use inconsistent session IDs, causing messages to be saved to wrong sessions and fragmenting conversation history.

**Where is the Issue**: `app/api/routes/chat.py`, lines 88-89

**Cause of the Issue**: Session existence check and session creation are separate non-atomic operations. Between checking if a session exists and using its ID, another request can create a new session.

**How to Resolve**: Use MongoDB's atomic find-and-update operation to ensure session creation is atomic and thread-safe.

**Steps to Resolve**:
1. Open `app/services/db_service.py`
2. Add new function:
   ```python
   async def get_or_create_session_atomic(device_id: str) -> str:
       """Atomically get existing session or create new one"""
       db = get_database()
       result = await db.sessions.find_one_and_update(
           {"device_id": device_id},
           {
               "$setOnInsert": {
                   "session_id": str(uuid.uuid4()),
                   "device_id": device_id,
                   "is_active": True,
                   "is_escalated": False,
                   "created_at": datetime.now(timezone.utc),
                   "updated_at": datetime.now(timezone.utc)
               },
               "$set": {"updated_at": datetime.now(timezone.utc)}
           },
           upsert=True,
           return_document=True
       )
       return result["session_id"]
   ```
3. Import uuid at top of db_service.py if not already present
4. Update `app/api/routes/chat.py` line 88-89:
   ```python
   actual_session_id = await get_or_create_session_atomic(req.device_id)
   ```
5. Add tests for concurrent session creation scenarios
6. Deploy and monitor for any duplicate session errors

---

### Issue 5: No Rate Limiting

**Define the Major Issue**: All API endpoints lack rate limiting, making the application vulnerable to denial-of-service attacks, API quota exhaustion, and abuse of crisis escalation features.

**Where is the Issue**: All API routes in `app/api/routes/` directory

**Cause of the Issue**: No rate limiting middleware or decorators have been implemented. This is a missing security feature for production applications.

**How to Resolve**: Implement rate limiting using the slowapi library with appropriate limits for different endpoint types.

**Steps to Resolve**:
1. Add to `requirements.txt`:
   ```
   slowapi==0.1.9
   ```
2. Install the dependency:
   ```bash
   pip install slowapi==0.1.9
   ```
3. Open `app/main.py`
4. Add imports:
   ```python
   from slowapi import Limiter, _rate_limit_exceeded_handler
   from slowapi.util import get_remote_address
   from slowapi.errors import RateLimitExceeded
   ```
5. Initialize limiter after app creation:
   ```python
   limiter = Limiter(key_func=get_remote_address)
   app.state.limiter = limiter
   app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
   ```
6. Add rate limits to critical endpoints in `app/api/routes/chat.py`:
   ```python
   from app.main import limiter

   @router.post("/stream")
   @limiter.limit("10/minute")
   async def stream_message(req: StreamChatRequest, user = Depends(get_current_user)):
   ```
7. Add stricter limits to expensive endpoints:
   - Audio transcription: 5/minute
   - Assessment: 3/minute
8. Test rate limiting with load testing tools
9. Monitor rate limit violations in logs

---

### Issue 6: Startup Blocking on Model Download

**Define the Major Issue**: Application startup is blocked while downloading and loading the 500MB HuggingFace emotion model, causing deployment timeouts and preventing health checks during startup.

**Where is the Issue**: `app/main.py`, line 30

**Cause of the Issue**: Model warmup is executed synchronously in the lifespan context manager before the application becomes available, blocking the entire startup process.

**How to Resolve**: Make model warmup asynchronous and non-blocking, allowing the application to start while the model loads in the background.

**Steps to Resolve**:
1. Open `app/main.py`
2. Modify the lifespan function:
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       logger.info("MindBridge starting up...")
       
       # 1. Connect to MongoDB
       from app.core.database import connect_to_mongo, close_mongo_connection
       await connect_to_mongo()
       
       # 2. Mark model as not ready initially
       app.state.model_ready = False
       
       # 3. Warm up model in background
       async def warmup_background():
           try:
               loop = asyncio.get_event_loop()
               await loop.run_in_executor(None, warmup)
               app.state.model_ready = True
               logger.info("Emotion model ready")
           except Exception as e:
               logger.error(f"Model warmup failed: {e}")
               app.state.model_ready = False
       
       loop.create_task(warmup_background())
       
       # 4. Start watchdog
       try:
           loop.create_task(human.inactivity_watchdog())
       except Exception as e:
           logger.error(f"Failed to start watchdog: {e}")
       
       logger.info("MindBridge ready (model warming in background)")
       yield
       logger.info("MindBridge shutting down.")
       await close_mongo_connection()
   ```
3. Update health check endpoint to include model status:
   ```python
   @app.get("/health")
   async def health():
       return {
           "status": "ok",
           "app": settings.APP_NAME,
           "model_ready": getattr(app.state, "model_ready", False)
       }
   ```
4. Update deployment configurations to wait for health check to pass
5. Monitor model warmup time in logs

---

### Issue 7: Vector Search Assumes Index Exists

**Define the Major Issue**: The vector search for long-term memory retrieval assumes the MongoDB vector index exists, causing silent failures and disabling the RAG feature if the index is missing.

**Where is the Issue**: `app/services/db_service.py`, line 56

**Cause of the Issue**: The code directly uses $vectorSearch without checking if the index exists or handling the case where it's not configured in MongoDB Atlas.

**How to Resolve**: Add error handling to detect missing vector index and implement a fallback to text-based search.

**Steps to Resolve**:
1. Open `app/services/db_service.py`
2. Modify `retrieve_long_term_memory` function:
   ```python
   async def retrieve_long_term_memory(
       device_id: str,
       query_vector: List[float],
       exclude_session_id: str = "",
       limit: int = 4,
   ) -> List[str]:
       if not query_vector:
           return []
       
       db = get_database()
       if db is None:
           return []
       
       # Try vector search first
       try:
           pipeline = [
               {
                   "$vectorSearch": {
                       "index": "messages_vector_index",
                       "path": "embedding",
                       "queryVector": query_vector,
                       "numCandidates": 50,
                       "limit": limit + 2,
                       "filter": {"device_id": device_id},
                   }
               },
               {"$match": {"session_id": {"$ne": exclude_session_id}}},
               {"$limit": limit},
               {"$project": {"role": 1, "content": 1, "_id": 0}},
           ]
           cursor = db.messages.aggregate(pipeline)
           docs = await cursor.to_list(length=limit)
           
           if docs:
               snippets = []
               for doc in docs:
                   role = "User" if doc.get("role") == "user" else "MindBridge"
                   content = doc.get("content", "")[:200].strip()
                   if content:
                       snippets.append(f"{role}: {content}")
               if snippets:
                   logger.info(f"Long-term memory (vector): {len(snippets)} turns retrieved")
                   return snippets
       except Exception as e:
           logger.warning(f"Vector search unavailable, falling back to text search: {e}")
       
       # Fallback: return empty list (could implement text search here)
       logger.info("Long-term memory: vector search unavailable, returning empty")
       return []
   ```
3. Create MongoDB vector index setup script in documentation
4. Test with and without vector index
5. Add monitoring for vector search failures

---

### Issue 8: Unbounded WebSocket Connections

**Define the Major Issue**: No limit on the number of WebSocket connections per device, allowing potential memory exhaustion attacks and resource abuse.

**Where is the Issue**: `app/api/routes/human.py`, line 144

**Cause of the Issue**: The ConnectionManager stores connections in a dictionary without enforcing any maximum limit per device ID.

**How to Resolve**: Add connection limits per device and reject new connections when the limit is exceeded.

**Steps to Resolve**:
1. Open `app/api/routes/human.py`
2. Add constant at top of file:
   ```python
   MAX_CONNECTIONS_PER_DEVICE = 5
   ```
3. Modify the `connect` method in ConnectionManager class:
   ```python
   async def connect(self, device_id: str, ws: WebSocket):
       await ws.accept()
       
       # Enforce connection limit
       if device_id in self.rooms:
           if len(self.rooms[device_id]) >= self.MAX_CONNECTIONS_PER_DEVICE:
               await ws.close(code=1008, reason="Too many connections")
               logger.warning(f"Rejected connection for {device_id}: limit reached")
               return
       
       self.rooms.setdefault(device_id, []).append(ws)
       logger.info(f"[WS] New connection in room '{device_id}'. Total: {len(self.rooms[device_id])}")
   ```
4. Add connection cleanup on disconnect to prevent stale connections
5. Test with multiple concurrent connections
6. Monitor connection counts in production

---

### Issue 9: No Retry Logic for External APIs

**Define the Major Issue**: External API calls to OpenAI, Groq, and MongoDB lack retry logic, causing permanent failures from transient network issues and reducing reliability.

**Where is the Issue**: All external API calls throughout the codebase

**Cause of the Issue**: No retry mechanism has been implemented for network operations, making the application fragile to temporary network failures.

**How to Resolve**: Implement exponential backoff retry logic using the tenacity library for all external API calls.

**Steps to Resolve**:
1. Add to `requirements.txt`:
   ```
   tenacity==8.2.3
   ```
2. Install the dependency:
   ```bash
   pip install tenacity==8.2.3
   ```
3. Create `app/utils/retry.py`:
   ```python
   from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
   import httpx

   @retry(
       stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError))
   )
   async def retry_network_call(func, *args, **kwargs):
       return await func(*args, **kwargs)
   ```
4. Update `app/services/llm.py` to wrap OpenAI calls:
   ```python
   from app.utils.retry import retry_network_call
   
   # Replace direct client calls with:
   response = await retry_network_call(
       client.chat.completions.create,
       model=settings.MAIN_MODEL,
       messages=[...],
       ...
   )
   ```
5. Update `app/services/safety.py` for Groq calls
6. Update database connection logic for retry on connection failures
7. Add logging for retry attempts
8. Test with simulated network failures

---

### Issue 10: Minimal Test Coverage

**Define the Major Issue**: Only a basic smoke test exists, with no unit tests, integration tests, or tests for critical paths like crisis escalation, leaving the application vulnerable to regressions.

**Where is the Issue**: `test_api.py` (18 lines only)

**Cause of the Issue**: No testing infrastructure has been set up, and no test suite has been developed for the application.

**How to Resolve**: Set up pytest with async support and create comprehensive tests for critical paths.

**Steps to Resolve**:
1. Add to `requirements.txt`:
   ```
   pytest==7.4.3
   pytest-asyncio==0.21.1
   pytest-cov==4.1.0
   pytest-mock==3.12.0
   httpx==0.27.2
   ```
2. Install dependencies:
   ```bash
   pip install pytest pytest-asyncio pytest-cov pytest-mock
   ```
3. Create `tests/conftest.py`:
   ```python
   import pytest
   from fastapi.testclient import TestClient
   from app.main import app
   
   @pytest.fixture
   def client():
       return TestClient(app)
   
   @pytest.fixture
   def mock_db():
       # Mock database setup
       pass
   ```
4. Create test structure:
   ```
   tests/
   ├── unit/
   │   ├── test_emotion_service.py
   │   ├── test_safety_service.py
   │   └── test_llm_service.py
   ├── integration/
   │   ├── test_chat_flow.py
   │   └── test_crisis_escalation.py
   └── conftest.py
   ```
5. Write critical path tests:
   - Crisis detection and escalation
   - Session management
   - WebSocket connections
6. Add pytest configuration to `pyproject.toml` or `pytest.ini`
7. Set up CI/CD to run tests automatically
8. Aim for 80% code coverage

---

## Normal Issues

### Issue 1: Missing Documentation

**Define the Normal Issue**: Lack of comprehensive documentation makes it difficult for new developers to onboard and for Android developers to integrate with the API.

**Where is the Issue**: Project root directory

**Cause of the Issue**: Only a README exists. No architecture docs, API guides, deployment guides, or crisis protocol documentation.

**How to Resolve**: Create comprehensive documentation covering architecture, API usage, deployment, and crisis handling procedures.

**Steps to Resolve**:
1. Create `ARCHITECTURE.md` with system design diagrams and data flow
2. Create `API_GUIDE.md` with detailed API examples for Android developers
3. Create `DEPLOYMENT.md` with production deployment checklist and monitoring setup
4. Create `CRISIS_PROTOCOL.md` with step-by-step guide for human counselors
5. Create `SCHEMA.md` with MongoDB collection schemas and index definitions
6. Create `TROUBLESHOOTING.md` with common issues and solutions
7. Create `CONTRIBUTING.md` with development workflow and coding standards
8. Review and update README.md to reference new documentation

---

### Issue 2: Incomplete .env Example

**Define the Normal Issue**: The .env_example file is missing several required environment variables, leading to configuration errors during setup.

**Where is the Issue**: `.env_example`

**Cause of the Issue**: The example file was not updated when new configuration options were added to the application.

**How to Resolve**: Update .env_example to include all required environment variables with descriptive comments.

**Steps to Resolve**:
1. Open `.env_example`
2. Add all missing variables:
   ```env
   # Security
   JWT_SECRET_KEY=generate-strong-random-secret-here
   
   # AI API Keys
   OPENAI_API_KEY=sk-...
   GROQ_API_KEY=gsk-...
   HF_API_TOKEN=hf_...
   
   # Database
   MONGODB_URL=mongodb+srv://username:password@cluster.mongodb.net/?appName=Cluster0
   DATABASE_NAME=mindbridge_db
   
   # App Configuration
   APP_NAME="MindBridge"
   APP_ENV=development
   APP_HOST=0.0.0.0
   APP_PORT=8000
   
   # Models
   MAIN_MODEL=gpt-4o
   SYNTHESIZER_MODEL=gpt-4o-mini
   GROQ_WHISPER_MODEL=whisper-large-v3
   HF_EMOTION_MODEL=SamLowe/roberta-base-go_emotions
   
   # LLM Parameters
   LLM_TEMPERATURE=0.7
   LLM_MAX_TOKENS=1000
   MAX_HISTORY_TURNS=50
   MEMORY_WINDOW_SIZE=20
   ```
3. Add comments explaining each variable
4. Test setup with the example file

---

### Issue 3: Inconsistent Logging

**Define the Normal Issue**: Logging is inconsistent throughout the codebase with mixed log levels and no structured logging format, making it difficult to debug production issues.

**Where is the Issue**: Throughout the codebase

**Cause of the Issue**: No logging standard was established during development, leading to ad-hoc logging practices.

**How to Resolve**: Implement structured logging with consistent format and correlation IDs for request tracking.

**Steps to Resolve**:
1. Add to `requirements.txt`:
   ```
   structlog==23.2.0
   ```
2. Update `app/core/logger.py`:
   ```python
   import structlog
   
   def get_logger(name: str):
       return structlog.get_logger(name)
   ```
3. Configure structlog in `app/main.py`:
   ```python
   import structlog
   
   structlog.configure(
       processors=[
           structlog.stdlib.filter_by_level,
           structlog.stdlib.add_logger_name,
           structlog.stdlib.add_log_level,
           structlog.stdlib.PositionalArgumentsFormatter(),
           structlog.processors.TimeStamper(fmt="iso"),
           structlog.processors.StackInfoRenderer(),
           structlog.processors.format_exc_info,
           structlog.processors.JSONRenderer()
       ],
       context_class=dict,
       logger_factory=structlog.stdlib.LoggerFactory(),
       wrapper_class=structlog.stdlib.BoundLogger,
       cache_logger_on_first_use=True,
   )
   ```
4. Add correlation ID middleware in main.py
5. Update existing log calls to use structured format
6. Set up log aggregation (ELK, CloudWatch, etc.)

---

### Issue 4: Large Service File

**Define the Normal Issue**: The db_service.py file is 629 lines long, violating single responsibility principle and making it difficult to maintain and test.

**Where is the Issue**: `app/services/db_service.py`

**Cause of the Issue**: All database operations were consolidated into a single file without proper separation of concerns.

**How to Resolve**: Split the large service file into smaller, focused modules organized by domain.

**Steps to Resolve**:
1. Create new directory structure:
   ```
   app/services/db/
   ├── __init__.py
   ├── user_service.py
   ├── session_service.py
   ├── message_service.py
   ├── embedding_service.py
   └── escalation_service.py
   ```
2. Move user-related functions to `user_service.py`:
   - upsert_user_profile
   - get_user_profile
   - build_personality_summary
3. Move session functions to `session_service.py`:
   - get_existing_session
   - create_session
   - get_all_sessions
4. Move message functions to `message_service.py`:
   - save_message
   - get_formatted_history
   - get_session_messages
   - get_device_messages
5. Move embedding functions to `embedding_service.py`:
   - generate_embedding
   - retrieve_long_term_memory
6. Move escalation functions to `escalation_service.py`:
   - escalate_session
   - escalate_device
   - close_escalation
   - is_session_escalated
   - is_device_escalated
7. Update imports in all files that use these functions
8. Run tests to ensure nothing broke

---

### Issue 5: Magic Numbers in Code

**Define the Normal Issue**: Hardcoded numeric values (timeouts, limits) are scattered throughout the code, making it difficult to tune behavior without code changes.

**Where is the Issue**: Various files throughout the codebase

**Cause of the Issue**: Configuration values were hardcoded instead of being moved to centralized configuration.

**How to Resolve**: Extract magic numbers to configuration file with descriptive names.

**Steps to Resolve**:
1. Open `app/core/config.py`
2. Add new configuration fields:
   ```python
   # WebSocket Configuration
   COUNSELOR_TIMEOUT_SECONDS: int = 1200
   MAX_CONNECTIONS_PER_DEVICE: int = 5
   INACTIVITY_TIMEOUT_MINUTES: int = 35
   
   # Database Configuration
   DEFAULT_HISTORY_LIMIT: int = 100
   VECTOR_SEARCH_LIMIT: int = 4
   VECTOR_SEARCH_NUM_CANDIDATES: int = 50
   
   # Rate Limiting
   CHAT_RATE_LIMIT: str = "10/minute"
   AUDIO_RATE_LIMIT: str = "5/minute"
   ASSESSMENT_RATE_LIMIT: str = "3/minute"
   ```
3. Update `app/api/routes/human.py`:
   - Replace `COUNSELOR_TIMEOUT_SECONDS = 1200` with `settings.COUNSELOR_TIMEOUT_SECONDS`
   - Replace `MAX_CONNECTIONS_PER_DEVICE = 5` with `settings.MAX_CONNECTIONS_PER_DEVICE`
4. Update `app/services/db_service.py`:
   - Replace `limit=100` with `settings.DEFAULT_HISTORY_LIMIT`
   - Replace `limit=4` with `settings.VECTOR_SEARCH_LIMIT`
5. Update other files with hardcoded values
6. Update .env_example with new configuration options
7. Document each configuration option

---

### Issue 6: No Monitoring or Metrics

**Define the Normal Issue**: No monitoring or metrics collection makes it difficult to track system performance, detect anomalies, and troubleshoot production issues.

**Where is the Issue**: Application-wide

**Cause of the Issue**: Monitoring infrastructure was not set up during initial development.

**How to Resolve**: Implement Prometheus metrics collection and set up monitoring dashboards.

**Steps to Resolve**:
1. Add to `requirements.txt`:
   ```
   prometheus-fastapi-instrumentator==7.0.0
   ```
2. Install dependency:
   ```bash
   pip install prometheus-fastapi-instrumentator
   ```
3. Update `app/main.py`:
   ```python
   from prometheus_fastapi_instrumentator import Instrumentator
   
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       # ... existing code ...
       
       # Setup metrics
       Instrumentator().instrument(app).expose(app, endpoint="/metrics")
       
       yield
   ```
4. Set up Prometheus server
5. Create Grafana dashboards for:
   - Request latency by endpoint
   - Error rates by endpoint
   - AI API call counts and latency
   - WebSocket connection counts
   - Database query performance
6. Set up alerts for:
   - High error rates
   - Slow response times
   - Failed crisis detection
7. Document monitoring setup in DEPLOYMENT.md

---

### Issue 7: CORS Misconfiguration for Production

**Define the Normal Issue**: CORS is configured to allow all origins, which is a security risk for production deployments.

**Where is the Issue**: `app/main.py`, line 55

**Cause of the Issue**: CORS configuration uses wildcard for convenience during development but is unsafe for production.

**How to Resolve**: Configure CORS to only allow specific, trusted origins.

**Steps to Resolve**:
1. Open `app/core/config.py`
2. Add new configuration field:
   ```python
   ALLOWED_ORIGINS: str = ""  # Comma-separated list
   ```
3. Update `app/main.py`:
   ```python
   allowed_origins = settings.ALLOWED_ORIGINS.split(",") if settings.ALLOWED_ORIGINS else ["*"]
   
   app.add_middleware(
       CORSMiddleware,
       allow_origins=allowed_origins,
       allow_credentials=True,
       allow_methods=["*"],
       allow_headers=["*"],
   )
   ```
4. Update `.env_example`:
   ```env
   ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
   ```
5. Set ALLOWED_ORIGINS in production environment
6. Test with allowed and disallowed origins

---

### Issue 8: Missing Type Hints

**Define the Normal Issue**: Some functions lack complete type annotations, reducing IDE support and making it harder to catch type errors early.

**Where is the Issue**: Various functions throughout the codebase

**Cause of the Issue**: Type hints were not consistently applied during development.

**How to Resolve**: Add mypy for type checking and complete type annotations across the codebase.

**Steps to Resolve**:
1. Add to `requirements.txt`:
   ```
   mypy==1.8.0
   types-requests==2.31.0.20240106
   ```
2. Install dependencies:
   ```bash
   pip install mypy types-requests
   ```
3. Create `mypy.ini`:
   ```ini
   [mypy]
   python_version = 3.11
   warn_return_any = True
   warn_unused_configs = True
   disallow_untyped_defs = True
   ```
4. Run mypy to find missing type hints:
   ```bash
   mypy app/
   ```
5. Add missing type hints to flagged functions
6. Add mypy to pre-commit hooks
7. Fix type errors iteratively

---

### Issue 9: No Pre-Commit Hooks

**Define the Normal Issue**: No pre-commit hooks allow linting errors and formatting inconsistencies to reach the main branch.

**Where is the Issue**: Project configuration

**Cause of the Issue**: Pre-commit hooks were not set up during project initialization.

**How to Resolve**: Set up pre-commit hooks with black, ruff, and mypy.

**Steps to Resolve**:
1. Add to `requirements.txt`:
   ```
   pre-commit==3.6.0
   black==24.1.1
   ruff==0.1.15
   ```
2. Install dependencies:
   ```bash
   pip install pre-commit black ruff
   ```
3. Create `.pre-commit-config.yaml`:
   ```yaml
   repos:
     - repo: https://github.com/psf/black
       rev: 24.1.1
       hooks:
         - id: black
     - repo: https://github.com/astral-sh/ruff-pre-commit
       rev: v0.1.15
       hooks:
         - id: ruff
           args: [--fix]
     - repo: https://github.com/pre-commit/mirrors-mypy
       rev: v1.8.0
       hooks:
         - id: mypy
           additional_dependencies: [types-requests]
   ```
4. Install pre-commit hooks:
   ```bash
   pre-commit install
   ```
5. Run pre-commit on all files:
   ```bash
   pre-commit run --all-files
   ```
6. Fix any issues found
7. Commit the configuration

---

### Issue 10: No Health Check with Dependency Status

**Define the Normal Issue**: The health check endpoint only returns a simple status without checking dependencies like MongoDB and external APIs.

**Where is the Issue**: `app/main.py`, lines 76-79

**Cause of the Issue**: Health check was implemented as a minimal endpoint without dependency verification.

**How to Resolve**: Enhance health check to verify MongoDB, OpenAI, and Groq connectivity.

**Steps to Resolve**:
1. Open `app/main.py`
2. Update health check endpoint:
   ```python
   @app.get("/health")
   async def health():
       checks = {
           "app": settings.APP_NAME,
           "status": "healthy",
           "model_ready": getattr(app.state, "model_ready", False),
       }
       
       # Check MongoDB
       try:
           db = get_database()
           await db.command("ping")
           checks["mongodb"] = "healthy"
       except Exception as e:
           checks["mongodb"] = f"unhealthy: {str(e)}"
           checks["status"] = "degraded"
       
       # Check OpenAI
       try:
           from app.services.llm import _get_client
           client = _get_client()
           await client.models.list()
           checks["openai"] = "healthy"
       except Exception as e:
           checks["openai"] = f"unhealthy: {str(e)}"
           checks["status"] = "degraded"
       
       # Check Groq
       try:
           from app.services.safety import _get_client
           client = _get_client()
           await client.models.list()
           checks["groq"] = "healthy"
       except Exception as e:
           checks["groq"] = f"unhealthy: {str(e)}"
           checks["status"] = "degraded"
       
       status_code = 200 if checks["status"] == "healthy" else 503
       return JSONResponse(content=checks, status_code=status_code)
   ```
3. Test health check with dependencies up and down
4. Configure load balancers to use this endpoint
5. Set up alerts for unhealthy status

---

## Summary

### Major Issues Priority
1. **Immediate (Fix Today)**: Hardcoded JWT secret, Exposed MongoDB credentials
2. **This Week**: Silent crisis failures, Session race conditions, Rate limiting
3. **Before Production**: Startup blocking, Vector search fallback, WebSocket limits, Retry logic, Test coverage

### Normal Issues Priority
1. **High Impact**: Missing documentation, No monitoring, CORS misconfiguration
2. **Medium Impact**: Incomplete .env, Inconsistent logging, Large service file
3. **Low Impact**: Magic numbers, Missing type hints, No pre-commit hooks, Health check enhancement
