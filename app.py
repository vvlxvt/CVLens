import argparse
import os
import certifi
from pathlib import Path

import numpy as np
from qdrant_client.models import PointStruct
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from qdrant_client.models import Distance, VectorParams
from qdrant_client.models import Filter, FieldCondition, MatchText, TextIndexParams, TokenizerType
from huggingface_hub import snapshot_download
from sentence_transformers import SentenceTransformer

from extract.parser import parse_cv_sections
from db.models import Resume
from db import resumes_repo


load_dotenv()

# ==========================================
# CONFIG
# ==========================================
ROOT_DIR = Path(__file__).resolve().parent
COLLECTION_NAME = "cv_reviews"
EMBED_MODEL = "intfloat/multilingual-e5-base"
LOCAL_EMBED_MODEL = ROOT_DIR / "models" / "e5-base"

FIELD_WEIGHTS = {
    "role_position": 3.0,
    "skills": 3.0,
    "about_me_summary": 1.0,
    "experience": 1.0,
}

qdrant_api_key = os.getenv("QDRANT_API_KEY")
qdrant_url = os.getenv("QDRANT_URL")
qdrant_verify_ssl = os.getenv("QDRANT_VERIFY_SSL", "false").lower() in (
    "1",
    "true",
    "yes",
)

# ==========================================
# QDRANT
# ==========================================

client = QdrantClient(
    url=qdrant_url,
    api_key=qdrant_api_key,
    check_compatibility=False,
    verify=certifi.where() if qdrant_verify_ssl else False,
    timeout=30,
)


def ensure_collection(client, collection_name, vector_size=768, force_recreate=False):
    exists = any(
        c.name == collection_name for c in client.get_collections().collections
    )

    if exists and not force_recreate:
        print(f"Collection '{collection_name}' already exists")
        return

    if exists:
        client.delete_collection(collection_name)

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    print(f"Collection '{collection_name}' {'recreated' if exists else 'created'}")

    ensure_text_indexes(client, collection_name)


def ensure_text_indexes(client, collection_name):
    """
    Full-text payload indexes so MatchText filters (e.g. skills contains
    "python" AND "django") are fast and token-aware, instead of falling
    back to a slow exact-substring scan over the whole collection.
    """
    for field in ("skills", "role"):
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=TextIndexParams(
                type="text",
                tokenizer=TokenizerType.WORD,
                min_token_len=2,
                lowercase=True,
            ),
        )


# ==========================================
# DB <-> "case" dict adapter
# encode_weighted_resume / build_resume_text work on a flat dict with
# keys: role_position, skills, about_me_summary, experience — this keeps
# those functions unchanged whether the data came from a DB row or a
# freshly-parsed PDF (which doesn't have a DB row yet).
# ==========================================


def resume_to_case_dict(resume: Resume) -> dict:
    return {
        "id": resume.resume_id,
        "role_position": resume.role_position or "",
        "skills": resume.skills or "",
        "about_me_summary": resume.about_summary or resume.about_me_summary_raw or "",
        "experience": resume.experience or "",
        "feedback": resume.feedback_raw or "",
        "feedback_summary": resume.feedback_summary or "",
        "feedback_sections": resume.feedback_sections or [],
    }


# ==========================================
# TEXT BUILDER
# ==========================================


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


def load_embedding_model() -> SentenceTransformer:
    if LOCAL_EMBED_MODEL.exists():
        print(f"Loading local embedding model from {LOCAL_EMBED_MODEL}")
        return SentenceTransformer(str(LOCAL_EMBED_MODEL))

    try:
        cached_path = snapshot_download(EMBED_MODEL, local_files_only=True)
        print(f"Loading embedding model from Hugging Face cache: {cached_path}")
        model = SentenceTransformer(cached_path)
    except OSError:
        print(f"Downloading embedding model {EMBED_MODEL}...")
        model = SentenceTransformer(EMBED_MODEL)

    LOCAL_EMBED_MODEL.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(LOCAL_EMBED_MODEL))
    print(f"Saved model to {LOCAL_EMBED_MODEL}")
    return model


def embedding_dimension(model: SentenceTransformer) -> int:
    if hasattr(model, "get_embedding_dimension"):
        return model.get_embedding_dimension()
    return model.get_embedding_dimension()


def load_cv_from_pdf(pdf_path: str | Path) -> dict:
    """Parses a not-yet-indexed CV for a search query (no DB write)."""
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    sections = parse_cv_sections(pdf_path.name, data_dir=pdf_path.parent)
    if sections.get("noeng"):
        raise ValueError("CV must be in English (>= 90% Latin characters in header)")

    return {
        "role_position": sections.get("role_position") or "",
        "skills": sections.get("skills") or "",
        "about_me_summary": sections.get("about_summary")
        or sections.get("about_me_summary_raw")
        or "",
        "experience": sections.get("experience") or "",
    }


