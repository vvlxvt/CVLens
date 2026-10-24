"""Resume CRUD, upload pipeline, vector search and recompute endpoints."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from openai import OpenAIError
from pydantic import ValidationError
from qdrant_client.http.exceptions import (
    ApiException,
    ResponseHandlingException,
    UnexpectedResponse,
)

import vector_search
from api.deps import DbSession
from api.reviewing import (
    _llm_provider_error_detail,
    _match_from_point,
    _parse_uploaded_query_cv,
    _resume_intro_text,
)
from api.schemas import (
    DeleteResult,
    LlmOptions,
    PaginatedResumeCards,
    ParsedCVOut,
    ReindexResult,
    RecomputeFeedbackIn,
    ResumeCard,
    ResumeOut,
    SearchResponse,
    TelegramExportIn,
    UploadResult,
)
from core.config import settings
from core.rate_limit import llm_rate_limiter, read_rate_limiter
from db import resumes_repo
from extract.parser import DATA_DIR, build_cases
from response import (
    configured_llm_options,
    extract_feedback_data,
    extract_intro_data,
    LLM_PROVIDER,
    MODEL,
    OLLAMA_MODEL,
    OPENROUTER_MODEL,
)

router = APIRouter(tags=["resumes"])


@router.post(
    "/resumes/upload",
    response_model=UploadResult,
    dependencies=[Depends(llm_rate_limiter.check)],
)
def upload_resumes(
    file: Annotated[
        UploadFile,
        File(description="result.json — Telegram's chat export, unmodified"),
    ],
    pdf_files: Annotated[
        list[UploadFile] | None,
        File(description="CV PDFs referenced by result.json's messages (optional)"),
    ] = None,
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
    the whole export finishes) — if the run fails partway through, whatever
    was already processed is still safely in the DB. Re-uploading the same
    file afterwards only reprocesses what's missing/changed.

    Runs as a sync endpoint so the whole (long, blocking) LLM pipeline
    executes in the threadpool instead of blocking the event loop.
    """
    raw = file.file.read()
    if len(raw) > settings.max_result_json_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"result.json is too large ({len(raw)} bytes; "
                f"limit {settings.max_result_json_bytes})"
            ),
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400, detail=f"'{file.filename}' is not valid JSON: {e}"
        ) from e

    try:
        export = TelegramExportIn(**data)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=[
                {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
                for err in e.errors()
            ],
        ) from e

    if pdf_files and len(pdf_files) > settings.max_pdf_files_per_upload:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Too many PDF files ({len(pdf_files)}; "
                f"limit {settings.max_pdf_files_per_upload})"
            ),
        )

    saved_pdf_names: list[str] = []
    skipped_pdf_names: list[str] = []
    total_bytes = len(raw)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for pdf in pdf_files or []:
        # Strip any directory component the browser/client sent — never
        # trust a client-supplied path (path traversal, e.g. "../../etc/x").
        safe_name = Path(pdf.filename or "").name
        if not safe_name.lower().endswith(".pdf"):
            skipped_pdf_names.append(pdf.filename or "")
            continue
        content = pdf.file.read()
        total_bytes += len(content)
        if len(content) > settings.max_pdf_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"'{safe_name}' exceeds the per-file limit "
                    f"({settings.max_pdf_bytes} bytes)"
                ),
            )
        # Validate PDF magic bytes — reject files with .pdf extension that
        # are not actually PDF documents (e.g. renamed executables, text
        # files, or empty uploads).
        if not content.startswith(b"%PDF-"):
            skipped_pdf_names.append(pdf.filename or "")
            continue
        if total_bytes > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Upload exceeds the total limit "
                    f"({settings.max_upload_bytes} bytes)"
                ),
            )
        (DATA_DIR / safe_name).write_bytes(content)
        saved_pdf_names.append(safe_name)

    saved_ids: list[str] = []
    skipped_ids: list[str] = []

    def _on_case_processed(case: dict):
        if case.get("feedback_sections") is None:
            skipped_ids.append(case["resume_id"])
            return
        # A short dedicated transaction per CV: each finished CV is durable
        # even if the rest of the batch fails later.
        with get_session() as session:
            resumes_repo.upsert(session, case)
        saved_ids.append(case["resume_id"])

    try:
        cases = build_cases(export.messages, on_case_processed=_on_case_processed)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return UploadResult(
        received=len(cases),
        saved=len(saved_ids),
        saved_ids=saved_ids,
        skipped_ids=skipped_ids,
        saved_pdf_files=saved_pdf_names,
        skipped_pdf_files=skipped_pdf_names,
    )


@router.post(
    "/resumes/search",
    response_model=SearchResponse,
    dependencies=[Depends(read_rate_limiter.check)],
)
def search_resumes(
    session: DbSession,
    file: Annotated[
        UploadFile,
        File(
            description="Query CV (PDF) to find similar historically-reviewed candidates for"
        ),
    ],
    limit: Annotated[
        int,
        Form(
            description="Total number of matches to return (top match + up to limit-1 others)"
        ),
    ] = 6,
    skills: Annotated[
        str,
        Form(description='Comma-separated skills that must ALL be present, e.g. "python,django"'),
    ] = "",
):
    """
    Parses the uploaded CV (no DB write — it's a live query, not saved),
    embeds it, and finds the most similar historically-reviewed resumes
    in Qdrant. The single best match comes back as top_match (with its
    feedback); any further matches (up to limit-1, capped at 5 for display)
    come back as other_matches.
    """
    content = file.file.read()
    query_case = _parse_uploaded_query_cv(file, content)

    skills_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    points = vector_search.search_similar(query_case, limit=limit, skills=skills_list)

    matches = [m for point in points if (m := _match_from_point(session, point))]
    top_match = matches[0] if matches else None
    other_matches = matches[1:6]  # up to 5 more, shown as cards below

    return SearchResponse(
        parsed_cv=ParsedCVOut(**query_case),
        top_match=top_match,
        other_matches=other_matches,
    )


