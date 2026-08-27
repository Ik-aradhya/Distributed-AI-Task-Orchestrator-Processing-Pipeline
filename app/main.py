# app/main.py
from fastapi import FastAPI
from app.api.routes import jobs, health

app = FastAPI(title="AI Image Generation Orchestrator")
app.include_router(jobs.router)
app.include_router(health.router)