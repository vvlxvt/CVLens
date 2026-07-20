import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from db import resumes_repo, prompts_repo
from extract.parser import build_cases, DATA_DIR
from api.schemas import (
    TelegramExportIn,
    ResumeCard,
    ResumeOut,
    UploadResult,
    DeleteResult,
    PaginatedResumeCards,
    PromptOut,
    PromptCreateIn,
)

app = FastAPI(
    title="CVLens API",
    version="0.1.0",
    description="CRUD access to the parsed-CV / recruiter-feedback DB, for the future web UI.",
)

# Allows a separately-hosted web frontend (different origin/port) to call this
# API from the browser. Tighten allow_origins to the real frontend URL(s)
# once you have one, instead of leaving it wide open.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup templates and static files
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web"))
app.mount(
    "/static/css",
    StaticFiles(directory=str(BASE_DIR / "web" / "css")),
    name="static_css",
)
app.mount(
    "/static/js", StaticFiles(directory=str(BASE_DIR / "web" / "js")), name="static_js"
)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Web UI Routes
# ---------------------------------------------------------------------------


@app.get("/")
def serve_index(request: Request):
    """Serve the main CV listing page."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request},
    )


@app.get("/upload")
def serve_upload(request: Request):
    """Serve the upload page."""
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"request": request},
    )


@app.get("/prompts")
def serve_prompts(request: Request):
    """Serve the prompts management page."""
    return templates.TemplateResponse(
            request=request,
            name="prompts.html",
            context={"request": request},
        )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@app.post("/resumes/upload", response_model=UploadResult)
async def upload_resumes(
    file: UploadFile = File(
        ..., description="result.json — Telegram's chat export, unmodified"
    ),
    pdf_files: list[UploadFile] = File(
        default=[],
        description="CV PDFs referenced by result.json's messages (optional)",
    ),
):
    """
    Accepts result.json uploaded as a file (multipart/form-data, field name
    "file") — the exact file Telegram's chat export produces, unmodified —
    plus, optionally, the CV PDFs it references (field name "pdf_files",
    repeatable). Uploaded PDFs are saved under extract/data/ using just
    their filename (any directory component in the upload is stripped).

    If a PDF a message references isn't among pdf_files, the pipeline falls
    back to whatever's already on disk under extract/data/ — so re-uploading
    just result.json (no PDFs) still works as before, as long as the files
    are already there from an earlier upload or a direct server-side copy.

    Does the full pipeline server-side: parses the CV PDFs, runs the LLM
    intro/feedback extraction, and upserts the results into the DB.

    CVs with no clear feedback (feedback_sections is null) are parsed but
    not saved — their resume_ids come back in skipped_ids.

    Each CV is saved to the DB the moment it's processed (not batched until
    the whole export finishes) — if the run fails partway through (e.g. the
    LLM provider runs out of tokens and no fallback is configured), whatever
    was already processed is still safely in the DB. Re-uploading the same
    file afterwards only reprocesses what's missing/changed.

    Note: this runs the full LLM pipeline synchronously, so a large export
    can take a while to respond — set a generous client-side timeout.
    """
    raw = await file.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400, detail=f"'{file.filename}' is not valid JSON: {e}"
        )

    try:
        export = TelegramExportIn(**data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())

    saved_pdf_names = []
    skipped_pdf_names = []
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for pdf in pdf_files:
        # Strip any directory component the browser/client sent — never
        # trust a client-supplied path (path traversal, e.g. "../../etc/x").
        safe_name = Path(pdf.filename).name
        if not safe_name.lower().endswith(".pdf"):
            skipped_pdf_names.append(pdf.filename)
            continue
        content = await pdf.read()
        (DATA_DIR / safe_name).write_bytes(content)
        saved_pdf_names.append(safe_name)

    saved_ids = []
    skipped_ids = []

    def _on_case_processed(case: dict):
        if case.get("feedback_sections") is None:
            skipped_ids.append(case["resume_id"])
            return
        resumes_repo.upsert(case)
        saved_ids.append(case["resume_id"])

    cases = build_cases(export.messages, on_case_processed=_on_case_processed)

    return UploadResult(
        received=len(cases),
        saved=len(saved_ids),
        saved_ids=saved_ids,
        skipped_ids=skipped_ids,
        saved_pdf_files=saved_pdf_names,
        skipped_pdf_files=skipped_pdf_names,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@app.get("/resumes", response_model=PaginatedResumeCards)
def list_resumes(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    llm: Optional[str] = Query(None, description="Filter by about_llm or feedback_llm"),
    has_feedback: Optional[bool] = Query(
        None,
        description="true: only resumes with feedback_sections set; false: only without",
    ),
    section: Optional[str] = Query(
        None, description="Only resumes whose feedback_sections contains this value"
    ),
):
    """
    Card-grid listing: one lightweight card per resume (role_position,
    feedback_summary, feedback_sections, llm). Click a card and fetch
    GET /resumes/{resume_id} for the full record.
    """
    rows, total = resumes_repo.list_paginated(
        skip=skip, limit=limit, llm=llm, has_feedback=has_feedback, section=section
    )
    items = [
        ResumeCard(
            resume_id=r.resume_id,
            role_position=r.role_position,
            feedback_summary=r.feedback_summary,
            feedback_sections=r.feedback_sections,
            llm=r.feedback_llm,
        )
        for r in rows
    ]
    return PaginatedResumeCards(total=total, skip=skip, limit=limit, items=items)


@app.get("/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: str):
    resume = resumes_repo.get_by_resume_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return resume


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@app.delete("/resumes/{resume_id}", response_model=DeleteResult)
def delete_resume(resume_id: str):
    deleted = resumes_repo.delete(resume_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return DeleteResult(deleted=1, deleted_ids=[resume_id])


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
# "intro_extraction" (about) and "feedback_extraction" (feedback) are the
# only two prompt names the pipeline actually uses — response.py fetches
# whichever is latest at call time. Every save here creates a NEW version
# (v1, v2, ...) rather than overwriting, so prompt history is never lost
# and DB rows already processed with an older version stay traceable via
# their about_prompt_id/feedback_prompt_id.

PROMPT_NAMES = {"intro_extraction", "feedback_extraction"}

# Each prompt's user_template is filled via str.format() with exactly this
# placeholder — validated at save time so a typo (e.g. {into_text}) is
# caught immediately instead of blowing up mid-batch later.
PROMPT_PLACEHOLDER = {
    "intro_extraction": "intro_text",
    "feedback_extraction": "feedback_text",
}


def _check_known_prompt_name(name: str):
    if name not in PROMPT_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown prompt name {name!r}. Expected one of {sorted(PROMPT_NAMES)}",
        )


@app.get("/prompts/{name}", response_model=list[PromptOut])
def list_prompt_versions(name: str):
    """All saved versions for `name`, oldest first — for the web form's
    version history navigation (prev/next through past prompts)."""
    _check_known_prompt_name(name)
    return prompts_repo.list_all(name)


@app.get("/prompts/{name}/latest", response_model=PromptOut)
def get_latest_prompt(name: str):
    _check_known_prompt_name(name)
    prompt = prompts_repo.get_latest(name)
    if prompt is None:
        raise HTTPException(
            status_code=404,
            detail=f"No prompt saved yet for {name!r}. Run `python -m db.seed_prompts` or create one here.",
        )
    return prompt


@app.post("/prompts/{name}", response_model=PromptOut)
def create_prompt_version(name: str, body: PromptCreateIn):
    _check_known_prompt_name(name)

    placeholder = PROMPT_PLACEHOLDER[name]
    try:
        body.user_template.format(**{placeholder: ""})
    except (KeyError, IndexError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=(
                f"user_template is invalid: {e}. "
                f"It must be fillable with just the '{{{placeholder}}}' placeholder "
                f"(no other {{...}} placeholders)."
            ),
        )

    return prompts_repo.create_next_version(name, body.system_text, body.user_template)
