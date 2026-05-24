"""
Test Suite — Idempotency Gateway
Run with:  pytest tests/ -v
"""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.store import IdempotencyStore



# Fixtures


@pytest.fixture(autouse=True)
def fresh_store():
    """Give every test a clean in-memory store."""
    app.state.store = IdempotencyStore(ttl_seconds=3600)


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac



# Helpers


def payment_headers(key: str) -> dict:
    return {"Idempotency-Key": key, "Content-Type": "application/json"}


BODY = {"amount": 100, "currency": "GHS"}
BODY_ALT = {"amount": 500, "currency": "GHS"}



# User Story 1 — Happy Path


@pytest.mark.asyncio
async def test_first_request_returns_201(client):
    r = await client.post("/process-payment", json=BODY, headers=payment_headers("key-001"))
    assert r.status_code == 201
    data = r.json()
    assert data["message"] == "Charged 100 GHS"
    assert data["status"] == "success"
    assert data["idempotency_key"] == "key-001"


@pytest.mark.asyncio
async def test_first_request_no_cache_hit_header(client):
    r = await client.post("/process-payment", json=BODY, headers=payment_headers("key-002"))
    assert "X-Cache-Hit" not in r.headers



# User Story 2 — Duplicate Attempt (Idempotency Logic)


@pytest.mark.asyncio
async def test_duplicate_returns_same_response(client):
    h = payment_headers("key-003")
    r1 = await client.post("/process-payment", json=BODY, headers=h)
    r2 = await client.post("/process-payment", json=BODY, headers=h)

    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json()


@pytest.mark.asyncio
async def test_duplicate_has_cache_hit_header(client):
    h = payment_headers("key-004")
    await client.post("/process-payment", json=BODY, headers=h)
    r2 = await client.post("/process-payment", json=BODY, headers=h)

    assert r2.headers.get("X-Cache-Hit") == "true"


# User Story 3 — Different Body, Same Key (Conflict)


@pytest.mark.asyncio
async def test_conflict_on_different_body(client):
    await client.post("/process-payment", json=BODY, headers=payment_headers("key-005"))
    r2 = await client.post("/process-payment", json=BODY_ALT, headers=payment_headers("key-005"))

    assert r2.status_code == 409
    assert "different request body" in r2.json()["detail"]



# Validation


@pytest.mark.asyncio
async def test_missing_idempotency_key_returns_422(client):
    r = await client.post("/process-payment", json=BODY)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_amount_returns_422(client):
    r = await client.post(
        "/process-payment",
        json={"amount": -50, "currency": "GHS"},
        headers=payment_headers("key-006"),
    )
    assert r.status_code == 422


# Bonus — In-Flight / Race Condition Check


@pytest.mark.asyncio
async def test_concurrent_requests_processed_once(client):
    """
    Fire two identical requests simultaneously.
    Both should succeed and the store should contain exactly one record.
    """
    h = payment_headers("key-race")

    async def send():
        return await client.post("/process-payment", json=BODY, headers=h)

    r1, r2 = await asyncio.gather(send(), send())

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["message"] == r2.json()["message"]
    assert app.state.store.record_count() == 1


# Developer's Choice — TTL Expiry

@pytest.mark.asyncio
async def test_expired_key_is_reprocessed(client):
    """An expired key must be treated as new — no cache hit."""
    app.state.store = IdempotencyStore(ttl_seconds=0)

    h = payment_headers("key-ttl")
    r1 = await client.post("/process-payment", json=BODY, headers=h)
    assert r1.status_code == 201

    r2 = await client.post("/process-payment", json=BODY, headers=h)
    assert "X-Cache-Hit" not in r2.headers
    assert r2.status_code == 201



# Health check


@pytest.mark.asyncio
async def test_health_check(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"