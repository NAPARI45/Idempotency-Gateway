"""
Meta router — infrastructure / operational endpoints.

Mounted at the root path in main.py so /health is reachable directly.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Meta"])


@router.get("/health")
async def health_check():
    """Quick liveness probe — confirms the process is up and accepting requests."""
    return {"status": "ok", "service": "idempotency-gateway"}