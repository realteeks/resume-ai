from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import JobApplication, TailoredApplication, User
from app.schemas import (JOB_STATUSES, JobApplicationCreate,
                         JobApplicationOut, JobApplicationUpdate)

router = APIRouter(prefix="/applications", tags=["applications"])


def _owned(app_id: int, user: User, db: Session) -> JobApplication:
    app = (db.query(JobApplication)
           .filter(JobApplication.id == app_id, JobApplication.user_id == user.id)
           .first())
    if not app:
        raise HTTPException(404, "Application not found.")
    return app


@router.get("", response_model=list[JobApplicationOut])
def list_applications(user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    return (db.query(JobApplication)
            .filter(JobApplication.user_id == user.id)
            .order_by(JobApplication.created_at.desc()).all())


@router.post("", response_model=JobApplicationOut)
def create_application(payload: JobApplicationCreate,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    if payload.status not in JOB_STATUSES:
        raise HTTPException(400, f"Status must be one of {JOB_STATUSES}.")
    if payload.match_score is not None and not (0 <= payload.match_score <= 100):
        raise HTTPException(400, "match_score must be between 0 and 100.")
    # A client-supplied tailored_id must reference the caller's own resume.
    if payload.tailored_id is not None:
        owns = (db.query(TailoredApplication)
                .filter(TailoredApplication.id == payload.tailored_id,
                        TailoredApplication.user_id == user.id).first())
        if not owns:
            raise HTTPException(404, "Tailored resume not found.")
    app = JobApplication(user_id=user.id, **payload.model_dump())
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


@router.put("/{app_id}", response_model=JobApplicationOut)
def update_application(app_id: int, payload: JobApplicationUpdate,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    app = _owned(app_id, user, db)
    updates = payload.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] not in JOB_STATUSES:
        raise HTTPException(400, f"Status must be one of {JOB_STATUSES}.")
    for k, v in updates.items():
        setattr(app, k, v)
    db.commit()
    db.refresh(app)
    return app


@router.delete("/{app_id}")
def delete_application(app_id: int, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    app = _owned(app_id, user, db)
    db.delete(app)
    db.commit()
    return {"ok": True}
