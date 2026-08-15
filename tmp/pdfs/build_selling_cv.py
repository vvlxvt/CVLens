from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageTemplate,
    Paragraph,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output" / "pdf" / "Vitaliy_Volokhatiuk_Python_Backend_Developer_CV.pdf"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

FONT_DIR = Path(r"C:\Windows\Fonts")
pdfmetrics.registerFont(TTFont("CVSans", FONT_DIR / "arial.ttf"))
pdfmetrics.registerFont(TTFont("CVSans-Bold", FONT_DIR / "arialbd.ttf"))

NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#1677A8")
INK = colors.HexColor("#17212B")
MUTED = colors.HexColor("#52616B")
RULE = colors.HexColor("#B9CAD5")


class CVDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=20 * mm,
            bottomMargin=11 * mm,
            title="Vitaliy Volokhatiuk - Python Backend Developer",
            author="Vitaliy Volokhatiuk",
            subject="Python Backend Developer CV",
        )
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(PageTemplate(id="cv", frames=[frame]))


styles = {
    "name": ParagraphStyle(
        "name", fontName="CVSans-Bold", fontSize=22, leading=23,
        textColor=NAVY, alignment=TA_CENTER, spaceAfter=2.0 * mm,
    ),
    "title": ParagraphStyle(
        "title", fontName="CVSans-Bold", fontSize=10.8, leading=12.8,
        textColor=BLUE, alignment=TA_CENTER, spaceAfter=2.2 * mm,
    ),
    "contact": ParagraphStyle(
        "contact", fontName="CVSans", fontSize=8.8, leading=10.6,
        textColor=MUTED, alignment=TA_CENTER, spaceAfter=3.2 * mm,
    ),
    "section": ParagraphStyle(
        "section", fontName="CVSans-Bold", fontSize=10.3, leading=12,
        textColor=NAVY, spaceBefore=2.8 * mm, spaceAfter=1.0 * mm,
    ),
    "body": ParagraphStyle(
        "body", fontName="CVSans", fontSize=9.8, leading=13.0,
        textColor=INK, spaceAfter=1.3 * mm,
    ),
    "role": ParagraphStyle(
        "role", fontName="CVSans-Bold", fontSize=10.2, leading=12.3,
        textColor=INK, spaceBefore=1.4 * mm, spaceAfter=0.7 * mm,
    ),
    "bullet": ParagraphStyle(
        "bullet", fontName="CVSans", fontSize=9.25, leading=12.0,
        textColor=INK, leftIndent=3.4 * mm, firstLineIndent=-2.5 * mm,
        bulletIndent=0.6 * mm, spaceAfter=0.8 * mm,
    ),
    "stack": ParagraphStyle(
        "stack", fontName="CVSans", fontSize=8.55, leading=10.7,
        textColor=MUTED, spaceBefore=0.6 * mm, spaceAfter=1.3 * mm,
    ),
    "skills": ParagraphStyle(
        "skills", fontName="CVSans", fontSize=9.15, leading=11.8,
        textColor=INK, spaceAfter=0.7 * mm,
    ),
}


def section(title: str):
    return [
        Paragraph(title.upper(), styles["section"]),
        HRFlowable(width="100%", thickness=0.65, color=RULE, spaceBefore=0, spaceAfter=1.0 * mm),
    ]


def bullet(text: str):
    return Paragraph(f"<bullet>&bull;</bullet>{text}", styles["bullet"])


story = [
    Paragraph("VITALIY VOLOKHATIUK", styles["name"]),
    Paragraph("PYTHON BACKEND DEVELOPER | FASTAPI · DJANGO · AI/LLM SYSTEMS", styles["title"]),
    Paragraph(
        'Batumi, Georgia &nbsp;|&nbsp; '
        '<link href="mailto:vvlxvt@gmail.com" color="#52616B">vvlxvt@gmail.com</link> &nbsp;|&nbsp; '
        '<link href="https://www.linkedin.com/in/vitalii-volokhatiuk-818964253" color="#52616B">LinkedIn</link> &nbsp;|&nbsp; '
        '<link href="https://github.com/vvlxvt" color="#52616B">GitHub</link> &nbsp;|&nbsp; Remote / Relocation',
        styles["contact"],
    ),
]

