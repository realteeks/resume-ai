"""Render structured resume data to PDF with a TRUE one-page fit check.

Unlike the .docx path (which can only estimate), fpdf2 lets us render and read
the actual page count. `compute_fit` renders at progressively smaller sizes and
returns the largest that genuinely fits one page — the real guarantee. The same
(body_size, scale) is then reused for the .docx so both stay consistent.
"""

import io

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.resume_templates import effective_style

LETTER_W = 612.0  # points
LETTER_H = 792.0

# fpdf2 core fonts only (no TTF bundling needed).
_FONT_MAP = {"Georgia": "Times", "Times": "Times", "Courier": "Courier"}

# (body_size, spacing_scale) candidates, largest first.
_FIT_LADDER = [(10.5, 1.0), (10.0, 0.85), (9.5, 0.72), (9.0, 0.6), (8.5, 0.5), (8.0, 0.42)]


def _font(name: str) -> str:
    return _FONT_MAP.get(name, "Helvetica")


def _rgb(hexstr: str):
    return tuple(int(hexstr[i : i + 2], 16) for i in (0, 2, 4))


# Core PDF fonts are latin-1 only; map common unicode the LLM emits, drop the rest.
_REPL = {
    "•": "-", "–": "-", "—": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "→": "->",
    " ": " ", "−": "-", "‐": "-", "‑": "-",
}


def _s(text) -> str:
    t = str(text or "")
    for k, v in _REPL.items():
        t = t.replace(k, v)
    return t.encode("latin-1", "ignore").decode("latin-1")


def _build(data: dict, style: dict, body: float, scale: float,
           margin_in: float | None = None) -> FPDF:
    margin = (margin_in if margin_in is not None else style["margin"]) * 72
    pdf = FPDF(orientation="P", unit="pt", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=margin)
    pdf.set_margins(margin, margin, margin)
    pdf.add_page()
    font = _font(style["font"])
    accent = _rgb(style["accent"])
    usable = LETTER_W - 2 * margin
    lh = body * 1.3  # line height

    contact = data.get("contact", {}) or {}

    # Name
    pdf.set_font(font, "B", style["name_size"])
    pdf.set_text_color(*accent)
    pdf.cell(0, style["name_size"] * 1.1, _s(contact.get("full_name") or "Your Name"),
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Contact line
    bits = [contact.get(k) for k in ("email", "phone", "location", "linkedin", "website")]
    line = "   |   ".join(b for b in bits if b)
    if line:
        pdf.set_font(font, "", body - 1)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, lh, _s(line), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4 * scale)

    def heading(text):
        pdf.ln(5 * scale)
        pdf.set_font(font, "B", body + 1.5)
        pdf.set_text_color(*accent)
        label = text.upper() if style["uppercase_headings"] else text
        pdf.cell(0, lh, _s(label), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if style["heading_rule"]:
            y = pdf.get_y()
            pdf.set_draw_color(*accent)
            pdf.set_line_width(0.6)
            pdf.line(margin, y, LETTER_W - margin, y)
            pdf.ln(2 * scale)
        pdf.set_text_color(20, 20, 20)

    def role_line(bold_text, rest, right):
        pdf.set_font(font, "B", body)
        pdf.set_text_color(20, 20, 20)
        left_w = usable * 0.72
        pdf.cell(left_w, lh, _trunc(pdf, _s(bold_text + rest), left_w, font, "B", body),
                 new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font(font, "I", body - 1)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(usable - left_w, lh, _s(right), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(20, 20, 20)

    def para(text, size=None, italic=False, gray=False, md=False):
        pdf.set_font(font, "I" if italic else "", size or body)
        pdf.set_text_color(*((90, 90, 90) if gray else (20, 20, 20)))
        pdf.multi_cell(usable, lh, _s(text), markdown=md, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def bullet(text):
        # markdown=True renders **bold** impact phrases the model emits.
        pdf.set_font(font, "", body)
        pdf.set_text_color(20, 20, 20)
        x0 = pdf.get_x()
        pdf.cell(12, lh, "-", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.multi_cell(usable - 12, lh, _s(text), markdown=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(x0)

    def section(key):
        if key == "summary" and style["include_summary"] and data.get("summary"):
            heading("Summary"); para(data["summary"], md=True)
        elif key == "skills" and data.get("skills"):
            heading("Skills"); para("   |   ".join(data["skills"]))
        elif key == "experience" and data.get("experience"):
            heading("Experience")
            for job in data["experience"]:
                company = job.get("company", "")
                dates = " - ".join(d for d in [job.get("start_date"), job.get("end_date")] if d)
                role_line(job.get("title", ""), f"  -  {company}" if company else "", dates)
                if job.get("location"):
                    para(job["location"], size=body - 1, italic=True, gray=True)
                for b in job.get("bullets", []):
                    if b:
                        bullet(b)
        elif key == "projects" and data.get("projects"):
            heading("Projects")
            for p in data["projects"]:
                desc = f" - {p['description']}" if p.get("description") else ""
                para(f"{p.get('name','')}{desc}")
                for b in p.get("bullets", []) or []:
                    if b:
                        bullet(b)
        elif key == "education" and data.get("education"):
            heading("Education")
            for e in data["education"]:
                inst = e.get("institution", "")
                role_line(e.get("degree", ""), f"  -  {inst}" if inst else "",
                          e.get("graduation_date", ""))
                if e.get("details"):
                    para(e["details"], size=body - 1, gray=True)
        elif key == "achievements" and data.get("achievements"):
            heading("Achievements")
            for a in data["achievements"]:
                if a:
                    bullet(a)
        elif key == "certifications" and data.get("certifications"):
            heading("Certifications")
            for c in data["certifications"]:
                if c:
                    bullet(c)

    for key in style["section_order"]:
        section(key)
    return pdf


def _trunc(pdf, text, width, font, fstyle, size):
    """Truncate a single-line string with an ellipsis to fit width (points)."""
    pdf.set_font(font, fstyle, size)
    if pdf.get_string_width(text) <= width:
        return text
    while text and pdf.get_string_width(text + "...") > width:
        text = text[:-1]
    return text + "..."


def compute_fit(data: dict, template: str, layout: dict | None = None) -> dict:
    """Render at decreasing sizes (and tighter margins as a last resort) and
    return the fit that truly puts the resume on ONE page. The returned dict
    {body, scale, margin, pages} is reused for the .docx so both formats match.
    """
    style = effective_style(template, layout)
    if not style.get("one_page"):
        pdf = _build(data, style, style["body_size"], 1.0)
        return {"body": style["body_size"], "scale": 1.0,
                "margin": style["margin"], "pages": pdf.page_no()}
    last = None
    for body, scale in _FIT_LADDER:
        # Tighten margins once the font is already small.
        margin = style["margin"] if body >= 9 else min(style["margin"], 0.4)
        pdf = _build(data, style, body, scale, margin_in=margin)
        pages = pdf.page_no()
        last = {"body": body, "scale": scale, "margin": margin, "pages": pages}
        if pages <= 1:
            return last
    return last  # content too long to fit even at the smallest size


def render_pdf(data: dict, template: str = "technical", layout: dict | None = None):
    """Return (pdf_bytes, pages) using the fitted size."""
    style = effective_style(template, layout)
    fit = compute_fit(data, template, layout)
    pdf = _build(data, style, fit["body"], fit["scale"], margin_in=fit["margin"])
    return bytes(pdf.output()), pdf.page_no()
