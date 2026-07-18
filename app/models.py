from datetime import datetime

from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    google_sub = Column(String, unique=True, index=True)  # null for email/password accounts
    password_hash = Column(String)  # bcrypt hash; null for Google-only accounts
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String)
    picture = Column(String)
    credits = Column(Integer, default=0, nullable=False)  # 1 credit = 1 generation
    plan = Column(String, default="free")  # "free" | "pro"
    pro_expires_at = Column(DateTime)  # PRO pass validity end
    created_at = Column(DateTime, default=datetime.utcnow)

    resumes = relationship("Resume", back_populates="owner", cascade="all, delete")

    @property
    def is_pro(self) -> bool:
        return (self.plan == "pro" and self.pro_expires_at is not None
                and self.pro_expires_at > datetime.utcnow())


class Resume(Base):
    """The user's master/base resume, stored as structured JSON."""

    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="My Resume")
    template = Column(String, default="modern")  # which visual template
    data = Column(JSON, nullable=False)  # structured resume content
    source = Column(String, default="manual")  # "manual" or "upload"
    source_filename = Column(String)  # original file name if uploaded
    is_master = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="resumes")


class TailoredApplication(Base):
    """A resume tailored to a specific job description."""

    __tablename__ = "tailored_applications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    source_resume_id = Column(Integer, ForeignKey("resumes.id"))
    job_title = Column(String)
    company = Column(String)
    job_description = Column(Text)
    template = Column(String, default="technical")  # chosen render template
    layout = Column(JSON)  # {"section_order": [...], "include_summary": bool}
    answers = Column(JSON)  # user's answers to gap questions
    feedback = Column(Text)  # latest regeneration feedback
    tailored_data = Column(JSON)  # tailored resume content
    match_summary = Column(Text)  # LLM explanation of changes / gaps
    scores = Column(JSON)  # {"ats": {...}, "match": {...}}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OutreachMessage(Base):
    """Generated outreach (cold email / LinkedIn / referral) for a tailored job.

    Separate table (1:1-ish with a tailored resume) so it auto-creates without
    a migration on existing databases.
    """

    __tablename__ = "outreach_messages"
    # One outreach record per (user, tailored resume) — enforced at the DB level
    # so concurrent generate calls can't create duplicates.
    __table_args__ = (
        UniqueConstraint("user_id", "tailored_id", name="uq_outreach_user_tailored"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tailored_id = Column(
        Integer, ForeignKey("tailored_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    messages = Column(JSON)  # {"cold_email":{...},"linkedin":{...},"referral":{...}}
    inputs = Column(JSON)  # {recipient_name, note, tone}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentOrder(Base):
    """A Razorpay order and what it purchases (credit pack or PRO month)."""

    __tablename__ = "payment_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String, default="razorpay")
    provider_order_id = Column(String, unique=True, index=True, nullable=False)
    payment_id = Column(String)  # razorpay payment id once paid
    kind = Column(String, nullable=False)  # "pack3" | "pack5" | "pack10" | "pro"
    credits = Column(Integer, default=0)  # credits granted on success
    amount = Column(Integer, nullable=False)  # in minor units (paise)
    currency = Column(String, default="INR")
    status = Column(String, default="created")  # created | paid | failed
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime)


class JobApplication(Base):
    """An entry in the user's job application tracker."""

    __tablename__ = "job_applications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Optional link; if the tailored resume is removed, keep the tracker entry.
    tailored_id = Column(
        Integer, ForeignKey("tailored_applications.id", ondelete="SET NULL")
    )
    job_title = Column(String)
    company = Column(String)
    job_url = Column(String)
    location = Column(String)
    status = Column(String, default="saved")  # saved|applied|interviewing|offer|rejected
    notes = Column(Text)
    match_score = Column(Integer)  # snapshot for quick display
    applied_date = Column(String)  # free-text date the user applied
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
