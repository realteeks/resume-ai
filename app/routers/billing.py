"""Billing: credit packs + PRO month pass via Razorpay.

Flow (standard Razorpay checkout):
  1. POST /billing/order  -> server prices the purchase from env config and
     creates a Razorpay Order (amount is NEVER taken from the client).
  2. Frontend opens Razorpay Checkout with that order_id.
  3. POST /billing/verify -> server verifies the HMAC-SHA256 signature
     (order_id|payment_id signed with the key secret) and grants credits/PRO.

Idempotent: verifying an already-paid order returns success without
double-granting.
"""

import hashlib
import hmac
import time
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import PaymentOrder, User

router = APIRouter(prefix="/billing", tags=["billing"])

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"
RAZORPAY_PAYMENTS_URL = "https://api.razorpay.com/v1/payments"


class OrderRequest(BaseModel):
    kind: str  # "pack3" | "pack5" | ... | "pro"


class VerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class DevGrantRequest(BaseModel):
    credits: int = 5


def _catalog() -> dict:
    """Purchasable items, priced from env config (minor units = paise)."""
    items = {}
    for size in settings.credit_pack_sizes:
        items[f"pack{size}"] = {
            "kind": f"pack{size}", "credits": size, "pro": False,
            "amount": int(round(size * settings.credit_price * 100)),
            "label": f"{size} credits",
        }
    items["pro"] = {
        "kind": "pro", "credits": settings.pro_monthly_credits, "pro": True,
        "amount": int(round(settings.pro_price * 100)),
        "label": f"PRO — {settings.pro_monthly_credits} credits/month + premium features",
    }
    return items


@router.get("/info")
def billing_info(user: User = Depends(get_current_user)):
    return {
        "credits": user.credits or 0,
        "plan": user.plan or "free",
        "is_pro": user.is_pro,
        "pro_expires_at": user.pro_expires_at.isoformat() if user.pro_expires_at else None,
        "currency": settings.currency,
        "credit_price": settings.credit_price,
        "items": list(_catalog().values()),
        "payments_configured": settings.payments_configured,
        "key_id": settings.razorpay_key_id if settings.payments_configured else "",
    }


@router.post("/order")
def create_order(payload: OrderRequest, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    if not settings.payments_configured:
        raise HTTPException(503, "Payments are not configured yet.")
    item = _catalog().get(payload.kind)
    if not item:
        raise HTTPException(400, "Unknown plan.")

    try:
        resp = httpx.post(
            RAZORPAY_ORDERS_URL,
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={
                "amount": item["amount"],
                "currency": settings.currency,
                "receipt": f"u{user.id}-{int(time.time())}",
                "payment_capture": 1,  # auto-capture so authorized funds settle
                "notes": {"kind": item["kind"], "user_id": str(user.id)},
            },
            timeout=20,
        )
        resp.raise_for_status()
        order = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not create the payment order ({type(e).__name__}).")

    db.add(PaymentOrder(
        user_id=user.id, provider="razorpay", provider_order_id=order["id"],
        kind=item["kind"], credits=item["credits"], amount=item["amount"],
        currency=settings.currency, status="created",
    ))
    db.commit()
    return {
        "order_id": order["id"], "amount": item["amount"],
        "currency": settings.currency, "key_id": settings.razorpay_key_id,
        "kind": item["kind"], "label": item["label"],
        "name": "job.run.place", "email": user.email,
    }


@router.post("/verify")
def verify_payment(payload: VerifyRequest, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    order = (db.query(PaymentOrder)
             .filter(PaymentOrder.provider_order_id == payload.razorpay_order_id,
                     PaymentOrder.user_id == user.id).first())
    if not order:
        raise HTTPException(404, "Order not found.")
    if order.status == "paid":  # idempotent — never double-grant
        return {"ok": True, "credits": user.credits, "is_pro": user.is_pro}

    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, payload.razorpay_signature):
        order.status = "failed"
        db.commit()
        raise HTTPException(400, "Payment verification failed.")

    # Reconcile with Razorpay: confirm the payment actually captured this order's
    # exact amount/currency (a valid signature alone can fire on mere authorization).
    # If the API is unreachable we fall back to the (already-verified) signature.
    try:
        pr = httpx.get(f"{RAZORPAY_PAYMENTS_URL}/{payload.razorpay_payment_id}",
                       auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                       timeout=20)
        pr.raise_for_status()
        pay = pr.json()
    except httpx.HTTPError:
        pay = None
    if pay is not None:
        ok = (pay.get("status") == "captured"
              and pay.get("order_id") == order.provider_order_id
              and pay.get("amount") == order.amount
              and pay.get("currency") == order.currency)
        if not ok:
            raise HTTPException(400, "Payment not captured or does not match the order.")

    order.status = "paid"
    order.payment_id = payload.razorpay_payment_id
    order.paid_at = datetime.utcnow()
    user.credits = (user.credits or 0) + order.credits
    if order.kind == "pro":
        user.plan = "pro"
        base = user.pro_expires_at if user.is_pro else datetime.utcnow()
        user.pro_expires_at = base + timedelta(days=30)
    db.commit()
    return {"ok": True, "credits": user.credits, "is_pro": user.is_pro}


@router.post("/dev-grant")
def dev_grant(payload: DevGrantRequest, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Local testing convenience — grants credits. Available ONLY when neither
    auth nor payments are configured, so it can never be live on a real deploy."""
    if not settings.dev_tools_enabled:
        raise HTTPException(404, "Not found")
    user.credits = (user.credits or 0) + max(0, min(payload.credits, 100))
    db.commit()
    return {"ok": True, "credits": user.credits}
