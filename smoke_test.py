#!/usr/bin/env python3
"""
End-to-end smoke test for the Distributed AI Task Orchestration Pipeline.

What it does:
  1. Waits for the API to be healthy
  2. Creates a raw API key and inserts its hash directly into Postgres
  3. POSTs a job to /jobs  (triggers Celery worker → Imagen 3)
  4. Polls /jobs/{id} until COMPLETED or FAILED
  5. Prints the result URL (local file path of the generated image)

Usage:
    python smoke_test.py [--prompt "..."] [--api-url http://localhost:8000]

Prerequisites:
    pip install httpx asyncpg

The stack must already be running:
    docker compose up --build -d
"""

import argparse
import asyncio
import hashlib
import secrets
import time
import sys
import uuid

import httpx

try:
    import asyncpg
except ImportError:
    print("[ERROR] asyncpg not installed. Run: pip install asyncpg")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_DB_URL = "postgresql://orchestrator:orchestrator@localhost:5432/orchestrator"
DEFAULT_PROMPT = "A photorealistic painting of a golden retriever sitting in a sunlit wheat field"
POLL_INTERVAL_S = 3
POLL_TIMEOUT_S = 300  # 5 min – Imagen 3 can be slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def seed_api_key(db_url: str) -> tuple[str, str]:
    """Insert a fresh API key row into Postgres. Returns (raw_key, key_id)."""
    conn = await asyncpg.connect(db_url)
    try:
        raw_key = f"sk-smoketest-{secrets.token_urlsafe(24)}"
        key_hash = hash_key(raw_key)
        key_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO api_keys (id, key_hash, name, is_active)
            VALUES ($1, $2, $3, true)
            """,
            key_id,
            key_hash,
            "smoke-test-key",
        )
        print(f"[OK] Seeded API key  id={key_id}  hash={key_hash[:16]}...")
        return raw_key, key_id
    finally:
        await conn.close()


async def wait_for_health(api_url: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{api_url}/health", timeout=3)
                if r.status_code == 200:
                    print(f"[OK] API is healthy at {api_url}")
                    return
        except Exception:
            pass
        print("    ... waiting for API to be ready")
        await asyncio.sleep(3)
    print(f"[FAIL] API did not become healthy within {timeout}s")
    sys.exit(1)


async def submit_job(api_url: str, raw_key: str, prompt: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{api_url}/jobs",
            json={"prompt": prompt},
            headers={"X-API-Key": raw_key},
            timeout=15,
        )
    if r.status_code != 202:
        print(f"[FAIL] Job submission failed: {r.status_code} {r.text}")
        sys.exit(1)
    job_id = r.json()["job_id"]
    print(f"[OK] Job submitted   job_id={job_id}")
    return job_id


async def poll_job(api_url: str, raw_key: str, job_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT_S
    while time.time() < deadline:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{api_url}/jobs/{job_id}",
                headers={"X-API-Key": raw_key},
                timeout=10,
            )
        if r.status_code != 200:
            print(f"[FAIL] Poll error: {r.status_code} {r.text}")
            sys.exit(1)

        data = r.json()
        status = data["status"]
        print(f"    status={status}")

        if status == "COMPLETED":
            return data
        if status == "FAILED":
            print(f"[FAIL] Job FAILED: {data.get('error_message')}")
            sys.exit(1)

        await asyncio.sleep(POLL_INTERVAL_S)

    print(f"[FAIL] Timed out waiting for job {job_id} after {POLL_TIMEOUT_S}s")
    sys.exit(1)


async def cleanup_api_key(db_url: str, raw_key: str) -> None:
    conn = await asyncpg.connect(db_url)
    try:
        # Must delete jobs first due to FK constraint jobs.api_key_id -> api_keys.id
        deleted_jobs = await conn.execute(
            """DELETE FROM jobs WHERE api_key_id = (
                SELECT id FROM api_keys WHERE key_hash = $1
            )""",
            hash_key(raw_key),
        )
        await conn.execute(
            "DELETE FROM api_keys WHERE key_hash = $1", hash_key(raw_key)
        )
        print(f"[OK] Cleaned up smoke-test API key (removed jobs: {deleted_jobs})")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="E2E smoke test for the orchestrator")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Image generation prompt")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Base URL of the API")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="Postgres connection string")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep the seeded API key")
    args = parser.parse_args()

    print("\n============================================================")
    print("  Orchestrator Pipeline Smoke Test (Hugging Face / FLUX.1)")
    print("============================================================\n")

    # 1. Health check
    await wait_for_health(args.api_url)

    # 2. Seed API key
    raw_key, _ = await seed_api_key(args.db_url)

    try:
        # 3. Submit job
        print(f"\n[->] Prompt: {args.prompt!r}\n")
        job_id = await submit_job(args.api_url, raw_key, args.prompt)

        # 4. Poll until done
        print(f"\n[?] Polling job {job_id} ...")
        result = await poll_job(args.api_url, raw_key, job_id)

        # 5. Report
        print("\n============================================================")
        print("  PIPELINE TEST PASSED")
        print(f"  job_id    : {result['job_id']}")
        print(f"  status    : {result['status']}")
        print(f"  image URL : {result.get('result_url', 'n/a')}")
        print("============================================================\n")

    finally:
        if not args.no_cleanup:
            await cleanup_api_key(args.db_url, raw_key)


if __name__ == "__main__":
    asyncio.run(main())
