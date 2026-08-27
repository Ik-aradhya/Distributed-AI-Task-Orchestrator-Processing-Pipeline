# app/api/routes/health.py
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.api.dependencies import get_db
from app.core.redis_client import get_redis

router = APIRouter(tags=["health"])

@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    get_redis().ping()
    return {"status": "ok"}