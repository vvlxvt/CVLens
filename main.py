from pathlib import Path
from uuid import uuid4

from ollama import chat
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    FilterSelector,
    PayloadSchemaType
)
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter
)
import os
import certifi
from dotenv import load_dotenv

# Загружает переменные из файла .env в окружение
load_dotenv()


# ==========================================
# CONFIG
# ==========================================

COLLECTION_NAME = "personal_memory"
EMBED_MODEL = (
    "intfloat/multilingual-e5-small"
)
LOCAL_EMBED_MODEL = "models/e5-small"
OLLAMA_MODEL = "llama3.2:3b"
KNOWLEDGE_DIR = "knowledge"
DEFAULT_SOURCE = "english.txt"
qdrant_api_key = os.getenv("QDRANT_API_KEY")
qdrant_url = os.getenv("QDRANT_URL")
ollama_num_gpu = int(os.getenv("OLLAMA_NUM_GPU", "0"))
qdrant_verify_ssl = (
    os.getenv("QDRANT_VERIFY_SSL", "false")
    .lower()
    in ("1", "true", "yes")
)

# ==========================================
# EMBEDDINGS
# ==========================================

print("Loading embedding model...")

model_path = (
    LOCAL_EMBED_MODEL
    if Path(LOCAL_EMBED_MODEL).exists()
    else EMBED_MODEL
)

embedding_model = SentenceTransformer(model_path)

if not Path(LOCAL_EMBED_MODEL).exists():
    embedding_model.save(LOCAL_EMBED_MODEL)

VECTOR_SIZE = (
    embedding_model
    .get_embedding_dimension()
)

# ==========================================
# QDRANT
# ==========================================

client = QdrantClient(
    url=qdrant_url,
    api_key=qdrant_api_key,
    cloud_inference=True,
    check_compatibility=False,
    verify=certifi.where() if qdrant_verify_ssl else False
)

collections = [
    c.name
    for c in client.get_collections().collections
]

if COLLECTION_NAME not in collections:

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE
        )
    )

    print("Collection created")

client.create_payload_index(
    collection_name=COLLECTION_NAME,
    field_name="source",
    field_schema=PayloadSchemaType.KEYWORD
)

# ==========================================
# TEXT SPLITTER
# ==========================================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=300,
    chunk_overlap=50
)


def source_filter(source):

    return Filter(
        must=[
            FieldCondition(
                key="source",
                match=MatchValue(value=source)
            )
        ]
    )


def encode_passage(text):

    return embedding_model.encode(
        f"passage: {text}"
    ).tolist()


def encode_query(text):

    return embedding_model.encode(
        f"query: {text}"
    ).tolist()

# ==========================================
# INDEXING
# ==========================================

def index_documents():

    txt_files = Path(
        KNOWLEDGE_DIR
    ).glob("*.txt")

    for file_path in txt_files:

        print(
            f"Indexing {file_path.name}"
        )

        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=FilterSelector(
                filter=source_filter(file_path.name)
            )
        )

        points = []

        text = file_path.read_text(
            encoding="utf-8"
        )

        chunks = splitter.split_text(
            text
        )

        for chunk in chunks:

            vector = encode_passage(chunk)

            points.append(
                PointStruct(
                    id=str(uuid4()),
                    vector=vector,
                    payload={
                        "text": chunk,
                        "source": file_path.name
                    }
                )
            )

        if points:

            client.upsert(
                collection_name=COLLECTION_NAME,
                points=points
            )

            print(
                f"Indexed {len(points)} chunks from {file_path.name}"
            )


def source_is_indexed(source=DEFAULT_SOURCE):

    result = client.count(
        collection_name=COLLECTION_NAME,
        count_filter=source_filter(source),
        exact=True
    )

    return result.count > 0


# ==========================================
# SEARCH
# ==========================================

def search_context(
    query,
    limit=5,
    source=DEFAULT_SOURCE
):

    query_vector = encode_query(query)

    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=source_filter(source),
        limit=limit
    )

    return response.points


# ==========================================
# RAG
# ==========================================

def ask_rag(question, source=DEFAULT_SOURCE):

    results = search_context(
        question,
        source=source
    )

    if not results:
        return (
            f"В базе нет контекста из {source}. "
            "Сначала запусти индексирование: выбери 1 - index."
        )

    context_blocks = []

    for item in results:

        text = (
            item.payload["text"]
        )

        score = (
            round(item.score, 3)
        )

        source_name = item.payload.get("source", "unknown")

        context_blocks.append(
            f"[source={source_name}, score={score}]\n{text}"
        )

    context = "\n\n".join(
        context_blocks
    )

    prompt = f"""
Ты персональный ассистент.

Отвечай только на основании
контекста.

Если данных недостаточно,
так и скажи.

КОНТЕКСТ:

{context}

ВОПРОС:

{question}
"""

    response = chat(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        options={
            "num_gpu": ollama_num_gpu
        }
    )

    return (
        response["message"]
        ["content"]
    )


# ==========================================
# CLI
# ==========================================

def main():

    if not source_is_indexed(DEFAULT_SOURCE):
        print(
            f"{DEFAULT_SOURCE} is not indexed yet. Indexing knowledge files..."
        )
        index_documents()

    while True:

        question = input(
            "\nВопрос: "
        )

        if question.lower() in (
            "exit",
            "quit"
        ):
            break

        answer = ask_rag(
            question
        )

        print(
            "\nОтвет:\n"
        )

        print(answer)


if __name__ == "__main__":

    choice = input(
        "1 - index\n"
        "2 - chat\n"
        "Choice: "
    )

    if choice == "1":
        index_documents()

    elif choice == "2":
        main()
