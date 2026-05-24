"""
Shared FastAPI dependencies and pure utility functions.

"""

import hashlib
import json

from fastapi import Request

from app.store import IdempotencyStore



# Utility


def body_hash(body: dict) -> str:
    """
    Return a stable SHA-256 fingerprint for a request-body dict.

    Uses sort_keys=True so key ordering in the client payload never
    causes false conflicts (e.g. {"amount":100,"currency":"GHS"} and
    {"currency":"GHS","amount":100} hash identically).
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()



# FastAPI dependency


def get_store(request: Request) -> IdempotencyStore:
    """
    Inject the shared IdempotencyStore from app.state.

    Declaring this as a FastAPI Depends() keeps route handlers free of
    direct Request access and makes the store trivially swappable in tests.
    """
    return request.app.state.store