@router.post(
    "/resumes/reindex",
    response_model=ReindexResult,
    dependencies=[Depends(llm_rate_limiter.check)],
)
def reindex_resumes():
    """
    Recreates the Qdrant collection from scratch using the current DB
    contents. Blocking and can take a while (re-embeds every resume);
    call this after a batch of new uploads, not on every search.
    """
    count = vector_search.reindex_all()
    return ReindexResult(indexed=count)


@router.get(
    "/resumes",
    response_model=PaginatedResumeCards,
    dependencies=[Depends(read_rate_limiter.check)],
)
def list_resumes(
    session: DbSession,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    llm: Annotated[str | None, Query(description="Filter by feedback_llm")] = None,
    has_feedback: Annotated[
        bool | None,
        Query(
            description="true: only resumes with feedback_sections set; false: only without"
        ),
    ] = None,
    section: Annotated[
        str | None,
        Query(description="Only resumes whose feedback_sections contains this value"),
    ] = None,
):
    """
    Card-grid listing: one lightweight card per resume (role_position,
    feedback_summary, feedback_sections, llm). Click a card and fetch
    GET /resumes/{resume_id} for the full record.
    """
    rows, total = resumes_repo.list_paginated(
        session, skip=skip, limit=limit, llm=llm, has_feedback=has_feedback, section=section
    )
    try:
        indexed_ids = vector_search.indexed_resume_ids()
    except (
        ApiException,
        OSError,
        ResponseHandlingException,
        TimeoutError,
        UnexpectedResponse,
    ) as e:
        print(f"[api] Could not read vector index status: {type(e).__name__}: {e}")
        indexed_ids = set()

    items = [
        ResumeCard(
            resume_id=r.resume_id,
            role_position=r.role_position,
            feedback_summary=r.feedback_summary,
            feedback_sections=r.feedback_sections,
            llm=r.feedback_llm,
            is_indexed=r.resume_id in indexed_ids,
        )
        for r in rows
    ]
    return PaginatedResumeCards(total=total, skip=skip, limit=limit, items=items)


@router.get(
    "/resumes/llms",
    response_model=list[str],
    dependencies=[Depends(read_rate_limiter.check)],
)
def list_resume_llms(session: DbSession):
    return resumes_repo.list_feedback_llms(session)


@router.get(
    "/resumes/llm-options",
    response_model=LlmOptions,
    dependencies=[Depends(read_rate_limiter.check)],
)
def get_llm_options(session: DbSession):
    feedback_models = resumes_repo.list_feedback_llms(session)
    available_models = configured_llm_options()
    preferred_model = {
        "official_openai": OPENROUTER_MODEL,
        "openai": MODEL,
    }.get(LLM_PROVIDER, OLLAMA_MODEL)
    return LlmOptions(
        feedback_models=feedback_models,
        available_models=available_models,
        preferred_model=preferred_model,
        local_model=OLLAMA_MODEL,
    )


@router.get(
    "/resumes/{resume_id}",
    response_model=ResumeOut,
    dependencies=[Depends(read_rate_limiter.check)],
)
def get_resume(session: DbSession, resume_id: str):
    resume = resumes_repo.get_by_resume_id(session, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return resume


@router.post(
    "/resumes/{resume_id}/feedback/recompute",
    response_model=ResumeOut,
    dependencies=[Depends(llm_rate_limiter.check)],
)
def recompute_resume_feedback(
    session: DbSession, resume_id: str, body: RecomputeFeedbackIn
):
    if body.model not in configured_llm_options():
        raise HTTPException(
            status_code=400,
            detail=f"Model {body.model!r} is not configured for recompute",
        )

    resume = resumes_repo.get_by_resume_id(session, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    if not resume.feedback_raw:
        raise HTTPException(
            status_code=400,
            detail=f"Resume {resume_id!r} has no raw feedback to recompute",
        )

    try:
        about_fields, about_model, about_prompt_id = extract_intro_data(
            _resume_intro_text(resume),
            model=body.model,
        )
        fields, applied_model, prompt_id = extract_feedback_data(
            resume.feedback_raw,
            model=body.model,
        )
    except OpenAIError as e:
        raise HTTPException(
            status_code=502,
            detail=_llm_provider_error_detail(e),
        ) from e
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned an invalid recompute payload: {e}",
        ) from e

    updated_about = resumes_repo.update_about(
        session,
        resume_id=resume_id,
        full_name=about_fields.get("full_name") or resume.full_name,
        role_position=about_fields.get("role_position") or resume.role_position,
        about_summary=about_fields.get("summary") or resume.about_summary,
        llm=about_model,
        prompt_id=about_prompt_id,
    )
    if not updated_about:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")

    updated = resumes_repo.update_feedback(
        session,
        resume_id=resume_id,
        feedback_summary=fields.get("feedback_summary"),
        feedback_sections=fields.get("feedback_sections"),
        llm=applied_model,
        prompt_id=prompt_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")

    refreshed = resumes_repo.get_by_resume_id(session, resume_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return refreshed


@router.delete(
    "/resumes/{resume_id}",
    response_model=DeleteResult,
    dependencies=[Depends(read_rate_limiter.check)],
)
def delete_resume(session: DbSession, resume_id: str):
    deleted = resumes_repo.delete(session, resume_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return DeleteResult(deleted=1, deleted_ids=[resume_id])