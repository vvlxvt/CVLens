"""Shared helpers for the CV-review / search / rules endpoints."""

import json
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from openai import OpenAIError
from pydantic import ValidationError
from sqlalchemy.orm import Session

import vector_search
from api.schemas import (
    CVReviewReport,
    ReviewRuleGeneration,
    SearchMatchOut,
    SimilarReviewExample,
)
from db import resumes_repo
from db.models import Resume
from response import generate_response


def _clip(text: str | None, limit: int = 1800) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _json_clip(value, limit: int = 1800) -> str:
    return _clip(json.dumps(value, ensure_ascii=False, default=str), limit)


def _is_llm_error_message(message: str) -> bool:
    return any(
        marker in message
        for marker in (
            "LLM",
            "OpenAI",
            "Groq",
            "Ollama",
            "JSON",
            "cloud fallback",
            "fallback is disabled",
            "quota",
        )
    )


def _llm_provider_error_detail(error: OpenAIError) -> str:
    return f"LLM provider error ({type(error).__name__}): {error}"


def _review_about_text(cv_data) -> str:
    if hasattr(cv_data, "about_me_summary_raw"):
        return cv_data.about_me_summary_raw or cv_data.about_me_summary or ""
    return cv_data.get("about_me_summary_raw") or cv_data.get("about_me_summary") or ""


def _resume_intro_text(resume: Resume) -> str:
    return "\n".join(
        part
        for part in (
            resume.full_name,
            resume.role_position,
            resume.about_me_summary_raw,
            resume.about_summary,
        )
        if part
    )


def _match_from_point(session: Session, point) -> SearchMatchOut | None:
    payload = point.payload or {}
    case_id = payload.get("case_id")
    if case_id is None:
        return None

    resume = resumes_repo.get_by_resume_id(session, str(case_id))
    if resume is None:
        return None

    return SearchMatchOut(
        resume_id=resume.resume_id,
        score=point.score,
        role_position=resume.role_position,
        skills=resume.skills,
        about_me_summary=resume.about_summary or resume.about_me_summary_raw,
        about_me_summary_raw=resume.about_me_summary_raw,
        experience=resume.experience,
        feedback_raw=resume.feedback_raw,  # полный текст
        feedback_summary=resume.feedback_summary,
        feedback_sections=resume.feedback_sections or [],
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
            if _is_llm_error_message(str(e)):
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not parse CV with the configured LLMs: {e}",
                ) from e
            raise HTTPException(status_code=400, detail=str(e)) from e

def _build_review_prompt(
    parsed_cv: dict,
    examples: list[SimilarReviewExample],
    language: str = "ru",
    active_rules: list[dict] | None = None,
) -> tuple[str, str]:
    output_language = "English" if language == "en" else "Russian"
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
About: {_clip(_review_about_text(example))}
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

Active review rules:
{_json_clip(active_rules or [], 2400) if active_rules else "No active review rules yet."}

New CV to review:
Full name: {_clip(parsed_cv.get("full_name"), 300)}
Role: {_clip(parsed_cv.get("role_position"), 500)}
Skills: {_clip(parsed_cv.get("skills"))}
About: {_clip(_review_about_text(parsed_cv))}
Experience: {_clip(parsed_cv.get("experience"), 2800)}

Write a strict, practical CV review in {output_language}.
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
- All human-readable JSON values must be written in {output_language}.
- Follow active review rules when they are relevant, but do not invent facts.
""".strip()

    return system, user


def _generate_structured_review(prompt: str, system: str) -> tuple[CVReviewReport, str]:
    raw_review, applied_model = generate_response(
        prompt,
        json_mode=True,
        system=system,
        allow_ollama=False,
    )
    try:
        return CVReviewReport.model_validate(raw_review), applied_model
    except ValidationError as first_error:
        retry_prompt = f"""
Your previous response did not match the required schema.
It was:
{_json_clip(raw_review, 1600)}

Return ONLY one JSON object, not an array and not a list of strings.
The object must match exactly this shape:
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

Original task:
{prompt}
""".strip()
        retry_raw_review, retry_model = generate_response(
            retry_prompt,
            json_mode=True,
            system=system,
            model=applied_model,
            allow_ollama=False,
        )
        try:
            return CVReviewReport.model_validate(retry_raw_review), retry_model
        except ValidationError as retry_error:
            raise retry_error from first_error

def _build_rule_rebuild_prompt(
    current_rules: list[dict],
    reviewed_cases,
) -> tuple[str, str]:
    system = """
You improve a CV review prompt by distilling user feedback into durable review rules.
Return ONLY valid JSON matching the requested schema.
""".strip()

    case_blocks = []
    for row in reviewed_cases:
        case_blocks.append(
            {
                "review_id": row.id,
                "rating": row.user_rating,
                "user_comment": row.user_comment,
                "parsed_cv": row.parsed_cv,
                "generated_review": row.review,
                "similar_examples": row.similar_examples,
                "llm": row.llm,
            }
        )

    user = f"""
Current active rules:
{_json_clip(current_rules, 5000)}

Reviewed cases with user feedback:
{_json_clip(case_blocks, 14000)}

Create the next rule set for future CV reviews.

Return JSON with exactly this shape:
{{
  "rules": [
    {{
      "title": "short rule name",
      "description": "specific instruction for future CV reviews",
      "confidence": 0.0,
      "source_review_ids": [1, 2]
    }}
  ],
  "added_rules": [{{"title": "...", "description": "...", "confidence": 0.0, "source_review_ids": []}}],
  "changed_rules": [{{"title": "...", "description": "...", "before": "...", "after": "...", "source_review_ids": []}}],
  "removed_rules": [{{"title": "...", "reason": "...", "source_review_ids": []}}],
  "summary": "short summary of the new rule set",
  "diff_summary": "short git-like summary of what changed"
}}

Rules:
- Prefer a small, high-signal rule set over many weak rules.
- Use agree feedback as confirmation, partially_agree as refinement, disagree as correction.
- Keep rules actionable and observable from CV text.
- Do not include rules that require private knowledge unavailable in a CV.
- confidence is a number from 0 to 1.
- source_review_ids must reference the provided reviewed cases.
""".strip()
    return system, user


def _normalize_rule_sources(rule: dict, allowed_ids: set[int]) -> dict:
    normalized = dict(rule)
    normalized_ids = []
    for source_id in normalized.get("source_review_ids") or []:
        try:
            normalized_id = int(source_id)
        except (TypeError, ValueError):
            continue
        if normalized_id in allowed_ids:
            normalized_ids.append(normalized_id)
    normalized["source_review_ids"] = normalized_ids
    return normalized


def _normalize_rule_payload(
    generated: ReviewRuleGeneration,
    source_review_ids: list[int],
) -> ReviewRuleGeneration:
    allowed_ids = set(source_review_ids)
    return ReviewRuleGeneration(
        rules=[_normalize_rule_sources(rule, allowed_ids) for rule in generated.rules],
        added_rules=[
            _normalize_rule_sources(rule, allowed_ids) for rule in generated.added_rules
        ],
        changed_rules=[
            _normalize_rule_sources(rule, allowed_ids) for rule in generated.changed_rules
        ],
        removed_rules=[
            _normalize_rule_sources(rule, allowed_ids) for rule in generated.removed_rules
        ],
        summary=generated.summary,
        diff_summary=generated.diff_summary,
    )