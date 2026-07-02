"""Professional resume template pool.

Each template defines a human description plus a concrete render `style`:
  font, accent, typography, page margins, which sections to show and in what
  order, whether to include a summary, and whether to enforce a single page.

`section_order` keys: summary, skills, experience, projects, education,
achievements, certifications. (summary only renders if include_summary.)
"""

DEFAULT_ORDER = ["summary", "skills", "experience", "projects", "education",
                 "achievements", "certifications"]

TEMPLATES = [
    {
        "id": "technical",
        "name": "Technical Pro",
        "recommended": True,
        "description": "Strong, ATS-first technical resume. No summary; skills up "
        "top, then experience, projects, education, achievements. Strict 1 page.",
        "style": {
            "font": "Calibri", "accent": "0B6B3A", "css_font": "system-ui",
            "heading_rule": True, "name_size": 19, "body_size": 10.5,
            "uppercase_headings": True, "margin": 0.5, "include_summary": False,
            "one_page": True,
            "section_order": ["skills", "experience", "projects", "education",
                              "achievements", "certifications"],
        },
    },
    {
        "id": "modern",
        "name": "Modern",
        "recommended": False,
        "description": "Clean sans-serif with a bold blue header and underlined "
        "section rules. Great for tech and startups.",
        "style": {
            "font": "Calibri", "accent": "1A73E8", "css_font": "system-ui",
            "heading_rule": True, "name_size": 21, "body_size": 10.5,
            "uppercase_headings": True, "margin": 0.6, "include_summary": True,
            "one_page": True, "section_order": DEFAULT_ORDER,
        },
    },
    {
        "id": "classic",
        "name": "Classic",
        "recommended": False,
        "description": "Traditional serif, conservative and ATS-safe. Ideal for "
        "finance, law, and corporate roles.",
        "style": {
            "font": "Georgia", "accent": "000000", "css_font": "Georgia,serif",
            "heading_rule": True, "name_size": 22, "body_size": 10.5,
            "uppercase_headings": True, "margin": 0.7, "include_summary": True,
            "one_page": True, "section_order": DEFAULT_ORDER,
        },
    },
    {
        "id": "minimal",
        "name": "Minimal",
        "recommended": False,
        "description": "Lots of whitespace, no rules, understated. For design and "
        "creative roles.",
        "style": {
            "font": "Helvetica", "accent": "444444", "css_font": "Helvetica,Arial,sans-serif",
            "heading_rule": False, "name_size": 23, "body_size": 10.5,
            "uppercase_headings": False, "margin": 0.8, "include_summary": True,
            "one_page": True, "section_order": DEFAULT_ORDER,
        },
    },
    {
        "id": "executive",
        "name": "Executive",
        "recommended": False,
        "description": "Refined navy serif with generous spacing. For senior and "
        "leadership roles.",
        "style": {
            "font": "Georgia", "accent": "1F3A5F", "css_font": "Georgia,serif",
            "heading_rule": True, "name_size": 23, "body_size": 11,
            "uppercase_headings": True, "margin": 0.7, "include_summary": True,
            "one_page": True, "section_order": DEFAULT_ORDER,
        },
    },
    {
        "id": "compact",
        "name": "Compact",
        "recommended": False,
        "description": "Tighter spacing and smaller type to fit dense content on "
        "one page.",
        "style": {
            "font": "Calibri", "accent": "333333", "css_font": "system-ui",
            "heading_rule": True, "name_size": 18, "body_size": 10,
            "uppercase_headings": True, "margin": 0.5, "include_summary": True,
            "one_page": True, "section_order": DEFAULT_ORDER,
        },
    },
]

EMPTY_RESUME_DATA = {
    "contact": {"full_name": "", "email": "", "phone": "", "location": "",
                "linkedin": "", "website": ""},
    "summary": "",
    "skills": [],
    "experience": [{"title": "", "company": "", "location": "", "start_date": "",
                    "end_date": "", "bullets": [""]}],
    "education": [{"degree": "", "institution": "", "location": "",
                   "graduation_date": "", "details": ""}],
    "projects": [],
    "achievements": [],
    "certifications": [],
}


# Every section the layout system knows about (summary is toggle-able).
ALL_SECTIONS = ["summary", "skills", "experience", "projects", "education",
                "achievements", "certifications"]

SECTION_LABELS = {
    "summary": "Professional Summary", "skills": "Skills",
    "experience": "Experience", "projects": "Projects",
    "education": "Education", "achievements": "Achievements",
    "certifications": "Certifications",
}


def get_template(template_id: str) -> dict | None:
    return next((t for t in TEMPLATES if t["id"] == template_id), None)


def get_style(template_id: str) -> dict:
    t = get_template(template_id) or TEMPLATES[0]
    return t["style"]


def default_layout(template_id: str) -> dict:
    """The starting per-resume layout for a template: a full section order
    (all sections present so any can be reordered/enabled) + summary on/off."""
    style = get_style(template_id)
    order = list(style.get("section_order", ALL_SECTIONS))
    if "summary" not in order:
        order.insert(0, "summary")
    for key in ALL_SECTIONS:
        if key not in order:
            order.append(key)
    return {"section_order": order,
            "include_summary": bool(style.get("include_summary", True))}


def effective_style(template_id: str, layout: dict | None = None) -> dict:
    """Template style with the per-resume layout overrides applied."""
    style = dict(get_style(template_id))
    if layout:
        if layout.get("section_order"):
            style["section_order"] = list(layout["section_order"])
        if "include_summary" in layout:
            style["include_summary"] = bool(layout["include_summary"])
    return style


def validate_layout(layout: dict) -> dict:
    """Sanitize a client-supplied layout: known sections only, no dupes."""
    order, seen = [], set()
    for key in layout.get("section_order", []):
        if key in ALL_SECTIONS and key not in seen:
            seen.add(key)
            order.append(key)
    for key in ALL_SECTIONS:  # keep it complete so nothing silently vanishes
        if key not in seen:
            order.append(key)
    return {"section_order": order,
            "include_summary": bool(layout.get("include_summary", True))}
