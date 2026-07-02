from typing import Any, Literal, Optional

from pydantic import BaseModel


class ContactInfo(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    website: str = ""


class ExperienceItem(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = []


class EducationItem(BaseModel):
    degree: str = ""
    institution: str = ""
    location: str = ""
    graduation_date: str = ""
    details: str = ""


class ResumeData(BaseModel):
    contact: ContactInfo = ContactInfo()
    summary: str = ""
    skills: list[str] = []
    experience: list[ExperienceItem] = []
    education: list[EducationItem] = []
    projects: list[dict[str, Any]] = []
    achievements: list[str] = []
    certifications: list[str] = []


class ResumeCreate(BaseModel):
    title: str = "My Resume"
    template: str = "modern"
    data: ResumeData


class ResumeOut(BaseModel):
    id: int
    title: str
    template: str
    data: dict[str, Any]
    source: str = "manual"
    is_master: int

    class Config:
        from_attributes = True


class FreeformRequest(BaseModel):
    """Optional free-text experience/skills the user types in addition to files."""
    text: str
    title: str = "Additional details"


# --- Scoring ---------------------------------------------------------------

class AtsScore(BaseModel):
    score: int = 0
    strengths: list[str] = []
    flaws: list[str] = []
    suggestions: list[str] = []


class MatchScore(BaseModel):
    score: int = 0
    matched_keywords: list[str] = []
    missing_keywords: list[str] = []
    notes: str = ""


class AtsScoreRequest(BaseModel):
    resume_data: Optional[dict[str, Any]] = None  # None => use consolidated profile


class MatchScoreRequest(BaseModel):
    job_title: Optional[str] = None
    job_description: str
    resume_data: Optional[dict[str, Any]] = None  # None => use consolidated profile


# --- Tailoring flow --------------------------------------------------------

class JobUrlRequest(BaseModel):
    url: str


class JobExtractResult(BaseModel):
    found: bool
    job_title: str = ""
    company: str = ""
    job_description: str = ""


class AnalyzeRequest(BaseModel):
    job_title: Optional[str] = None
    company: Optional[str] = None
    job_description: str


class GapQuestion(BaseModel):
    id: str
    topic: str = ""
    question: str


class AnalyzeResult(BaseModel):
    sufficient: bool
    coverage_summary: str = ""
    matched_strengths: list[str] = []
    questions: list[GapQuestion] = []
    match_score: Optional[MatchScore] = None  # "before" score on raw profile


class GenerateRequest(BaseModel):
    job_title: Optional[str] = None
    company: Optional[str] = None
    job_description: str
    template: str = "technical"
    answers: dict[str, str] = {}
    include_summary: Optional[bool] = None  # override the template default


class LayoutRequest(BaseModel):
    section_order: list[str]
    include_summary: bool = True


class EditRequest(BaseModel):
    tailored_data: dict[str, Any]


class TemplateChangeRequest(BaseModel):
    template: str


class RegenerateRequest(BaseModel):
    feedback: str = ""
    template: Optional[str] = None


class TailorResult(BaseModel):
    id: int
    job_title: Optional[str]
    company: Optional[str]
    template: str
    layout: Optional[dict[str, Any]] = None  # {"section_order":[...],"include_summary":bool}
    tailored_data: dict[str, Any]
    match_summary: str
    scores: Optional[dict[str, Any]] = None  # {"ats": {...}, "match": {...}}

    class Config:
        from_attributes = True


# --- Outreach messages -----------------------------------------------------

OUTREACH_TONES = ["professional", "warm", "direct"]


class OutreachRequest(BaseModel):
    recipient_name: str = ""   # recruiter / hiring manager / employee name
    note: str = ""             # optional context/hook the user wants included
    tone: Literal["professional", "warm", "direct"] = "professional"


class OutreachResult(BaseModel):
    messages: dict[str, Any]
    inputs: Optional[dict[str, Any]] = None

    class Config:
        from_attributes = True


# --- Job tracker -----------------------------------------------------------

JOB_STATUSES = ["saved", "applied", "interviewing", "offer", "rejected"]


class JobApplicationCreate(BaseModel):
    job_title: str = ""
    company: str = ""
    job_url: str = ""
    location: str = ""
    status: str = "saved"
    notes: str = ""
    applied_date: str = ""
    tailored_id: Optional[int] = None
    match_score: Optional[int] = None


class JobApplicationUpdate(BaseModel):
    job_title: Optional[str] = None
    company: Optional[str] = None
    job_url: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    applied_date: Optional[str] = None


class JobApplicationOut(BaseModel):
    id: int
    tailored_id: Optional[int]
    job_title: Optional[str]
    company: Optional[str]
    job_url: Optional[str]
    location: Optional[str]
    status: str
    notes: Optional[str]
    match_score: Optional[int]
    applied_date: Optional[str]

    class Config:
        from_attributes = True
