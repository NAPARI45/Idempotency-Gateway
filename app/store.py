"""
In-memory Idempotency Store
----------------------------
Stores processed payment results keyed by the client's Idempotency-Key.

Features
--------
* Per-key asyncio locks to prevent concurrent duplicate processing.
* TTL-based automatic expiry (Developer's Choice feature).
* Thread-safe via a single asyncio event loop assumption (FastAPI default).
"""

import asyncio
import time
from typing import Any


class IdempotencyStore:
    """
    A lightweight, in-memory store for idempotency records.

    Each record is a dict with the shape:
        {
            "body_hash":   str,   # SHA-256 of the original request body
            "status":      str,   # "in-flight" | "complete"
            "response":    dict,  # the JSON-serialisable response body
            "status_code": int,   # HTTP status to replay
            "created_at":  float, # unix timestamp — used for TTL eviction
        }

    Developer's Choice — TTL Expiry
    --------------------------------
    Idempotency keys are not kept forever. After `ttl_seconds` the record is
    evicted. This prevents unbounded memory growth and means clients cannot
    accidentally replay extremely old keys (a security + correctness win for
    a real Fintech system). The default is 1 hour, matching Stripe's policy.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ttl = ttl_seconds

    # ── Public read/write API ─────────────────────────────────────────────────

    async def get(self, key: str) -> dict[str, Any] | None:
        record = self._store.get(key)
        if record is None:
            return None
        # Treat expired records as non-existent (lazy eviction).
        if time.monotonic() - record["created_at"] > self._ttl:
            del self._store[key]
            return None
        return record

    async def set_in_flight(self, key: str, body_hash: str) -> None:
        self._store[key] = {
            "body_hash": body_hash,
            "status": "in-flight",
            "response": None,
            "status_code": None,
            "created_at": time.monotonic(),
        }

    async def set_complete(
        self,
        key: str,
        body_hash: str,
        response: dict[str, Any],
        status_code: int,
    ) -> None:
        self._store[key] = {
            "body_hash": body_hash,
            "status": "complete",
            "response": response,
            "status_code": status_code,
            "created_at": time.monotonic(),
        }

    # ── Per-key lock (prevents simultaneous new processing) ──────────────────

    def acquire_lock(self, key: str) -> asyncio.Lock:
        """Return (and lazily create) an asyncio.Lock for *key*, used as an async context manager."""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # ── Background TTL cleanup loop ───────────────────────────────────────────

    async def run_cleanup_loop(self, interval_seconds: int = 300) -> None:
        """
        Periodically remove expired records and their associated locks.
        Runs as a background asyncio task for the lifetime of the server.
        """
        while True:
            await asyncio.sleep(interval_seconds)
            now = time.monotonic()
            expired_keys = [
                k
                for k, v in list(self._store.items())
                if now - v["created_at"] > self._ttl
            ]
            for k in expired_keys:
                self._store.pop(k, None)
                self._locks.pop(k, None)

    # ── Introspection (useful for tests & health checks) ─────────────────────

    def record_count(self) -> int:
        return len(self._store)