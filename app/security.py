"""Production protections: per-client rate limiting and security headers.

The rate limiter is an in-memory sliding window keyed by session user (when
logged in) or client IP — appropriate for a single-instance free-tier deploy.
LLM-backed endpoints get a much tighter budget than plain CRUD so scrapers or
stuck loops can't burn the LLM quota or run up costs.
"""

import time
from collections import OrderedDict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

MAX_KEYS = 20_000  # hard cap on tracked clients (prevents memory-exhaustion DoS)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# (max requests, window seconds)
EXPENSIVE = (20, 60)   # LLM-backed / payment endpoints
GENERAL = (120, 60)    # everything else


def _is_expensive(method: str, path: str) -> bool:
    """Only the endpoints that hit the LLM pool (or create payment orders)
    get the tight budget — downloads, previews, and layout edits stay snappy."""
    if method != "POST":
        return False
    if path.startswith(("/score", "/resumes/upload", "/resumes/freeform",
                        "/billing/order")):
        return True
    if path in ("/tailor/generate", "/tailor/analyze", "/tailor/extract-url",
               "/auth/login", "/auth/signup"):  # throttle credential-guessing/spam
        return True
    return path.startswith("/tailor/") and path.endswith(("/regenerate", "/outreach"))


def _client_key(request: Request) -> str:
    user_id = None
    try:
        user_id = request.session.get("user_id")
    except (AssertionError, AttributeError):  # session middleware not reached
        pass
    if user_id:  # authenticated key is not spoofable
        return f"u:{user_id}"
    # For anonymous requests, trust X-Forwarded-For ONLY behind a known proxy,
    # and then take the RIGHTMOST hop (appended by our proxy) — the leftmost is
    # attacker-controlled. Otherwise use the real socket peer.
    ip = request.client.host if request.client else "?"
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        hops = [h.strip() for h in fwd.split(",") if h.strip()]
        if hops:
            ip = hops[-1]
    return f"ip:{ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits: "OrderedDict[str, deque]" = OrderedDict()

    async def dispatch(self, request, call_next):
        expensive = _is_expensive(request.method, request.url.path)
        limit, window = EXPENSIVE if expensive else GENERAL
        key = f"{'x' if expensive else 'g'}:{_client_key(request)}"
        now = time.monotonic()

        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
        self._hits.move_to_end(key)  # LRU: most-recently-used at the end
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            resp = JSONResponse(
                {"detail": "Too many requests — please slow down and try again shortly."},
                status_code=429,
            )
            for k, v in SECURITY_HEADERS.items():
                resp.headers.setdefault(k, v)
            return resp
        hits.append(now)
        # Hard cap: evict the least-recently-used keys regardless of freshness,
        # so a flood of distinct keys can't grow the map without bound.
        while len(self._hits) > MAX_KEYS:
            self._hits.popitem(last=False)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp
