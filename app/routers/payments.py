"""
Payments router — handles POST /process-payment.

"""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from app.dependencies import body_hash, get_store
from app.models import ErrorResponse, PaymentRequest, PaymentResponse
from app.store import IdempotencyStore

router = APIRouter(tags=["Payments"])


@router.post(
    "",
    status_code=201,
    response_model=PaymentResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Key reused with a different request body"},
        422: {"model": ErrorResponse, "description": "Validation error — missing header or bad body"},
    },
)
async def process_payment(
    payload: PaymentRequest,
    store: IdempotencyStore = Depends(get_store),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description="Client-generated UUID that uniquely identifies this payment attempt.",
    ),
):
    """
    Process a payment exactly once.

    * New key               → process (2 s delay) + persist → 201 Created
    * Same key + same body  → return cached response         → X-Cache-Hit: true
    * Same key + diff body  → reject                         → 409 Conflict
    """
    incoming_hash = body_hash(payload.model_dump())

    # Fast path: key already completed 
    existing = await store.get(idempotency_key)

    if existing is not None and existing["status"] == "complete":
        return _replay_or_conflict(existing, incoming_hash)

    #New key :acquire lock, process, persist
    async with store.acquire_lock(idempotency_key):
        # Double-check after acquiring the lock.
        existing = await store.get(idempotency_key)
        if existing is not None and existing["status"] == "complete":
            return _replay_or_conflict(existing, incoming_hash)

        await store.set_in_flight(idempotency_key, incoming_hash)
        response_body = await _run_payment(payload, idempotency_key)
        await store.set_complete(
            key=idempotency_key,
            body_hash=incoming_hash,
            response=response_body,
            status_code=201,
        )

    return JSONResponse(content=response_body, status_code=201)



# Private helpers


def _replay_or_conflict(existing: dict, incoming_hash: str) -> JSONResponse:
    """Return the cached response, or raise 409 if the body has changed."""
    if existing["body_hash"] != incoming_hash:
        raise HTTPException(
            status_code=409,
            detail="Idempotency key already used for a different request body.",
        )
    return JSONResponse(
        content=existing["response"],
        status_code=existing["status_code"],
        headers={"X-Cache-Hit": "true"},
    )


async def _run_payment(payload: PaymentRequest, idempotency_key: str) -> dict:
    """Simulate payment processing with a 2-second delay."""
    await asyncio.sleep(2)
    return {
        "message": f"Charged {payload.amount:g} {payload.currency}",
        "idempotency_key": idempotency_key,
        "amount": payload.amount,
        "currency": payload.currency,
        "status": "success",
    }