import os
from pathlib import Path

import certifi
import numpy as np
from huggingface_hub import snapshot_download
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchText,
    PointStruct,
    TextIndexParams,
    TokenizerType,
    VectorParams,
)
from sentence_transformers import SentenceTransformer

from db import resumes_repo
from extract.parser import parse_cv_sections

ROOT_DIR = Path(__file__).resolve().parent
COLLECTION_NAME = "cv_reviews"
EMBED_MODEL = "intfloat/multilingual-e5-base"
LOCAL_EMBED_MODEL = ROOT_DIR / "models" / "e5-base"

# role_position/skills matter most for "is this a similar candidate";
# about/experience still contribute but shouldn't dominate the match.
FIELD_WEIGHTS = {
    "role_position": 3.0,
    "skills": 3.0,
    "about_me_summary": 1.0,
    "experience": 1.0,
}

qdrant_api_key = os.getenv("QDRANT_API_KEY")
qdrant_url = os.getenv("QDRANT_URL")
qdrant_verify_ssl = os.getenv("QDRANT_VERIFY_SSL", "false").lower() in ("1", "true", "yes")

client = QdrantClient(
    url=qdrant_url,
    api_key=qdrant_api_key,
    check_compatibility=False,
    verify=certifi.where() if qdrant_verify_ssl else False,
    timeout=30,
)

_embedding_model: SentenceTransformer | None = None


def embedding_dimension(model: SentenceTransformer) -> int:
    return model.get_embedding_dimension()


def ensure_collection(vector_size: int, force_recreate: bool = False):
    exists = collection_exists()

    if exists and not force_recreate:
        return
    if exists:
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    # Full-text indexes so a skills filter (MatchText) is fast and
    # token-aware instead of a slow substring scan over the collection.
    for field in ("skills", "role"):
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field,
            field_schema=TextIndexParams(
                type="text", tokenizer=TokenizerType.WORD, min_token_len=2, lowercase=True
            ),
        )


def get_embedding_model() -> SentenceTransformer:
    """Lazy singleton — loads once per process (expensive), reused across
    requests. Also makes sure the Qdrant collection exists (cold-start safe:
    the very first search or reindex call bootstraps everything)."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    if LOCAL_EMBED_MODEL.exists():
        _embedding_model = SentenceTransformer(str(LOCAL_EMBED_MODEL))
    else:
        try:
            cached_path = snapshot_download(EMBED_MODEL, local_files_only=True)
            _embedding_model = SentenceTransformer(cached_path)
        except OSError:
            _embedding_model = SentenceTransformer(EMBED_MODEL)
        LOCAL_EMBED_MODEL.parent.mkdir(parents=True, exist_ok=True)
        _embedding_model.save(str(LOCAL_EMBED_MODEL))

    ensure_collection(embedding_dimension(_embedding_model))
    return _embedding_model


def build_resume_text(case: dict) -> str:
    parts = []
    if case.get("role_position"):
        parts.append(f"role_position:\n{case['role_position']}")
    if case.get("skills"):
        parts.append(f"skills:\n{case['skills']}")
    if case.get("about_me_summary"):
        parts.append(f"about_me_summary:\n{case['about_me_summary']}")
    if case.get("experience"):
        parts.append(f"experience:\n{case['experience']}")
    return "\n\n".join(parts).strip()


def encode_weighted_resume(case: dict, model: SentenceTransformer, prefix: str = "query") -> list[float]:
    weighted_sum = None
    for field, weight in FIELD_WEIGHTS.items():
        text = case.get(field) or ""
        if not str(text).strip():
            continue
        vec = model.encode(f"{prefix}: {field}:\n{text}", normalize_embeddings=True)
        contribution = vec * weight
        weighted_sum = contribution if weighted_sum is None else weighted_sum + contribution

    if weighted_sum is None:
        return model.encode(f"{prefix}: ", normalize_embeddings=True).tolist()

    norm = np.linalg.norm(weighted_sum)
    if norm > 0:
        weighted_sum = weighted_sum / norm
    return weighted_sum.tolist()


def resume_to_case_dict(resume) -> dict:
    return {
        "role_position": resume.role_position or "",
        "skills": resume.skills or "",
        "about_me_summary": resume.about_summary or resume.about_me_summary_raw or "",
        "experience": resume.experience or "",
        "feedback_summary": resume.feedback_summary or "",
        "feedback_sections": resume.feedback_sections or [],
        "llm": resume.feedback_llm or "",
    }


def reindex_all() -> int:
    """Rebuilds the Qdrant collection from scratch using the current DB
    contents. Returns the number of resumes actually indexed (some may be
    skipped: too little text to embed meaningfully, or a non-numeric
    resume_id that can't become a Qdrant point id)."""
    model = get_embedding_model()
    ensure_collection(embedding_dimension(model), force_recreate=True)

    points = []
    for resume in resumes_repo.list_all():
        case = resume_to_case_dict(resume)
        text = build_resume_text(case)
        if len(text.strip()) < 100:
            continue
        try:
            point_id = int(resume.resume_id)
        except (TypeError, ValueError):
            continue

        vector = encode_weighted_resume(case, model, prefix="passage")
        payload = {
            "case_id": resume.resume_id,
            "role": case["role_position"],
            "skills": case["skills"],
            "summary": case["about_me_summary"],
            "experience": case["experience"],
            # "feedback_summary": case["feedback_summary"],
            # "feedback_sections": case["feedback_sections"],
            # "llm": case["llm"],
        }
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


def collection_exists() -> bool:
    return any(c.name == COLLECTION_NAME for c in client.get_collections().collections)


def indexed_resume_ids() -> set[str]:
    """Return resume_ids that currently exist as Qdrant vector points."""
    if not collection_exists():
        return set()

    indexed: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=256,
            offset=offset,
            with_payload=["case_id"],
            with_vectors=False,
        )
        for point in points:
            case_id = (point.payload or {}).get("case_id")
            if case_id is not None:
                indexed.add(str(case_id))
        if offset is None:
            return indexed


def build_skills_filter(skills: list[str] | None) -> Filter | None:
    if not skills:
        return None
    return Filter(
        must=[FieldCondition(key="skills", match=MatchText(text=s.strip())) for s in skills if s.strip()]
    )


def parse_query_cv(pdf_path: Path) -> dict:
    """Parses a not-yet-indexed CV for a search query — no DB write, this
    is just for building the query embedding."""
    sections = parse_cv_sections(pdf_path.name, data_dir=pdf_path.parent)
    if sections.get("noeng"):
        raise ValueError("CV must be in English (>= 90% Latin characters in header)")

    return {
        "full_name": sections.get("full_name"),
        "role_position": sections.get("role_position") or "",
        "skills": sections.get("skills") or "",
        "about_me_summary": sections.get("about_summary") or sections.get("about_me_summary_raw") or "",
        "experience": sections.get("experience") or "",
    }


def search_similar(query_case: dict, limit: int = 6, skills: list[str] | None = None):
    model = get_embedding_model()
    vector = encode_weighted_resume(query_case, model, prefix="query")
    return client.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        query_filter=build_skills_filter(skills),
        limit=limit,
    ).points
