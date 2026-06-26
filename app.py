import json
import os
import certifi
from pathlib import Path

from qdrant_client.models import PointStruct
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from qdrant_client.models import Distance, VectorParams
from sentence_transformers import SentenceTransformer

load_dotenv()

# ==========================================
# CONFIG
# ==========================================
DATA_DIR = Path(__file__).resolve().parent / "extract" / "data"
OUTPUT_PATH = DATA_DIR / "cases.json"
print(OUTPUT_PATH)

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
        parts.append(f"Кандидат:\n{case['role_position']}")
    if case.get("skills"):
        parts.append(f"Навыки:\n{case['skills']}")
    if case.get("about_me_summary"):
        parts.append(f"О себе:\n{case['about_me_summary']}")
    if case.get("experience"):
        parts.append(f"Опыт:\n{case['experience']}")
    return "\n\n".join(parts).strip()


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

        # vector = embedding_model.encode(resume_text, normalize_embeddings=True).tolist()
        vector = embedding_model.encode(
            "passage: " + resume_text,
            normalize_embeddings=True,
        ).tolist()

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
                id=case.get("id"),  # int, уникален — upsert перезапишет, не дублирует
                vector=vector,
                payload=payload,
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    print(f"Загружено {len(points)} кейсов")


# ==========================================
# SEARCH
# ==========================================


def search_similar_cv(client, collection_name, embedding_model, resume_text, limit=3):
    # query_vector = embedding_model.encode(
    #     resume_text, normalize_embeddings=True
    # ).tolist()
    query_vector = embedding_model.encode(
        "query: " + resume_text,
        normalize_embeddings=True,
    ).tolist()
    return client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=limit,
    ).points


# ==========================================
# MAIN
# ==========================================

# model = SentenceTransformer("intfloat/multilingual-e5-small")
model = SentenceTransformer("intfloat/multilingual-e5-base")

# ensure_collection(client, "cv_reviews")
ensure_collection(client, "cv_reviews", force_recreate=True)

load_cases_to_qdrant(
    client=client,
    collection_name="cv_reviews",
    embedding_model=model,
    cases_file=OUTPUT_PATH,
)

# ==========================================
# ПРИМЕР ПОИСКА
# ==========================================

CV = {
    "id": 136017,
    "role_position": "",
    "skills": "Languages: Python (proficient), Bash, C/C++, SQL. Containerization & orchestration: Docker, Kubernetes, Helm. CI/CD: Ansible, Jenkins, GitLab CI/CD, AWX, Argo. Data & observability: Postgres, Kafka, ELK, Prometheus. Python: flask, django, pandas, openpyxl, aiohttp, pytest.",
    "about_me_summary": "",
    "experience": "Implementation/DevOps Engineer March 2024 – Present. SberTech Moscow, Russia. Managed Istio service mesh configurations for client and platform environments in Kubernetes. Infrastructure Engineer Jan. 2020 – Oct 2023. Yandex Moscow, Russia. Developed a tool to interact with internal monitoring systems, adopted by 10+ teams.",
    "feedback": "",
}

results = search_similar_cv(
    client,
    collection_name="cv_reviews",
    embedding_model=model,
    resume_text=build_resume_text(CV),
    limit=10,
)

for r in results:
    print("\n---")
    print("score:", r.score)
    print(r.payload["role"])
    print(r.payload["skills"])
