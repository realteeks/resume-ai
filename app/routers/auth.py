from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_optional_user, oauth
from app.config import settings
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


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
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    request.session["user_id"] = user.id
    return RedirectResponse(url="/")


@router.post("/dev-login")
def dev_login(request: Request, db: Session = Depends(get_db)):
    """Local-only shortcut: signs in a fake user when OAuth isn't configured."""
    if settings.auth_configured:
        return {"error": "Disabled when Google OAuth is configured."}
    user = db.query(User).filter(User.email == "dev@local.test").first()
    if not user:
        user = User(
            google_sub="dev-local",
            email="dev@local.test",
            name="Dev User",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
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
    }
