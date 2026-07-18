from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine
from app.llm_providers import pool_status
from app.routers import applications, auth, billing, resume, score, tailor
from app.security import RateLimitMiddleware, SecurityHeadersMiddleware

Base.metadata.create_all(bind=engine)

# Fail fast: a real (auth/payments-configured) deploy must not ship the
# published default signing secret — otherwise anyone could forge session
# cookies for any account. Local/dev (no keys) is allowed to use a default.
_WEAK_SECRETS = {"", "dev-secret-change-me", "change-me-to-a-long-random-string"}
if (settings.auth_configured or settings.payments_configured) \
        and settings.app_secret_key.strip() in _WEAK_SECRETS:
    raise RuntimeError(
        "APP_SECRET_KEY is unset or still the default. Set a strong random "
        "APP_SECRET_KEY before deploying with real credentials."
    )

app = FastAPI(
    title="job.run.place — AI Resume Tailoring",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Middleware runs bottom-up: session -> rate limit (needs session) -> headers.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    same_site="lax",
    https_only=settings.cookies_secure,  # Secure whenever served over HTTPS
    max_age=7 * 24 * 3600,  # 7-day sessions (default was 14)
)

app.include_router(auth.router)
app.include_router(resume.router)
app.include_router(tailor.router)
app.include_router(score.router)
app.include_router(applications.router)
app.include_router(billing.router)


@app.get("/healthz")
def healthz():
    """Public liveness check. Config details only outside production."""
    body = {"status": "ok", "environment": settings.environment}
    if not settings.is_production:
        body.update({
            "auth_configured": settings.auth_configured,
            "llm_configured": settings.llm_configured,
            "payments_configured": settings.payments_configured,
            "llm": pool_status(),
        })
    return body


@app.get("/robots.txt")
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


# Serve the frontend (static index.html) at root.
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
