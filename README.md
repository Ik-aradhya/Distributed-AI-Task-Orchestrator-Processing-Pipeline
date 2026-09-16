# 🎨 AI Image Generation Orchestrator

An enterprise-grade asynchronous backend for submitting, scheduling, executing, tracking, and recovering AI image-generation jobs with high reliability.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-4169E1?style=flat&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7+-DC382D?style=flat&logo=redis&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-5+-37B24D?style=flat&logo=celery&logoColor=white)

> **Architecture Style:** Event-Driven Microservices / Distributed Task Queue  
> **Core Pattern:** Transactional Outbox Pattern + Asynchronous Worker Queue  
> **Primary Objective:** Decouple long-running AI workloads from HTTP request-response cycles while guaranteeing durable state persistence, fault recovery, and real-time client updates.

---

## 🚀 Key Features & Goals

- **Asynchronous Execution:** Non-blocking job submission (`202 Accepted`) returning immediate job identifiers.
- **Transactional Outbox Pattern:** Atomic persistence of job state and task dispatch records within a single PostgreSQL transaction.
- **Durable State Storage:** Guaranteed state survival across application and worker node restarts.
- **Resilient Background Processing:** Distributed task execution using Celery workers with exponential backoff retries.
- **Real-Time Progress Streaming:** Live status push to clients via Redis Pub/Sub & Server-Sent Events (SSE).
- **Independent Scalability:** Decoupled architecture allowing API instances and Celery worker pools to scale independently.
- **Rate-Limiting & Security:** Per-API-key rate limiting backed by atomic Redis pipelines and SHA-256 key hashing.

---

## 🏗 High-Level Architecture

```text
Client Application
       │
       │  POST /jobs (Bearer Token)
       ▼
 ┌───────────┐
 │  FastAPI  │ ──► [Redis Rate Limiter] (Atomic Pipeline)
 └─────┬─────┘
       │
       │ (1) Atomic DB Transaction
       ▼
 ┌───────────┐
 │ PostgreSQL│ ◄─── Primary Durable Source of Truth
 └─────┬─────┘      ├─ Jobs Table (QUEUED)
       │            └─ Outbox Table (JOB_SUBMITTED)
       │
       │ (2) Polling via `FOR UPDATE SKIP LOCKED`
       ▼
 ┌───────────┐
 │OutboxRelay│
 └─────┬─────┘
       │ (3) Publish Task
       ▼
 ┌───────────┐
 │Redis Broker│
 └─────┬─────┘
       │ (4) Consume Task
       ▼
 ┌───────────┐         (5) Invoke API        ┌────────────────┐
 │  Celery   │ ────────────────────────────► │ External AI    │
 │  Worker   │ ◄──────────────────────────── │ Image Provider │
 └─────┬─────┘          Image Result         └────────────────┘
       │
       │ (6) Update Job State (COMPLETED / FAILED)
       ├─────────────────────────────────► PostgreSQL
       │
       │ (7) Publish Status Event
       ▼
 ┌───────────┐           ┌──────────────┐           ┌───────────┐
 │ Redis     │ ────────► │ FastAPI SSE  │ ────────► │  Client   │
 │ Pub/Sub   │           │ Endpoint     │           │  Stream   │
 └───────────┘           └──────────────┘           └───────────┘
```

### Component Breakdown

| Component | Role & Responsibility |
| :--- | :--- |
| **FastAPI** | Ingress API layer handling auth, validation, rate-limiting, job submission, status endpoints, and SSE streaming. |
| **PostgreSQL** | Primary relational store for durable job states, API key hashes, and transactional outbox logs. |
| **Outbox Relay** | Background daemon polling pending outbox events using `SKIP LOCKED` and pushing tasks to Redis. |
| **Celery Worker** | Asynchronous execution engine invoking external AI APIs, managing retries, and updating job outcomes. |
| **Redis** | Multi-purpose broker: task queue broker for Celery, rate-limiter backend, and transient Pub/Sub provider. |

> 📌 **Note:** PostgreSQL is the single source of truth. Redis Pub/Sub is strictly used for transient notifications; client reconnections fall back to PostgreSQL state queries.

---

## 🔄 Job State Machine

Jobs move through a well-defined lifecycle managed by the API, Relay, and Workers:

```text
 [PENDING] ──► [QUEUED] ──► [PROCESSING] ──► [COMPLETED] (Terminal)
                                 │
                                 ▼ (Error / Timeout)
                               [FAILED]
                                 │
                 ┌───────────────┴──────────────┐
                 ▼ (Retries remaining)          ▼ (Max retries reached)
             [RETRYING]                    [FAILED] (Terminal)
                 │
                 └────────► [QUEUED]
```

