"""Abuse guards for the public, login-free API: per-IP token buckets, a request body cap, and one JSON
access-log line per request. One pure-ASGI middleware (GuardMiddleware) so it covers every route,
including ones added later.

Limits (per client IP; env "N/SECONDS" overrides, RATE_LIMITS_OFF=1 disables all of them):
  POST /sites                      RATE_LIMIT_SITES  3/3600   (plus server.MAX_BUILDS concurrent builds)
  POST /ask                        RATE_LIMIT_ASK    20/600
  POST /sites/{id}/rerun|retry     RATE_LIMIT_RERUN  4/3600
  POST /run (push not false)       RATE_LIMIT_RUN    4/3600
A request that ends in a 4xx (validation error, unknown site) gives its token back.
Bodies over MAX_BODY_BYTES (default 32768) get 413.
The client IP is X-Real-IP if set, else the first X-Forwarded-For hop (Railway's proxy), else the socket.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qs

log = logging.getLogger("alpine.access")

RULES = [  # (name, method, path regex, env var, default "N/SECONDS", extra predicate on the query)
    ("sites", "POST", re.compile(r"^/sites/?$"), "RATE_LIMIT_SITES", "3/3600", None),
    ("ask", "POST", re.compile(r"^/ask/?$"), "RATE_LIMIT_ASK", "20/600", None),
    ("rerun", "POST", re.compile(r"^/sites/[^/]+/(rerun|retry)/?$"), "RATE_LIMIT_RERUN", "4/3600", None),
    ("run", "POST", re.compile(r"^/run/?$"), "RATE_LIMIT_RUN", "4/3600",
     lambda q: (q.get("push", ["true"])[-1].lower() not in ("false", "0", "no"))),
]
_SALT = os.environ.get("LOG_IP_SALT") or secrets.token_hex(8)


def limits_off() -> bool:
    return os.environ.get("RATE_LIMITS_OFF", "").strip().lower() in ("1", "true", "yes")


def _parse(spec: str, default: str) -> tuple[float, float]:
    try:
        n, sec = (float(x) for x in spec.split("/"))
        if n > 0 and sec > 0:
            return n, sec
    except (ValueError, AttributeError):
        pass
    n, sec = (float(x) for x in default.split("/"))
    return n, sec


class TokenBucket:
    """capacity tokens per key, refilled continuously at capacity/period per second."""

    def __init__(self, capacity: float, period: float, clock=time.monotonic):
        self.capacity, self.period, self.clock = capacity, period, clock
        self.rate = capacity / period
        self._b: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def _level(self, key: str, now: float) -> float:
        tokens, at = self._b.get(key, (self.capacity, now))
        return min(self.capacity, tokens + (now - at) * self.rate)

    def take(self, key: str) -> float:
        """0 if a token was taken, else seconds until one is available."""
        with self._lock:
            now = self.clock()
            tokens = self._level(key, now)
            if tokens >= 1:
                self._b[key] = (tokens - 1, now)
                if len(self._b) > 50_000:   # bound memory: drop full buckets
                    self._b = {k: v for k, v in self._b.items() if self._level(k, now) < self.capacity}
                return 0.0
            self._b[key] = (tokens, now)
            return (1 - tokens) / self.rate

    def refund(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            self._b[key] = (min(self.capacity, self._level(key, now) + 1), now)


class Limiter:
    def __init__(self):
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def bucket(self, name: str, env: str, default: str) -> TokenBucket:
        n, sec = _parse(os.environ.get(env, default), default)
        with self._lock:
            b = self._buckets.get(name)
            if b is None or (b.capacity, b.period) != (n, sec):
                b = self._buckets[name] = TokenBucket(n, sec)
            return b

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def match(self, method: str, path: str, query: str):
        if limits_off():
            return None
        q = parse_qs(query or "")
        for name, m, rx, env, default, pred in RULES:
            if method == m and rx.match(path) and (pred is None or pred(q)):
                return name, self.bucket(name, env, default)
        return None


limiter = Limiter()


def client_ip(scope) -> str:
    h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
    ip = h.get("x-real-ip", "").strip() or h.get("x-forwarded-for", "").split(",")[0].strip()
    if not ip and scope.get("client"):
        ip = scope["client"][0]
    return ip or "unknown"


def ip_hash(ip: str) -> str:
    return hashlib.sha256((_SALT + ip).encode()).hexdigest()[:12]


def max_body() -> int:
    try:
        return int(os.environ.get("MAX_BODY_BYTES", "32768"))
    except ValueError:
        return 32768


async def _send_json(send, status: int, body: dict, headers: list | None = None) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
                + (headers or [])})
    await send({"type": "http.response.body", "body": data})


class GuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        t0 = time.perf_counter()
        method, path = scope["method"], scope["path"]
        ip = client_ip(scope)
        status = {"code": 500}

        async def send_wrap(msg):
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
            await send(msg)

        try:
            # body cap
            if method in ("POST", "PUT", "PATCH"):
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
                limit = max_body()
                too_big = False
                try:
                    too_big = int(headers.get("content-length") or 0) > limit
                except ValueError:
                    pass
                if too_big:
                    return await _send_json(send_wrap, 413, {"detail": f"request body too large (max {limit} bytes)"})
                if "content-length" not in headers:   # chunked: buffer up to the limit
                    chunks, size, more = [], 0, True
                    while more:
                        msg = await receive()
                        if msg["type"] != "http.request":
                            break
                        chunks.append(msg.get("body", b""))
                        size += len(chunks[-1])
                        if size > limit:
                            return await _send_json(send_wrap, 413,
                                                    {"detail": f"request body too large (max {limit} bytes)"})
                        more = msg.get("more_body", False)
                    body, done = b"".join(chunks), {"v": False}

                    async def receive():  # noqa: F811 - replay the buffered body
                        if done["v"]:
                            return {"type": "http.disconnect"}
                        done["v"] = True
                        return {"type": "http.request", "body": body, "more_body": False}

            # rate limits
            hit = limiter.match(method, path, scope.get("query_string", b"").decode("latin-1"))
            if hit:
                name, bucket = hit
                wait = bucket.take(ip)
                if wait > 0:
                    retry = max(1, math.ceil(wait))
                    return await _send_json(send_wrap, 429, {
                        "detail": f"rate limit: at most {bucket.capacity:g} {method} {path} requests per "
                                  f"{bucket.period / 60:g} minutes from one address; try again in {retry} s",
                        "retry_after": retry}, [(b"retry-after", str(retry).encode())])
                await self.app(scope, receive, send_wrap)
                if 400 <= status["code"] < 500 and status["code"] != 429:
                    bucket.refund(ip)   # validation errors and unknown ids don't use up the quota
                return
            await self.app(scope, receive, send_wrap)
        finally:
            log.info(json.dumps({"method": method, "path": path, "status": status["code"],
                                 "ms": round((time.perf_counter() - t0) * 1000, 1), "ip": ip_hash(ip)}))
