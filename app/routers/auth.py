import re

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_optional_user, oauth
from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, SignupRequest

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:  # malformed hash
        return False


@router.get("/login")
async def login(request: Request):
    if not settings.auth_configured:
        return HTMLResponse(
            "<h3>Google OAuth not configured.</h3>"
            "<p>Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env. "
            "For local testing without OAuth, use <code>POST /auth/dev-login</code>.</p>",
            status_code=503,
        )
    return await oauth.google.authorize_redirect(request, settings.google_redirect_uri)


@router.get("/callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    info = token.get("userinfo")

    user = db.query(User).filter(User.google_sub == info["sub"]).first()
    if not user:
        user = User(
            google_sub=info["sub"],
            email=info.get("email", ""),
            name=info.get("name", ""),
            picture=info.get("picture", ""),
            credits=settings.signup_credits,  # welcome credits
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    request.session["user_id"] = user.id
    return RedirectResponse(url="/")


@router.post("/dev-login")
def dev_login(request: Request, db: Session = Depends(get_db)):
    """Local-only shortcut: signs in a fake user. Available only when neither
    OAuth nor payments are configured (never on a real deploy)."""
    if not settings.dev_tools_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    user = db.query(User).filter(User.email == "dev@local.test").first()
    if not user:
        user = User(
            google_sub="dev-local",
            email="dev@local.test",
            name="Dev User",
            credits=settings.signup_credits,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    request.session["user_id"] = user.id
    return {"ok": True, "user_id": user.id}


@router.post("/signup")
def signup(payload: SignupRequest, request: Request, db: Session = Depends(get_db)):
    """Email+password account creation — an alternative to Google sign-in
    (useful when Google OAuth isn't set up yet, e.g. to test payment checkout)."""
    email = payload.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if existing.password_hash:
            raise HTTPException(409, "An account with this email already exists. Please log in.")
        raise HTTPException(409, "This email is registered via Google. Please continue with Google.")

    user = User(
        google_sub=None,
        email=email,
        name=payload.name.strip() or email.split("@")[0],
        password_hash=_hash_password(payload.password),
        credits=settings.signup_credits,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # concurrent signup with the same email
        db.rollback()
        raise HTTPException(409, "An account with this email already exists. Please log in.")
    db.refresh(user)

    request.session["user_id"] = user.id
    return {"ok": True, "user_id": user.id}


@router.post("/login")
def login_with_password(payload: LoginRequest, request: Request,
                        db: Session = Depends(get_db)):
    """Email+password login (alongside Google sign-in)."""
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user and not user.password_hash:
        raise HTTPException(400, "This email uses Google sign-in. Please continue with Google.")
    if not user or not _verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password.")

    request.session["user_id"] = user.id
    return {"ok": True, "user_id": user.id}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User | None = Depends(get_optional_user)):
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "credits": user.credits or 0,
        "is_pro": user.is_pro,
    }