| State | Description |
| :--- | :--- |
| `PENDING` | Job record initialized. |
| `QUEUED` | Job committed to PostgreSQL and registered in the outbox. |
| `PROCESSING` | Claimed by a Celery worker; generation request in flight. |
| `COMPLETED` | Image generated successfully; result URL stored. |
| `RETRYING` | Recoverable error encountered; scheduled for Celery retry with exponential backoff. |
| `FAILED` | Permanent error or retries exhausted. |

---

## 🛡 Reliability & Fault Tolerance

1. **Transactional Outbox:** Eliminates the dual-write problem. Database state updates and outbox dispatch records commit atomically in PostgreSQL.
2. **At-Least-Once Delivery:** Uses `FOR UPDATE SKIP LOCKED` for outbox polling. Tasks are guaranteed to be dispatched, even under relay failure scenarios.
3. **Late Acknowledgments:** Celery workers acknowledge tasks only after completion (`acks_late=True`, `prefetch_multiplier=1`), enabling automatic task re-queueing if a worker node crashes.
4. **Exponential Backoff:** Transient provider errors (e.g., 429 rate limits, 5xx server errors) automatically trigger exponential backoff retries.
5. **Atomic Rate Limiting:** Executes Redis increment and expiry operations inside an atomic pipeline to prevent un-expired key leaks.

---

## ⚡ API Specifications

| Method | Endpoint | Description | Status Code |
| :--- | :--- | :--- | :--- |
| `POST` | `/jobs` | Submit an image generation prompt | `202 Accepted` |
| `GET` | `/jobs/{job_id}` | Fetch current status, result URL, or error details | `200 OK` |
| `GET` | `/jobs/{job_id}/stream` | Subscribe to live SSE status updates | `200 OK` |
| `GET` | `/jobs` | List user jobs with pagination | `200 OK` |
| `GET` | `/health` | Check API, Database, and Redis health | `200 OK` |

### Sample Request & Response

#### 1. Submit Generation Job
```http
POST /jobs HTTP/1.1
Host: localhost:8000
Authorization: Bearer <API_KEY>
Content-Type: application/json

{
  "prompt": "A futuristic cyberpunk city in synthwave colors"
}
```

```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "QUEUED",
  "prompt": "A futuristic cyberpunk city in synthwave colors",
  "created_at": "2026-09-16T18:00:00Z"
}
```

#### 2. Fetch Job Result
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "COMPLETED",
  "prompt": "A futuristic cyberpunk city in synthwave colors",
  "result_url": "https://cdn.provider.com/generated/img_89421.png",
  "error_message": null,
  "retry_count": 0,
  "created_at": "2026-09-16T18:00:00Z",
  "completed_at": "2026-09-16T18:00:04Z"
}
```

---

## 📁 Repository Structure

```text
app/
├── api/                  # FastAPI routes and dependencies
│   ├── dependencies.py
│   └── routes/           # Job & Health endpoints
├── core/                 # App configuration, DB, Redis & logging setups
├── models/               # SQLAlchemy ORM models (Jobs, Outbox, API keys)
├── schemas/              # Pydantic schemas for requests/responses
├── repositories/         # Database access layer
├── services/             # Core business logic (Jobs, Rate Limiting)
├── providers/            # External AI image generator adapters
├── workers/              # Celery worker definitions & task queues
├── relay/                # Outbox relay worker process
└── main.py               # FastAPI application entrypoint
```

---

## 🛠 Local Setup & Running

### Prerequisites
- Docker & Docker Compose
- Python 3.11+

### 1. Environment Configuration
Create a `.env` file in the project root:
```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_orchestrator
REDIS_URL=redis://localhost:6379/0
PROVIDER_API_KEY=your_external_provider_key
```

### 2. Run Infrastructure (Docker)
```bash
docker compose up -d postgres redis
```

### 3. Run Database Migrations
```bash
alembic upgrade head
```

### 4. Launch Services
Run each service in separate terminal sessions:

```bash
# Terminal 1: Start FastAPI Web Application
uvicorn app.main:app --reload --port 8000

# Terminal 2: Start Outbox Relay Daemon
python -m app.relay.outbox_relay

# Terminal 3: Start Celery Worker Node
celery -A app.workers.celery_app worker --loglevel=info
```

---

## 🧪 Testing Suite

Run unit and integration tests covering rate-limiting, transaction boundaries, outbox dispatch, and failure recoveries:

```bash
pytest tests/ -v
```

---

## 📊 Summary Architecture & System Scope

This backend system bridges fast client HTTP responses with slow, unpredictable external AI providers. By leveraging PostgreSQL as an immutable audit trail and state machine, Celery for worker orchestration, and Redis for rate-limiting and Pub/Sub notifications, the pipeline ensures fault tolerance, zero lost jobs, and scalable processing.
