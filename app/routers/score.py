from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.llm import score_ats, score_match
from app.llm_providers import AllBackendsFailed
from app.models import Resume, User
from app.profile import build_profile
from app.schemas import AtsScore, AtsScoreRequest, MatchScore, MatchScoreRequest

router = APIRouter(prefix="/score", tags=["score"])


def _profile_or_data(resume_data, user, db):
    if resume_data:
        return resume_data
    resumes = db.query(Resume).filter(Resume.user_id == user.id).all()
    if not resumes:
        raise HTTPException(400, "No resume data yet.")
    return build_profile([r.data for r in resumes])


@router.post("/ats", response_model=AtsScore)
def ats(payload: AtsScoreRequest, user: User = Depends(get_current_user),
        db: Session = Depends(get_db)):
    """ATS score + strengths/flaws for a resume (or the consolidated profile)."""
    data = _profile_or_data(payload.resume_data, user, db)
    try:
        return score_ats(data)
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))


@router.post("/match", response_model=MatchScore)
def match(payload: MatchScoreRequest, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)):
    """Match score of a resume (or profile) against a job description."""
    data = _profile_or_data(payload.resume_data, user, db)
    try:
        return score_match(data, payload.job_title, payload.job_description)
    except (AllBackendsFailed, RuntimeError) as e:
        raise HTTPException(503, str(e))