story += section("Profile")
story.append(Paragraph(
    "Python Backend Developer experienced in building and deploying production systems. "
    "Strong in asynchronous Python, REST APIs, relational data modeling, and AI-enabled backends. Delivered an "
    "LLM-powered recruitment platform, a finance bot running in production for 3+ years with 10,000+ records, "
    "and a Django corporate platform. Owns the full delivery cycle from architecture and implementation to Docker deployment.",
    styles["body"],
))

story += section("Selected Project Experience")

story.append(KeepTogether([
    Paragraph("CVLens - AI Recruitment & Semantic Search Platform <font color='#52616B' size='8'>| Python Developer | 2024-Present</font>", styles["role"]),
    bullet("Built an end-to-end platform that parses PDF resumes, links recruiter feedback, and enables semantic candidate search."),
    bullet("Developed a multi-provider LLM extraction pipeline with OpenAI, Groq, and Ollama, including automatic fallback and 95%+ field extraction accuracy."),
    bullet("Reconstructed Telegram reply chains to match recruiter feedback with candidates, reducing manual matching by approximately 80%."),
    bullet("Designed weighted multilingual embeddings and hybrid search in Qdrant, prioritizing role and skills while supporting full-text filters."),
    bullet("Implemented FastAPI REST endpoints, prompt versioning, persistence, and database migrations."),
    Paragraph("<b>Stack:</b> Python, FastAPI, SQLAlchemy, Alembic, SQLite, Qdrant, Sentence Transformers, OpenAI, Groq, Ollama", styles["stack"]),
]))

story.append(KeepTogether([
    Paragraph("Expense Tracker - Production Telegram Finance Bot <font color='#52616B' size='8'>| Python Developer | 2022-Present</font>", styles["role"]),
    bullet("Built and operated an asynchronous personal-finance bot in production for 3+ years, storing 10,000+ expense records in a normalized database."),
    bullet("Implemented hybrid categorization using exact and fuzzy matching plus Logistic Regression, achieving 90%+ classification accuracy."),
    bullet("Created spending analytics, interactive reports, and time-based dashboards; managed schema evolution with Alembic."),
    bullet("Containerized the service with Docker and deployed it on Linux."),
    Paragraph("<b>Stack:</b> Python, Aiogram 3, asyncio, aiohttp, SQLAlchemy 2 Async, SQLite, Alembic, scikit-learn, RapidFuzz, pandas, Docker", styles["stack"]),
]))

story.append(KeepTogether([
    Paragraph("Industrial Services Website - Corporate Web Platform <font color='#52616B' size='8'>| Python Developer</font>", styles["role"]),
    bullet("Developed a Django/PostgreSQL platform for services, vacancies, content management, and automated lead processing."),
    bullet("Built admin workflows that reduced content publishing time by approximately 60%; integrated Telegram notifications and S3-compatible storage."),
    bullet("Applied CSRF protection, input validation, environment-based configuration, and Docker-based Linux deployment."),
    Paragraph("<b>Stack:</b> Python, Django, PostgreSQL, REST API, S3-compatible storage, Telegram Bot API, Docker, Linux", styles["stack"]),
]))

story += section("Technical Skills")
skill_lines = [
    ("Backend", "Python, FastAPI, Django, Django REST Framework, Flask, REST APIs, asyncio, Celery, background tasks"),
    ("Data", "PostgreSQL, MySQL, SQLite, Redis, SQLAlchemy 2.x, Alembic, Qdrant"),
    ("AI / ML", "OpenAI API, Groq API, Ollama, Sentence Transformers, RAG, prompt engineering, scikit-learn, AI agents, MCP"),
    ("Quality & Delivery", "pytest, unittest, Docker, Docker Compose, Linux, Git, CI/CD, Amazon S3, Qdrant Cloud"),
]
for label, values in skill_lines:
    story.append(Paragraph(f"<b>{label}:</b> {values}", styles["skills"]))

story += section("Education")
story.append(Paragraph(
    "<b>Master of Science in Engineering, Automatic Control Systems for Aircraft</b><br/>"
    "Siberian State University of Science and Technology, Krasnoyarsk, Russia | 2000-2006",
    styles["body"],
))

CVDocTemplate(str(OUTPUT)).build(story)
print(OUTPUT)
