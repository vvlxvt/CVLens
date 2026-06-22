import json
import os
import certifi
from uuid import uuid4
from pathlib import Path

from qdrant_client.models import PointStruct
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from qdrant_client.models import Distance, VectorParams
from sentence_transformers import SentenceTransformer

# Загружает переменные из файла .env в окружение
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
    cloud_inference=True,
    check_compatibility=False,
    verify=certifi.where() if qdrant_verify_ssl else False,
)


def create_collection_if_not_exists(client, collection_name):
    collections = client.get_collections().collections
    exists = any(c.name == collection_name for c in collections)

    if not exists:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=384,  # важно для e5-small
                distance=Distance.COSINE,
            ),
        )
        print(f"Collection '{collection_name}' created")
    else:
        print(f"Collection '{collection_name}' already exists")


def build_resume_text(case: dict) -> str:
    data = case.get("data", {})

    return f"""
                Кандидат:
                {data.get("role_position", "")}

                Навыки:
                {data.get("skills", "")}

                О себе:
                {data.get("about_me_summary", "")}

                Опыт:
                {data.get("experience", "")}
                                            """.strip()


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

        vector = embedding_model.encode(resume_text, normalize_embeddings=True).tolist()

        payload = {
            "case_id": case.get("id"),
            "candidate": case.get("data", {}).get("role_position"),
            "skills": case.get("data", {}).get("skills"),
            "summary": case.get("data", {}).get("about_me_summary"),
            "resume": resume_text,
            "feedback": case.get("feedback", ""),
        }

        points.append(PointStruct(id=str(uuid4()), vector=vector, payload=payload))

    client.upsert(collection_name=collection_name, points=points)

    print(f"Загружено {len(points)} кейсов")


model = SentenceTransformer("intfloat/multilingual-e5-small")

create_collection_if_not_exists(client, "cv_reviews")

load_cases_to_qdrant(
    client=client,
    collection_name="cv_reviews",
    embedding_model=model,
    cases_file=OUTPUT_PATH,
)

# ==========================================
# SEARCH
# ==========================================


def search_similar_cv(client, collection_name, embedding_model, resume_text, limit=10):
    query_vector = embedding_model.encode(
        resume_text, normalize_embeddings=True
    ).tolist()

    return client.query_points(
        collection_name=collection_name, query=query_vector, limit=limit
    ).points


CV = {
    "id": 136854,
    "data": {
      "role_position": "",
      "skills": "Programming Languages: Java 17+, Python, SQL Backend: Spring Boot, Spring Security, Spring Data JPA, Hibernate, REST APIs Databases: PostgreSQL, MySQL, H2 Infrastructure & Tools: Docker, Docker Compose, Git, Linux (basic), Maven, Postman, IntelliJ IDEA Caching & Messaging: Redis, Kafka (basic), Hazelcast (basic) Testing: JUnit, Mockito, Testcontainers (basic) Languages: English — B2, Czech — B2, Russian — Native",
      "about_me_summary": "Backend-oriented Software Engineer focused on microservices, integrations, and REST API development. Background includes commercial Java backend experience (REST APIs, PostgreSQL performance tuning) and a final-year BSc in Enterprise Systems at CTU/ČVUT FEL. Built a Docker Compose–based Mars data platform with Spring Boot microservices and ETL pipelines, storing metadata in PostgreSQL and assets in S3-compatible MinIO, with semantic search (Qdrant) and optional LLM-generated descriptions. Based in Prague (CZ), free access to the labor market (student); open to full-time or part-time, and B2B via IČO if required (can be arranged).",
      "experience": "Software Engineer August 2020 – September 2021 Step Logic Moscow, Russia Tech stack: Java, Spring Boot, PostgreSQL, JUnit 5, Mockito, Git, Jira • Contributed to the backend of internal employee-facing tools, implementing REST endpoints and improving stability of common workflows • Optimized performance-critical SQL queries by restructuring joins and implementing indexes, resulting in 10-20% faster execution times for key endpoints (measured via EXPLAIN ANALYZE on staging) • Assisted with endpoint access configuration (role/permission mapping) and resolved recurring authorization issues (e.g., 401/403) based on log analysis • Refactored parts of the service layer to improve structure, reduce duplicated logic, and simplify maintenance • Added unit tests with JUnit and Mockito to modules lacking automated coverage • Worked in a Scrum team using Jira: sprint planning, daily stand-ups, code reviews, and documentation updates Projects Mars Data Analysis Platform Sep 2025 – Present Tech stack: Java, Spring Boot, PostgreSQL, MinIO, Qdrant, LangChain, OpenAI API, Next.js, Docker Developed a data processing and retrieval platform based on NASA Mars mission image archives, including backend services, ETL workflows, structured storage, and similarity search functionality • Designed and implemented a Spring Boot backend service for ingesting, processing, and serving structured metadata derived from public NASA datasets • Built a modular ETL pipeline that downloads and processes dozens of separate datasets and tens of thousands of Mars images, with filtering by mission, instrument, and spacecraft, persisting metadata in PostgreSQL and storing image objects in MinIO • Implemented a basic vector-search mechanism using Qdrant to enable similarity-based retrieval of related images • Integrated a lightweight LLM component (LangChain + OpenAI API) to generate short descriptive summaries for selected Mars images • Developed a simple Next.js interface for browsing processed data, viewing metadata, and previewing images • Containerized the entire system using Docker Compose to ensure reproducible multi-service development and straightforward deployment Education Czech Technical University in Prague (CTU) Prague, Czech Republic Bachelor of Science: Software Engineering and Technology Aug. 2022 – May 2026"
    },
    "feedback": "А вы где находитесь?"
  }

results = search_similar_cv(
    client,
    collection_name="cv_reviews",
    embedding_model=model,
    resume_text=build_resume_text({"data": CV}),
    limit=10,
)


for r in results:
    print("\n---")
    print("score:", r.score)
    print(r.payload["candidate"])
    print(r.payload["skills"])
