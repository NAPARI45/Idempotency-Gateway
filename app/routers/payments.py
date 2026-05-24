"""
Payments router — handles POST /process-payment..
"""

import asyncio

from fastapi import APIRouter, Depends, Header
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
    Process a new payment and persist the result.

    Acquires a per-key lock, marks the record as in-flight, simulates
    a 2-second processing delay, then saves and returns the result.
    """
    incoming_hash = body_hash(payload.model_dump())

    async with store.acquire_lock(idempotency_key):
        await store.set_in_flight(idempotency_key, incoming_hash)
        response_body = await _run_payment(payload, idempotency_key)
        await store.set_complete(
            key=idempotency_key,
            body_hash=incoming_hash,
            response=response_body,
            status_code=201,
        )

    return JSONResponse(content=response_body, status_code=201)


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