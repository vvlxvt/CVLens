from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from db import resumes_repo
from api.schemas import (
    ResumeIn,
    ResumeOut,
    UploadResult,
    DeleteResult,
    PaginatedResumes,
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
def upload_resumes(resumes: list[ResumeIn]):
    """
    Bulk upsert. Body is a JSON array of resume objects (e.g. the output of
    parser.py's build_cases()). Existing resume_id rows are updated in place.
    """
    saved_ids = []
    for resume in resumes:
        resumes_repo.upsert(resume.model_dump())
        saved_ids.append(resume.resume_id)

    return UploadResult(
        received=len(resumes), saved=len(saved_ids), saved_ids=saved_ids
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@app.get("/resumes", response_model=PaginatedResumes)
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
      paginated list with llm / has_feedback /
    section filters
    """
    rows, total = resumes_repo.list_paginated(
        skip=skip, limit=limit, llm=llm, has_feedback=has_feedback, section=section
    )
    return PaginatedResumes(total=total, skip=skip, limit=limit, items=rows)


@app.get("/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: str):
    """
    single record, 404 if missing
    """
    resume = resumes_repo.get_by_resume_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return resume


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
# NOTE: the literal "/resumes/irrelevant" route must be declared BEFORE
# "/resumes/{resume_id}" — otherwise FastAPI would match "irrelevant" as a
# resume_id path parameter and this route would never be reached.


@app.delete("/resumes/irrelevant", response_model=DeleteResult)
def delete_irrelevant_resumes():
    """
    Deletes every resume with no usable feedback (feedback_sections is
    null — empty/off-topic feedback, a link with no critique, etc).
    """
    deleted_ids = resumes_repo.delete_irrelevant()
    return DeleteResult(deleted=len(deleted_ids), deleted_ids=deleted_ids)


@app.delete("/resumes/{resume_id}", response_model=DeleteResult)
def delete_resume(resume_id: str):
    """
    deletes a single record, 404 if missing
    """
    deleted = resumes_repo.delete(resume_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return DeleteResult(deleted=1, deleted_ids=[resume_id])