def encode_weighted_resume(
    case: dict,
    embedding_model: SentenceTransformer,
    prefix: str = "query",
) -> list[float]:
    weighted_sum = None

    for field, weight in FIELD_WEIGHTS.items():
        text = case.get(field) or ""
        if not str(text).strip():
            continue

        vec = embedding_model.encode(
            f"{prefix}: {field}:\n{text}",
            normalize_embeddings=True,
        )
        contribution = vec * weight
        weighted_sum = (
            contribution if weighted_sum is None else weighted_sum + contribution
        )

    if weighted_sum is None:
        return embedding_model.encode(f"{prefix}: ", normalize_embeddings=True).tolist()

    norm = np.linalg.norm(weighted_sum)
    if norm > 0:
        weighted_sum = weighted_sum / norm
    return weighted_sum.tolist()


# ==========================================
# INDEXING
# ==========================================


def load_cases_to_qdrant(client, collection_name, embedding_model):
    """Reads every resume from the SQLite DB and (re)indexes it in Qdrant."""
    resumes = resumes_repo.list_all()

    points = []

    for resume in resumes:
        case = resume_to_case_dict(resume)
        resume_text = build_resume_text(case)

        if len(resume_text.strip()) < 100:
            continue

        try:
            point_id = int(resume.resume_id)
        except (TypeError, ValueError):
            print(f"Skipping resume_id={resume.resume_id!r}: not a valid Qdrant point id (must be int or UUID)")
            continue

        vector = encode_weighted_resume(case, embedding_model, prefix="passage")

        payload = {
            "case_id": resume.resume_id,
            "role": case["role_position"],
            "skills": case["skills"],
            "summary": case["about_me_summary"],
            "experience": case["experience"],
            "resume": resume_text,
            "feedback": case["feedback"],
            "feedback_summary": case["feedback_summary"],
            "feedback_sections": case["feedback_sections"],
        }

        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )
        )

    if points:
        client.upsert(collection_name=collection_name, points=points)
    print(f"Загружено {len(points)} кейсов")


# ==========================================
# SEARCH
# ==========================================


def build_skills_filter(skills: list[str] | None) -> Filter | None:
    """
    AND-filter: every given skill must appear (as a token) in the
    'skills' payload field. E.g. ["python", "django"] only matches
    resumes whose skills field contains both words.
    """
    if not skills:
        return None
    return Filter(
        must=[
            FieldCondition(key="skills", match=MatchText(text=skill.strip()))
            for skill in skills
            if skill.strip()
        ]
    )


def search_similar_cv(
    client,
    collection_name,
    embedding_model,
    case: dict,
    limit=3,
    skills_filter: list[str] | None = None,
):
    query_vector = encode_weighted_resume(case, embedding_model, prefix="query")
    return client.query_points(
        collection_name=collection_name,
        query=query_vector,
        query_filter=build_skills_filter(skills_filter),
        limit=limit,
    ).points


def print_search_results(results) -> None:
    for r in results:
        print("\n---")
        print("score:", round(r.score, 4))
        print("role:", r.payload.get("role") or "—")
        print("skills:", (r.payload.get("skills") or "—")[:200])
        print("experience:", (r.payload.get("experience") or "—")[:200])
        feedback = r.payload.get("feedback") or ""
        if feedback:
            print("feedback:", feedback[:500] + ("..." if len(feedback) > 500 else ""))
        feedback_summary = r.payload.get("feedback_summary") or ""
        if feedback_summary:
            print("feedback_summary:", feedback_summary)
        sections = r.payload.get("feedback_sections") or []
        if sections:
            print("feedback_sections:", ", ".join(sections))


# ==========================================
# MAIN
# ==========================================


def main():
    parser = argparse.ArgumentParser(description="CV similarity search in Qdrant")
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Path to CV PDF — parse and search similar cases",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Recreate collection and reload cases from the SQLite DB",
    )
    parser.add_argument("--limit", type=int, default=10, help="Number of results")
    parser.add_argument(
        "--skills",
        type=str,
        help='Comma-separated skills that must ALL be present, e.g. --skills "python,django"',
    )
    args = parser.parse_args()

    model = load_embedding_model()
    vector_size = embedding_dimension(model)

    if args.reindex:
        ensure_collection(
            client, COLLECTION_NAME, vector_size=vector_size, force_recreate=True
        )
        load_cases_to_qdrant(
            client=client,
            collection_name=COLLECTION_NAME,
            embedding_model=model,
        )
    else:
        ensure_collection(client, COLLECTION_NAME, vector_size=vector_size)

    if not args.pdf:
        parser.print_help()
        return

    case = load_cv_from_pdf(args.pdf)
    resume_text = build_resume_text(case)

    if len(resume_text.strip()) < 50:
        raise ValueError("Parsed CV is too short — check PDF content and sections")

    print("Parsed CV text:\n")
    print(resume_text)
    print("\n" + "=" * 60)
    print("Similar cases (role_position + skills weighted 3x):\n")

    results = search_similar_cv(
        client,
        collection_name=COLLECTION_NAME,
        embedding_model=model,
        case=case,
        limit=args.limit,
        skills_filter=[s.strip() for s in args.skills.split(",")] if args.skills else None,
    )
    print_search_results(results)


if __name__ == "__main__":
    main()