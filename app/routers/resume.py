from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.llm import parse_resume_text
from app.llm_providers import AllBackendsFailed
from app.models import Resume, User
from app.parsing import SUPPORTED, extract_text
from app.profile import build_profile
from app.resume_render import render_docx
from app.resume_templates import EMPTY_RESUME_DATA, TEMPLATES, get_template
from app.schemas import FreeformRequest, ResumeCreate, ResumeOut

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.get("/templates")
def list_templates():
    """Templates a user can pick when creating a resume from scratch."""
    return {"templates": TEMPLATES, "empty_data": EMPTY_RESUME_DATA}


@router.post("/upload", response_model=ResumeOut)
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a resume (PDF/DOCX/TXT); we extract + AI-parse it into structured data."""
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    try:
        text = extract_text(file.filename, content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not text or len(text) < 30:
        raise HTTPException(
            422, "Could not extract readable text (scanned image PDF?). "
            f"Supported: {', '.join(SUPPORTED)}."
        )
    try:
        parsed = parse_resume_text(text)
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))

    resume = Resume(
        user_id=user.id,
        title=file.filename,
        template="modern",
        data=parsed,
        source="upload",
        source_filename=file.filename,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.post("/freeform", response_model=ResumeOut)
def add_freeform(
    payload: FreeformRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Capture optional free-text experience/skills the user types in, parsed
    into structured data and merged into their profile (requirement #1)."""
    text = (payload.text or "").strip()
    if len(text) < 10:
        raise HTTPException(400, "Please enter a bit more detail.")
    try:
        parsed = parse_resume_text(text)
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))
    resume = Resume(
        user_id=user.id, title=payload.title, template="technical",
        data=parsed, source="notes",
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.get("/profile")
def get_profile(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """The user's consolidated career data, merged across all their resumes."""
    resumes = db.query(Resume).filter(Resume.user_id == user.id).all()
    return build_profile([r.data for r in resumes])


@router.get("", response_model=list[ResumeOut])
def list_resumes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return db.query(Resume).filter(Resume.user_id == user.id).all()


@router.post("", response_model=ResumeOut)
def create_resume(
    payload: ResumeCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not get_template(payload.template):
        raise HTTPException(400, f"Unknown template '{payload.template}'.")
    resume = Resume(
        user_id=user.id,
        title=payload.title,
        template=payload.template,
        data=payload.data.model_dump(),
        is_master=1,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(
    resume_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = _owned_resume(resume_id, user, db)
    return resume


@router.put("/{resume_id}", response_model=ResumeOut)
def update_resume(
    resume_id: int,
    payload: ResumeCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = _owned_resume(resume_id, user, db)
    resume.title = payload.title
    resume.template = payload.template
    resume.data = payload.data.model_dump()
    db.commit()
    db.refresh(resume)
    return resume


@router.delete("/{resume_id}")
def delete_resume(
    resume_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = _owned_resume(resume_id, user, db)
    db.delete(resume)
    db.commit()
    return {"ok": True}


@router.get("/{resume_id}/download")
def download_resume(
    resume_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resume = _owned_resume(resume_id, user, db)
    docx_bytes = render_docx(resume.data, resume.template)
    filename = f"{(resume.title or 'resume').replace(' ', '_')}.docx"
    return StreamingResponse(
        iter([docx_bytes]),
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _owned_resume(resume_id: int, user: User, db: Session) -> Resume:
    resume = (
        db.query(Resume)
        .filter(Resume.id == resume_id, Resume.user_id == user.id)
        .first()
    )
    if not resume:
        raise HTTPException(404, "Resume not found.")
    return resume
