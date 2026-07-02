from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine
from app.llm_providers import pool_status
from app.routers import applications, auth, resume, score, tailor

Base.metadata.create_all(bind=engine)

app = FastAPI(title="PlaceholderAI — Agentic Resume Tailoring")

app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key)

app.include_router(auth.router)
app.include_router(resume.router)
app.include_router(tailor.router)
app.include_router(score.router)
app.include_router(applications.router)


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "auth_configured": settings.auth_configured,
        "llm_configured": settings.llm_configured,
        "llm": pool_status(),
    }


# Serve the minimal frontend (static index.html) at root.
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
