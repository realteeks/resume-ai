from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.jobfetch import JobFetchError, fetch_job_text
from app.llm import (analyze_job_fit, extract_job_from_text, generate_outreach,
                     generate_tailored, refine_resume, score_ats, score_match,
                     write_summary)
from app.llm_providers import AllBackendsFailed
from app.models import (JobApplication, OutreachMessage, Resume,
                        TailoredApplication, User)
from app.pdf_render import compute_fit, render_pdf
from app.profile import build_profile, enforce_factual_fields
from app.resume_render import render_docx
from app.resume_templates import default_layout, get_template, validate_layout
from app.schemas import (AnalyzeRequest, AnalyzeResult, EditRequest,
                         GenerateRequest, JobExtractResult, JobUrlRequest,
                         LayoutRequest, OUTREACH_TONES, OutreachRequest,
                         OutreachResult, RegenerateRequest,
                         TemplateChangeRequest, TailorResult)

router = APIRouter(prefix="/tailor", tags=["tailor"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _user_profile(user: User, db: Session) -> dict:
    resumes = db.query(Resume).filter(Resume.user_id == user.id).all()
    if not resumes:
        raise HTTPException(400, "No resume data yet. Upload or create a resume first.")
    return build_profile([r.data for r in resumes])


def _owned(tailored_id: int, user: User, db: Session) -> TailoredApplication:
    rec = (
        db.query(TailoredApplication)
        .filter(TailoredApplication.id == tailored_id,
                TailoredApplication.user_id == user.id)
        .first()
    )
    if not rec:
        raise HTTPException(404, "Tailored resume not found.")
    return rec


def _score(data: dict, job_title, job_description, template="technical",
           layout=None) -> dict:
    """ATS + match scores plus the TRUE rendered page count (best-effort)."""
    scores = {}
    try:
        scores["ats"] = score_ats(data)
    except Exception:
        scores["ats"] = None
    try:
        scores["match"] = score_match(data, job_title, job_description)
    except Exception:
        scores["match"] = None
    try:
        scores["pages"] = compute_fit(data, template, layout)["pages"]
    except Exception:
        scores["pages"] = None
    return scores


def _match_score(scores) -> int | None:
    """Safely pull the integer match score out of a scores dict."""
    m = (scores or {}).get("match")
    return m.get("score") if isinstance(m, dict) else None


def _effective_layout(rec: TailoredApplication) -> dict:
    """A resume's layout, backfilling the template default for legacy/NULL rows
    so section order + summary choice are never silently dropped at render time."""
    return rec.layout or default_layout(rec.template or "technical")


def _sync_tracker(db: Session, rec: TailoredApplication) -> None:
    """Keep the linked tracker entry's match-score snapshot in sync."""
    app = (db.query(JobApplication)
           .filter(JobApplication.tailored_id == rec.id).first())
    if app:
        app.match_score = _match_score(rec.scores)


@router.post("/extract-url", response_model=JobExtractResult)
def extract_url(payload: JobUrlRequest, user: User = Depends(get_current_user)):
    """Fetch a job posting URL and extract its title/company/description."""
    try:
        text = fetch_job_text(payload.url)
    except JobFetchError as e:
        raise HTTPException(400, str(e))
    if len(text) < 80:
        raise HTTPException(
            422, "Couldn't read meaningful content from that page (it may require "
            "login or load via JavaScript). Please paste the description manually."
        )
    try:
        result = extract_job_from_text(text)
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))
    if not result["found"] or len(result["job_description"]) < 40:
        raise HTTPException(
            422, "That page didn't look like a job posting. Please paste the "
            "description manually."
        )
    return result


@router.post("/analyze", response_model=AnalyzeResult)
def analyze(payload: AnalyzeRequest, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    """Assess fit, surface gap questions, and score the raw profile (the
    "before" match score we aim to beat)."""
    profile = _user_profile(user, db)
    try:
        result = analyze_job_fit(profile, payload.job_title, payload.company,
                                 payload.job_description)
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))
    try:
        result["match_score"] = score_match(profile, payload.job_title,
                                             payload.job_description)
    except Exception:
        result["match_score"] = None
    return result


def _require_template(template_id: str, user: User) -> dict:
    t = get_template(template_id)
    if not t:
        raise HTTPException(400, f"Unknown template '{template_id}'.")
    if t.get("pro") and not user.is_pro:
        raise HTTPException(403, f"'{t['name']}' is a PRO template. Upgrade to use it.")
    return t


