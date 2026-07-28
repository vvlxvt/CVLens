import json
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from qdrant_client.http.exceptions import (
    ApiException,
    ResponseHandlingException,
    UnexpectedResponse,
)

import vector_search
from api.schemas import (
    CVReviewReport,
    DeleteResult,
    LlmOptions,
    PaginatedResumeCards,
    ParsedCVOut,
    PromptCreateIn,
    PromptOut,
    RecomputeFeedbackIn,
    ReindexResult,
    ResumeCard,
    ResumeOut,
    ReviewFeedbackIn,
    ReviewFeedbackResult,
    ReviewResponse,
    SearchMatchOut,
    SearchResponse,
    SimilarReviewExample,
    TelegramExportIn,
    UploadResult,
)
from db import prompts_repo, resumes_repo, reviews_repo
from extract.parser import DATA_DIR, build_cases
from response import (
    LLM_PROVIDER,
    MODEL,
    OLLAMA_MODEL,
    configured_llm_options,
    extract_feedback_data,
    generate_response,
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


@app.get("/search")
def serve_search(request: Request):
    """Serve the vector similarity search page."""
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={"request": request},
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@app.post("/resumes/upload", response_model=UploadResult)
async def upload_resumes(
    file: Annotated[
        UploadFile,
        File(
            description="result.json — Telegram's chat export, unmodified",
        ),
    ],
    pdf_files: Annotated[
        list[UploadFile] | None,
        File(
            description="CV PDFs referenced by result.json's messages (optional)",
        ),
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
        ) from e

    try:
        export = TelegramExportIn(**data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    saved_pdf_names = []
    skipped_pdf_names = []
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for pdf in pdf_files or []:
        # Strip any directory component the browser/client sent — never
        # trust a client-supplied path (path traversal, e.g. "../../etc/x").
        safe_name = Path(pdf.filename).name
        if not safe_name.lower().endswith(".pdf"):
            skipped_pdf_names.append(pdf.filename)
            continue
        content = await pdf.read()
        # print("UPLOAD DATA_DIR:", DATA_DIR.resolve())
        (DATA_DIR / safe_name).write_bytes(content)
        saved_pdf_names.append(safe_name)

    saved_ids = []
    skipped_ids = []

    def _on_case_processed(case: dict):
        # print(case.keys())
        # print(case)
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


def _match_from_point(point) -> SearchMatchOut | None:
    payload = point.payload or {}
    case_id = payload.get("case_id")
    if case_id is None:
        return None

    resume = resumes_repo.get_by_resume_id(str(case_id))
    if resume is None:
        return None

    return SearchMatchOut(
        resume_id=resume.resume_id,
        score=point.score,
        role_position=resume.role_position,
        skills=resume.skills,
        about_me_summary=resume.about_summary or resume.about_me_summary_raw,
        experience=resume.experience,
        feedback_raw=resume.feedback_raw,  # полный текст
        feedback_summary=resume.feedback_summary,
        feedback_sections=resume.feedback_sections,
        llm=resume.feedback_llm,
    )


def _parse_uploaded_query_cv(file: UploadFile, content: bytes) -> dict:
    filename = Path(file.filename or "uploaded_cv.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail=f"'{filename}' must be a .pdf file")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / filename
        tmp_path.write_bytes(content)
        try:
            return vector_search.parse_query_cv(tmp_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/resumes/search", response_model=SearchResponse)
async def search_resumes(
    file: Annotated[
        UploadFile,
        File(
            description="Query CV (PDF) to find similar historically-reviewed candidates for",
        ),
    ],
    limit: Annotated[
        int,
        Form(
            description="Total number of matches to return (top match + up to limit-1 others)",
        ),
    ] = 6,
    skills: Annotated[
        str,
        Form(
            description='Comma-separated skills that must ALL be present, e.g. "python,django"',
        ),
    ] = "",
):
    """
    Parses the uploaded CV (no DB write — it's a live query, not saved),
    embeds it, and finds the most similar historically-reviewed resumes
    in Qdrant. The single best match comes back as top_match (with its
    feedback); any further matches (up to limit-1, capped at 5 for display)
    come back as other_matches.

    Equivalent to the old CLI's `python vector_search.py --pdf ... --limit ... --skills ...`.
    """
    content = await file.read()
    query_case = _parse_uploaded_query_cv(file, content)

    skills_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    points = vector_search.search_similar(query_case, limit=limit, skills=skills_list)

    matches = [match for point in points if (match := _match_from_point(point))]
    top_match = matches[0] if matches else None
    other_matches = matches[1:6]  # up to 5 more, shown as cards below

    return SearchResponse(
        parsed_cv=ParsedCVOut(**query_case),
        top_match=top_match,
        other_matches=other_matches,
    )


def _clip(text: str | None, limit: int = 1800) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _build_review_prompt(
    parsed_cv: dict,
    examples: list[SimilarReviewExample],
) -> tuple[str, str]:
    system = """
You are a senior CV reviewer trained to imitate the judgment of an experienced HR reviewer.
Use the historical examples as labeled examples, but review only the new CV.
Return ONLY valid JSON matching the requested schema.
""".strip()

    example_blocks = []
    for index, example in enumerate(examples, start=1):
        example_blocks.append(
            f"""
Example {index}
Role: {_clip(example.role_position, 500)}
Skills: {_clip(example.skills)}
About: {_clip(example.about_me_summary)}
Experience: {_clip(example.experience)}
HR feedback summary: {_clip(example.feedback_summary)}
HR raw feedback: {_clip(example.feedback_raw)}
Feedback sections: {", ".join(example.feedback_sections or [])}
Similarity score: {example.score:.4f}
""".strip()
        )

    user = f"""
Historical HR-reviewed examples:
{chr(10).join(example_blocks) if example_blocks else "No similar historical examples were found."}

New CV to review:
Full name: {_clip(parsed_cv.get("full_name"), 300)}
Role: {_clip(parsed_cv.get("role_position"), 500)}
Skills: {_clip(parsed_cv.get("skills"))}
About: {_clip(parsed_cv.get("about_me_summary"))}
Experience: {_clip(parsed_cv.get("experience"), 2800)}

Write a strict, practical CV review in Russian.
Calibrate the judgment and level of detail from the historical HR feedback examples.

Return JSON with exactly this shape:
{{
  "summary": "short overall assessment",
  "score": 0,
  "sections": {{
    "role_position": {{"status": "good|weak|missing", "comment": "...", "suggestion": "..."}},
    "skills": {{"status": "good|weak|missing", "comment": "...", "suggestion": "..."}},
    "about_me_summary": {{"status": "good|weak|missing", "comment": "...", "suggestion": "..."}},
    "experience": {{"status": "good|weak|missing", "comment": "...", "suggestion": "..."}},
    "formatting": {{"status": "good|weak|missing", "comment": "...", "suggestion": "..."}}
  }},
  "risks": ["..."],
  "recommended_actions": ["..."]
}}

Rules:
- score is an integer from 0 to 10.
- Do not invent facts that are not present in the CV.
- If evidence is missing, mark the relevant section as "missing" or "weak".
- Be specific and actionable.
""".strip()

    return system, user


@app.post("/resumes/review", response_model=ReviewResponse)
async def review_resume(
    file: Annotated[
        UploadFile,
        File(description="Query CV (PDF) to review with similar historical examples"),
    ],
    limit: Annotated[
        int,
        Form(description="Number of similar reviewed CVs to use as few-shot examples"),
    ] = 5,
    skills: Annotated[
        str,
        Form(
            description='Optional comma-separated skills filter, e.g. "python,django"',
        ),
    ] = "",
):
    """
    Reviews a new CV using retrieval-augmented few-shot learning:
    parse uploaded PDF, retrieve similar historically reviewed CVs, pass
    their CV+feedback pairs as examples to the LLM, and return a structured
    review. This endpoint does not save anything to the DB yet.
    """
    content = await file.read()
    query_case = _parse_uploaded_query_cv(file, content)

    example_limit = max(1, min(limit, 10))
    skills_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    points = vector_search.search_similar(
        query_case,
        limit=example_limit,
        skills=skills_list,
    )
    examples = [
        SimilarReviewExample(**match.model_dump())
        for point in points
        if (match := _match_from_point(point))
    ]

    system, prompt = _build_review_prompt(query_case, examples)
    try:
        raw_review, applied_model = generate_response(
            prompt,
            json_mode=True,
            system=system,
        )
        review = CVReviewReport.model_validate(raw_review)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned an invalid review payload: {e}",
        ) from e
    review_id = reviews_repo.create(
        uploaded_filename=Path(file.filename or "uploaded_cv.pdf").name,
        parsed_cv=ParsedCVOut(**query_case).model_dump(),
        similar_examples=[example.model_dump() for example in examples],
        review=review.model_dump(),
        llm=applied_model,
    )

    return ReviewResponse(
        review_id=review_id,
        parsed_cv=ParsedCVOut(**query_case),
        examples=examples,
        review=review,
        llm=applied_model,
    )


@app.post("/resumes/reindex", response_model=ReindexResult)
def reindex_resumes():
    """
    Recreates the Qdrant collection from scratch using the current DB
    contents — equivalent to the old CLI's `python vector_search.py --reindex`.
    Blocking and can take a while (re-embeds every resume); call this after
    a batch of new uploads, not on every search.
    """
    count = vector_search.reindex_all()
    return ReindexResult(indexed=count)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@app.get("/resumes", response_model=PaginatedResumeCards)
def list_resumes(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    llm: Annotated[
        str | None,
        Query(description="Filter by feedback_llm"),
    ] = None,
    has_feedback: Annotated[
        bool | None,
        Query(
            description="true: only resumes with feedback_sections set; false: only without",
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
        skip=skip, limit=limit, llm=llm, has_feedback=has_feedback, section=section
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


@app.get("/resumes/llms", response_model=list[str])
def list_resume_llms():
    return resumes_repo.list_feedback_llms()


@app.get("/resumes/llm-options", response_model=LlmOptions)
def get_llm_options():
    feedback_models = resumes_repo.list_feedback_llms()
    available_models = list(dict.fromkeys([*configured_llm_options(), *feedback_models]))
    return LlmOptions(
        feedback_models=feedback_models,
        available_models=available_models,
        preferred_model=MODEL if LLM_PROVIDER == "openai" else OLLAMA_MODEL,
        local_model=OLLAMA_MODEL,
    )


@app.get("/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: str):
    resume = resumes_repo.get_by_resume_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return resume


@app.post("/resumes/{resume_id}/feedback/recompute", response_model=ResumeOut)
def recompute_resume_feedback(resume_id: str, body: RecomputeFeedbackIn):
    resume = resumes_repo.get_by_resume_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    if not resume.feedback_raw:
        raise HTTPException(
            status_code=400,
            detail=f"Resume {resume_id!r} has no raw feedback to recompute",
        )

    try:
        fields, applied_model, prompt_id = extract_feedback_data(
            resume.feedback_raw,
            model=body.model,
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned an invalid feedback payload: {e}",
        ) from e

    updated = resumes_repo.update_feedback(
        resume_id=resume_id,
        feedback_summary=fields.get("feedback_summary"),
        feedback_sections=fields.get("feedback_sections"),
        llm=applied_model,
        prompt_id=prompt_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")

    refreshed = resumes_repo.get_by_resume_id(resume_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return refreshed


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@app.delete("/resumes/{resume_id}", response_model=DeleteResult)
def delete_resume(resume_id: str):
    deleted = resumes_repo.delete(resume_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id!r} not found")
    return DeleteResult(deleted=1, deleted_ids=[resume_id])


@app.post("/reviews/{review_id}/feedback", response_model=ReviewFeedbackResult)
def add_review_feedback(review_id: int, body: ReviewFeedbackIn):
    updated = reviews_repo.add_feedback(
        review_id=review_id,
        user_rating=body.rating,
        user_comment=body.comment,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Review {review_id!r} not found")
    return ReviewFeedbackResult(updated=True)


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
        ) from e

    return prompts_repo.create_next_version(name, body.system_text, body.user_template)
