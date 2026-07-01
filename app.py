import argparse
import json
import os
import certifi
from pathlib import Path

import numpy as np
from qdrant_client.models import PointStruct
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from qdrant_client.models import Distance, VectorParams
from huggingface_hub import snapshot_download
from sentence_transformers import SentenceTransformer

from extract.parser import parse_cv_sections


load_dotenv()

# ==========================================
# CONFIG
# ==========================================
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "extract" / "data"
OUTPUT_PATH = DATA_DIR / "cases.json"
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


# ==========================================
# TEXT BUILDER
# Кейс плоский: {id, role_position, skills, about_me_summary, experience, feedback}
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
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    sections = parse_cv_sections(pdf_path.name, data_dir=pdf_path.parent)
    if sections.get("noeng"):
        raise ValueError("CV must be in English (>= 90% Latin characters in header)")

    return sections


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
        weighted_sum = contribution if weighted_sum is None else weighted_sum + contribution

    if weighted_sum is None:
        return embedding_model.encode(f"{prefix}: ", normalize_embeddings=True).tolist()

    norm = np.linalg.norm(weighted_sum)
    if norm > 0:
        weighted_sum = weighted_sum / norm
    return weighted_sum.tolist()


# ==========================================
# INDEXING
# ==========================================


def load_cases_to_qdrant(
    client, collection_name, embedding_model, cases_file=OUTPUT_PATH
):
    with open(cases_file, "r", encoding="utf-8") as f:
        cases = json.load(f)

    points = []

    for case in cases:
        resume_text = build_resume_text(case)

        if len(resume_text.strip()) < 100:
            continue

        vector = encode_weighted_resume(case, embedding_model, prefix="passage")

        payload = {
            "case_id": case.get("id"),
            "role": case.get("role_position", ""),
            "skills": case.get("skills", ""),
            "summary": case.get("about_me_summary", ""),
            "resume": resume_text,
            "feedback": case.get("feedback", ""),
        }

        points.append(
            PointStruct(
                id=case.get("id"),
                vector=vector,
                payload=payload,
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    print(f"Загружено {len(points)} кейсов")


# ==========================================
# SEARCH
# ==========================================


def search_similar_cv(
    client,
    collection_name,
    embedding_model,
    case: dict,
    limit=3,
):
    query_vector = encode_weighted_resume(case, embedding_model, prefix="query")
    return client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=limit,
    ).points


def print_search_results(results) -> None:
    for r in results:
        print("\n---")
        print("score:", round(r.score, 4))
        print("role:", r.payload.get("role") or "—")
        print("skills:", (r.payload.get("skills") or "—")[:200])
        feedback = r.payload.get("feedback") or ""
        if feedback:
            print("feedback:", feedback[:500] + ("..." if len(feedback) > 500 else ""))


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
        help="Recreate collection and reload cases from cases.json",
    )
    parser.add_argument("--limit", type=int, default=10, help="Number of results")
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
            cases_file=OUTPUT_PATH,
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
    )
    print_search_results(results)


if __name__ == "__main__":
    main()