@router.post("/generate", response_model=TailorResult)
def generate(payload: GenerateRequest, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    _require_template(payload.template, user)
    profile = _user_profile(user, db)

    # Charge 1 credit up front with a guarded decrement, then COMMIT immediately
    # so we don't hold a write lock across the slow LLM work. If anything below
    # fails, we issue a compensating refund — a failed generation never costs a
    # credit, and concurrent requests aren't blocked on a long transaction.
    charged = (db.query(User)
               .filter(User.id == user.id, User.credits >= 1)
               .update({User.credits: User.credits - 1}, synchronize_session=False))
    if not charged:
        db.rollback()
        raise HTTPException(402, "You're out of credits. Buy a pack or go PRO to continue.")
    db.commit()

    def _refund():
        db.query(User).filter(User.id == user.id).update(
            {User.credits: User.credits + 1}, synchronize_session=False)
        db.commit()

    # Resolve the per-resume layout: template default, with the summary override.
    layout = default_layout(payload.template)
    if payload.include_summary is not None:
        layout["include_summary"] = payload.include_summary
    try:
        result = generate_tailored(profile, payload.job_title, payload.company,
                                   payload.job_description, payload.answers,
                                   template=payload.template,
                                   include_summary=layout["include_summary"])
        tailored_data = enforce_factual_fields(result["tailored_data"], profile)  # req #5
        scores = _score(tailored_data, payload.job_title, payload.job_description,
                        payload.template, layout)

        # Self-refinement: if the ATS reviewer found flaws, let the LLM fix the
        # content-fixable ones (e.g. cross-section redundancy) and keep the
        # result only when it scores at least as well.
        flaws = ((scores.get("ats") or {}).get("flaws")) or []
        if flaws:
            try:
                refined = refine_resume(tailored_data, flaws, payload.job_title,
                                        payload.job_description)
                refined_data = enforce_factual_fields(refined["tailored_data"], profile)
                new_scores = _score(refined_data, payload.job_title,
                                    payload.job_description, payload.template, layout)
                old_ats = ((scores.get("ats") or {}).get("score")) or 0
                new_ats = ((new_scores.get("ats") or {}).get("score")) or 0
                if new_ats >= old_ats:
                    tailored_data, scores = refined_data, new_scores
                    if refined.get("fixes_applied"):
                        result["match_summary"] = (result["match_summary"] +
                            " Auto-fixed: " + "; ".join(refined["fixes_applied"][:4]))
            except Exception:
                pass  # refinement is best-effort; the original result stands
    except HTTPException:
        _refund()
        raise
    except (AllBackendsFailed, RuntimeError) as e:
        _refund()
        raise HTTPException(503, str(e))
    except Exception:  # malformed provider response, etc. — never eat the credit
        _refund()
        raise HTTPException(503, "Generation failed unexpectedly — your credit was refunded.")

    rec = TailoredApplication(
        user_id=user.id, job_title=payload.job_title, company=payload.company,
        job_description=payload.job_description, template=payload.template,
        layout=layout, answers=payload.answers, tailored_data=tailored_data,
        match_summary=result["match_summary"], scores=scores,
    )
    db.add(rec)
    db.flush()  # assign rec.id without committing yet

    # Auto-add to the job tracker (status "saved"), linked to this resume.
    # Same transaction as the resume so they commit atomically.
    db.add(JobApplication(
        user_id=user.id, tailored_id=rec.id, job_title=payload.job_title or "",
        company=payload.company or "", status="saved",
        match_score=_match_score(scores),
    ))
    db.commit()
    db.refresh(rec)
    return rec


@router.put("/{tailored_id}", response_model=TailorResult)
def edit(tailored_id: int, payload: EditRequest,
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Save user edits to the generated resume, then re-score (req #4)."""
    rec = _owned(tailored_id, user, db)
    rec.tailored_data = payload.tailored_data
    rec.scores = _score(payload.tailored_data, rec.job_title, rec.job_description,
                        rec.template, _effective_layout(rec))
    _sync_tracker(db, rec)
    db.commit()
    db.refresh(rec)
    return rec


@router.put("/{tailored_id}/template", response_model=TailorResult)
def change_template(tailored_id: int, payload: TemplateChangeRequest,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Switch the visual template for an existing resume (keeps the user's
    section layout; only the visual style changes)."""
    _require_template(payload.template, user)
    rec = _owned(tailored_id, user, db)
    rec.template = payload.template
    # Page count depends on the template; refresh it without re-calling the LLM.
    scores = dict(rec.scores or {})
    try:
        scores["pages"] = compute_fit(rec.tailored_data, payload.template,
                                      _effective_layout(rec))["pages"]
    except Exception:
        pass
    rec.scores = scores
    db.commit()
    db.refresh(rec)
    return rec


@router.put("/{tailored_id}/layout", response_model=TailorResult)
def change_layout(tailored_id: int, payload: LayoutRequest,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Reorder/delete/restore sections and toggle the professional summary."""
    rec = _owned(tailored_id, user, db)
    new_layout = validate_layout(payload.model_dump())

    # Turning the summary ON when the resume has no summary text (e.g. it was
    # generated with a no-summary template): write one now so the toggle
    # actually shows content instead of silently doing nothing.
    had_summary = bool((rec.tailored_data or {}).get("summary", "").strip())
    if new_layout["include_summary"] and not had_summary:
        summary = ""
        try:
            summary = write_summary(rec.tailored_data, rec.job_title,
                                    rec.job_description)
        except Exception:
            resumes = db.query(Resume).filter(Resume.user_id == user.id).all()
            summary = build_profile([r.data for r in resumes]).get("summary", "")
        if summary:
            data = dict(rec.tailored_data or {})
            data["summary"] = summary
            rec.tailored_data = data

    rec.layout = new_layout
    # Layout changes what's shown and how much fits — refresh page count.
    scores = dict(rec.scores or {})
    try:
        scores["pages"] = compute_fit(rec.tailored_data, rec.template, rec.layout)["pages"]
    except Exception:
        pass
    rec.scores = scores
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/{tailored_id}/regenerate", response_model=TailorResult)
def regenerate(tailored_id: int, payload: RegenerateRequest,
               user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Regenerate the resume, optionally guided by user feedback (req #5)."""
    rec = _owned(tailored_id, user, db)
    profile = _user_profile(user, db)
    template = payload.template or rec.template
    _require_template(template, user)
    try:
        result = generate_tailored(profile, rec.job_title, rec.company,
                                   rec.job_description, rec.answers or {},
                                   template=template, feedback=payload.feedback)
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))

    rec.tailored_data = enforce_factual_fields(result["tailored_data"], profile)
    rec.match_summary = result["match_summary"]
    rec.template = template
    rec.feedback = payload.feedback
    rec.scores = _score(rec.tailored_data, rec.job_title, rec.job_description,
                        template, _effective_layout(rec))
    _sync_tracker(db, rec)
    db.commit()
    db.refresh(rec)
    return rec


@router.post("/{tailored_id}/outreach", response_model=OutreachResult)
def make_outreach(tailored_id: int, payload: OutreachRequest,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Generate (and persist) cold email / LinkedIn / referral messages for a
    tailored resume's job. PRO feature."""
    if not user.is_pro:
        raise HTTPException(403, "Outreach messages are a PRO feature. Upgrade to unlock them.")
    if payload.tone not in OUTREACH_TONES:
        raise HTTPException(400, f"Tone must be one of {OUTREACH_TONES}.")
    rec = _owned(tailored_id, user, db)
    try:
        messages = generate_outreach(
            rec.tailored_data, rec.job_title, rec.company, rec.job_description,
            recipient_name=payload.recipient_name, note=payload.note, tone=payload.tone,
        )
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))

    inputs = payload.model_dump()

    def _save():
        om = (db.query(OutreachMessage)
              .filter(OutreachMessage.tailored_id == rec.id,
                      OutreachMessage.user_id == user.id).first())
        if om:
            om.messages, om.inputs = messages, inputs
        else:
            db.add(OutreachMessage(user_id=user.id, tailored_id=rec.id,
                                   messages=messages, inputs=inputs))
        db.commit()

    try:
        _save()
    except IntegrityError:
        # A concurrent request inserted first; the unique constraint fired.
        db.rollback()
        _save()  # now the row exists → updates it
    return {"messages": messages, "inputs": inputs}


@router.get("/{tailored_id}/outreach", response_model=OutreachResult)
def get_outreach(tailored_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    _owned(tailored_id, user, db)
    om = (db.query(OutreachMessage)
          .filter(OutreachMessage.tailored_id == tailored_id,
                  OutreachMessage.user_id == user.id).first())
    if not om:
        raise HTTPException(404, "No outreach generated yet.")
    return {"messages": om.messages, "inputs": om.inputs}


@router.get("", response_model=list[TailorResult])
def list_tailored(user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    return (
        db.query(TailoredApplication)
        .filter(TailoredApplication.user_id == user.id)
        .order_by(TailoredApplication.created_at.desc())
        .all()
    )


@router.get("/{tailored_id}/download")
def download_tailored(tailored_id: int, format: str = "docx",
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Download the tailored resume as .docx (default) or .pdf. Both use the
    same one-page fit so they look consistent."""
    rec = _owned(tailored_id, user, db)
    template = rec.template or "technical"
    layout = _effective_layout(rec)
    company = (rec.company or "company").replace(" ", "_")

    if format == "pdf":
        pdf_bytes, _ = render_pdf(rec.tailored_data, template, layout)
        return StreamingResponse(
            iter([pdf_bytes]), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="resume_{company}.pdf"'},
        )

    fit = compute_fit(rec.tailored_data, template, layout)
    docx_bytes = render_docx(rec.tailored_data, template, fit=fit, layout=layout)
    return StreamingResponse(
        iter([docx_bytes]), media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="resume_{company}.docx"'},
    )


@router.get("/{tailored_id}/preview.pdf")
def preview_pdf(tailored_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Inline PDF (for the in-app preview iframe) — the true rendered page."""
    rec = _owned(tailored_id, user, db)
    pdf_bytes, _ = render_pdf(rec.tailored_data, rec.template or "technical",
                              _effective_layout(rec))
    return StreamingResponse(
        iter([pdf_bytes]), media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=preview.pdf"},
    )
