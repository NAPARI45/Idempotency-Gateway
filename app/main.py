"""
Idempotency-Gateway — FinSafe Transactions Ltd.
A payment processing API with a full idempotency layer.
"""

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.models import PaymentRequest, PaymentResponse, ErrorResponse
from app.store import IdempotencyStore



# Application lifespan (startup / shutdown)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background TTL-cleanup task on boot; cancel it on shutdown."""
    store: IdempotencyStore = app.state.store
    cleanup_task = asyncio.create_task(store.run_cleanup_loop())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


# App instance
# 

app = FastAPI(
    title="Idempotency Gateway",
    description="Pay-Once Protocol — FinSafe Transactions Ltd.",
    version="1.0.0",
    lifespan=lifespan,
)

# Attach a single shared store to app.state so tests can swap it out easily.
app.state.store = IdempotencyStore(ttl_seconds=3600)



# Helpers


def _body_hash(body: dict) -> str:
    """Return a stable SHA-256 fingerprint for a request body dict."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


#  Routes
 
@app.get("/health", tags=["Meta"])
async def health_check():
    """Quick liveness probe."""
    return {"status": "ok", "service": "idempotency-gateway"}


@app.post(
    "/process-payment",
    status_code=201,
    response_model=PaymentResponse,
    tags=["Payments"],
    responses={
        409: {"model": ErrorResponse, "description": "Key reused with different body"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        425: {"model": ErrorResponse, "description": "Request still in-flight"},
    },
)
async def process_payment(
    payload: PaymentRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description="Client-generated UUID that uniquely identifies this payment attempt.",
    ),
):
    """
    Process a payment exactly once.

    Rules
    -----
    * **New key** → simulate processing (2 s delay) and persist the result.
    * **Same key + same body** → return the cached response instantly with
      ``X-Cache-Hit: true``.
    * **Same key + different body** → reject with ``409 Conflict``.
    * **Same key currently in-flight** → block until the first request
      completes, then return its result (no duplicate processing).
    """
    store: IdempotencyStore = request.app.state.store
    body_dict = payload.model_dump()
    incoming_hash = _body_hash(body_dict)

    # 1. Check for an already-completed entry 
    existing = await store.get(idempotency_key)

    if existing is not None and existing["status"] != "in-flight":
        # Conflict: same key, different body
        if existing["body_hash"] != incoming_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used for a different request body.",
            )
        # Cache hit: return stored response
        headers = {"X-Cache-Hit": "true"}
        return JSONResponse(
            content=existing["response"],
            status_code=existing["status_code"],
            headers=headers,
        )

    # 2. In-flight handling (race condition / concurrent retry) 
    if existing is not None and existing["status"] == "in-flight":
        # Another coroutine is already processing this key.
        # Wait (with a timeout) for it to finish, then return its result.
        deadline = time.monotonic() + 10  # max 10 s wait
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            refreshed = await store.get(idempotency_key)
            if refreshed and refreshed["status"] != "in-flight":
                headers = {"X-Cache-Hit": "true", "X-In-Flight-Wait": "true"}
                return JSONResponse(
                    content=refreshed["response"],
                    status_code=refreshed["status_code"],
                    headers=headers,
                )
        # Timeout — the original request never completed; surface an error.
        raise HTTPException(
            status_code=425,
            detail="Original request is still processing. Please retry shortly.",
        )

    # 3. Acquire a per-key lock to prevent simultaneous new processing 
    async with store.acquire_lock(idempotency_key):
        # Double-check after acquiring the lock (another waiter may have
        # already populated the entry while we were queued).
        existing = await store.get(idempotency_key)
        if existing is not None and existing["status"] != "in-flight":
            if existing["body_hash"] != incoming_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key already used for a different request body.",
                )
            headers = {"X-Cache-Hit": "true"}
            return JSONResponse(
                content=existing["response"],
                status_code=existing["status_code"],
                headers=headers,
            )

        # Mark as in-flight so concurrent duplicates know to wait.
        await store.set_in_flight(idempotency_key, incoming_hash)

        # 4. Simulate payment processing (2-second delay)
        await asyncio.sleep(2)

        # 5. Build and persist the response 
        response_body = {
            "message": f"Charged {payload.amount:g} {payload.currency}",
            "idempotency_key": idempotency_key,
            "amount": payload.amount,
            "currency": payload.currency,
            "status": "success",
        }
        await store.set_complete(
            key=idempotency_key,
            body_hash=incoming_hash,
            response=response_body,
            status_code=201,
        )

    return JSONResponse(content=response_body, status_code=201)