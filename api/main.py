import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from db import resumes_repo
from extract.parser import build_cases
from api.schemas import (
    TelegramExportIn,
    ResumeCard,
    ResumeOut,
    UploadResult,
    DeleteResult,
    PaginatedResumeCards,
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


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@app.post("/resumes/upload", response_model=UploadResult)
async def upload_resumes(file: UploadFile = File(...)):
    """
    Accepts result.json uploaded as a file (multipart/form-data, field name
    "file") — the exact file Telegram's chat export produces, unmodified.
    Does the full pipeline server-side: parses any CV PDFs the messages
    reference (these must already exist under extract/data/, same as
    running parser.py from the CLI), runs the LLM intro/feedback
    extraction, and upserts the results into the DB.

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
