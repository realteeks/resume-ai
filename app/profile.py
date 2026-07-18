"""Consolidate all of a user's resumes into a single career profile.

This profile is what we feed the LLM for fit-analysis and tailoring, so the
model sees the union of everything the user has ever told us — not just one doc.

It also exposes the *canonical* factual fields (contact + education) so callers
can overwrite anything the LLM might have changed, guaranteeing we never alter
the user's real contact details or education (requirement #5).
"""

from typing import Any


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _merge_skills(resumes: list[dict]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for data in resumes:
        for skill in data.get("skills", []) or []:
            key = _norm(skill)
            if key and key not in seen:
                seen.add(key)
                out.append(str(skill).strip())
    return out


def _merge_experience(resumes: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for data in resumes:
        for exp in data.get("experience", []) or []:
            key = (_norm(exp.get("title")), _norm(exp.get("company")),
                   _norm(exp.get("start_date")))
            if key in seen:
                continue
            seen.add(key)
            out.append(exp)
    return out


def _merge_education(resumes: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for data in resumes:
        for edu in data.get("education", []) or []:
            key = (_norm(edu.get("degree")), _norm(edu.get("institution")))
            if key in seen:
                continue
            seen.add(key)
            out.append(edu)
    return out


def _best_contact(resumes: list[dict]) -> dict:
    """Pick the contact block with the most non-empty fields."""
    best: dict = {}
    best_score = -1
    for data in resumes:
        contact = data.get("contact", {}) or {}
        score = sum(1 for v in contact.values() if str(v or "").strip())
        if score > best_score:
            best_score = score
            best = contact
    return best


def _merge_list(resumes: list[dict], field: str) -> list:
    seen: set[str] = set()
    out: list = []
    for data in resumes:
        for item in data.get(field, []) or []:
            key = _norm(item if isinstance(item, str) else item.get("name", item))
            if key and key not in seen:
                seen.add(key)
                out.append(item)
    return out


def build_profile(resume_datas: list[dict]) -> dict:
    """Merge multiple resume `data` dicts into one consolidated profile."""
    return {
        "contact": _best_contact(resume_datas),
        "summary": next((d.get("summary") for d in resume_datas if d.get("summary")), ""),
        "skills": _merge_skills(resume_datas),
        "experience": _merge_experience(resume_datas),
        "education": _merge_education(resume_datas),
        "projects": _merge_list(resume_datas, "projects"),
        "achievements": _merge_list(resume_datas, "achievements"),
        "certifications": _merge_list(resume_datas, "certifications"),
    }


def enforce_factual_fields(tailored: dict, profile: dict) -> dict:
    """Overwrite contact + education with canonical profile values.

    Guarantees the LLM cannot change the user's real contact info or education,
    even if its output drifted. (Requirement #5.)
    """
    tailored = dict(tailored)
    tailored["contact"] = profile.get("contact", {})
    tailored["education"] = profile.get("education", [])
    return tailored
