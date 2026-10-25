# CVLens

CVLens extracts structured data from PDF resumes, pairs them with expert feedback from Telegram chat exports, and indexes the results in [Qdrant](https://qdrant.tech/) for semantic similarity search. Use it to find historically reviewed CVs that resemble a new candidate and surface relevant feedback.

## Features

- **PDF parsing** — Extract text from CV PDFs with PyMuPDF and normalize layout (headings, bullets, date ranges, whitespace).
- **Section detection** — Split resumes into `skills`, `experience`, and `about_me_summary` using heading aliases.
- **LLM extraction** — Pull `full_name`, `role_position`, and `summary` from the CV header via Ollama or Groq/OpenAI.
- **Feedback pairing** — Match admin replies in a Telegram export to the CV they comment on.
- **Vector search** — Embed cases with `intfloat/multilingual-e5-base` and search similar profiles in Qdrant with field-weighted embeddings.
- **SQLite database** — Persistent storage with SQLAlchemy for resumes, prompts, and version tracking.
- **REST API** — FastAPI backend for CRUD operations on resumes and prompt management.
- **Web UI** — Browser-based interface for uploading, viewing, and managing CVs.
- **Prompt versioning** — Track and manage different versions of extraction prompts with full history.

## How it works

```
Telegram export (result.json) + PDF files
        │
        ▼
  extract/parser.py  ──►  SQLite DB (resumes.db)
        │
        ├─► vector_search.py  ──►  Qdrant collection (cv_reviews)
        │                  │
        │                  └─► search_similar()  ──►  top-N similar CVs + feedback
        │
        ├─► api/main.py  ──►  FastAPI REST API
        │                     │
        │                     ├─► /resumes/upload  ──►  Parse & store CVs
        │                     ├─► /resumes         ──►  List & filter CVs
        │                     └─► /prompts         ──►  Manage extraction prompts
        │
        └─► web/  ──►  Browser UI for CV management
```

Each **resume** in the database contains:

| Field | Description |
|-------|-------------|
| `resume_id` | Telegram message ID of the original CV |
| `role_position` | Job title / desired role |
| `skills` | Technical skills block |
| `about_me_summary` | Professional summary |
| `experience` | Work history text |
| `feedback_raw` | Original recruiter feedback text |
| `feedback_summary` | LLM-generated 1-2 line summary |
| `feedback_sections` | Categorized feedback sections (e.g., ["skills", "experience"]) |
| `about_llm` | LLM model used for intro extraction |
| `feedback_llm` | LLM model used for feedback extraction |

## Requirements

- Python 3.10+
- A [Qdrant](https://qdrant.tech/) instance (cloud or self-hosted)
- One of:
  - [Ollama](https://ollama.com/) with a local model (default: `llama3.2:3b`), or
  - [Groq](https://groq.com/) API key (used by `response.py` when `LLM_PROVIDER=openai`), or
  - OpenAI API key (used by `response.py` when `LLM_PROVIDER=official_openai`)

Embedding models are downloaded automatically on first run via `sentence-transformers`.

## Installation

```bash
git clone <repository-url>
cd CVLens

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

## Configuration

1. Copy `.env.example` to `.env` and fill in real values:

   ```bash
   copy .env.example .env    # Windows
   # cp .env.example .env    # macOS / Linux
   ```

2. `.env` is gitignored — never commit real keys.

3. Required variables:
   - `API_TOKEN` — Bearer token required for all data routes (empty = auth disabled for local dev).

4. Optional provider keys — only set the ones you need:
   - `QDRANT_API_KEY`, `QDRANT_URL` — for vector search (Qdrant cloud or self-hosted)
   - `GROQ_API_KEY` — for Groq LLM provider
   - `OPEN_ROUTER_KEY` — for OpenRouter LLM provider

See `.env.example` for the full list of variables with placeholder values and comments.

## Usage

### 1. Initialize database

```bash
# Run database migrations
alembic upgrade head

### 2. Prepare data

1. Export a Telegram chat that contains CV PDFs and admin feedback replies.
2. Save the export as `extract/data/result.json`.
3. Place referenced PDF files under `extract/data/files/` (this directory is gitignored).

The parser expects admin names defined in `extract/parser.py` (`ADMINS`) and reply chains where an admin message replies to a PDF message.

### 3. Run the API server

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000` with interactive docs at `http://localhost:8000/docs`.

### 4. Use the Web UI

Open `web/index.html` in a browser to access the web interface for:
- Uploading Telegram exports and PDF files
- Viewing and filtering resumes
- Managing extraction prompts

### 5. Index and search in Qdrant

Reindex all resumes from SQLite to Qdrant via the API:

```bash
curl -X POST http://localhost:8000/resumes/reindex \
  -H "Authorization: Bearer $API_TOKEN"
```

Search for similar CVs via the API (upload a PDF):

```bash
curl -X POST http://localhost:8000/resumes/search \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "file=@path/to/cv.pdf" \
  -F "limit=5"
```

Search with skills filter:

```bash
curl -X POST http://localhost:8000/resumes/search \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "file=@path/to/cv.pdf" \
  -F "skills=python,django" \
  -F "limit=10"
```

Or use the Python helpers directly from `vector_search.py`:

```python
from vector_search import get_embedding_model, get_qdrant_client, search_similar, encode_weighted_resume
from extract.parser import parse_cv_sections
from pathlib import Path

model = get_embedding_model()
client = get_qdrant_client()

sections = parse_cv_sections("path/to/cv.pdf", data_dir=Path("."))
case = {
    "role_position": sections.get("role_position") or "",
    "skills": sections.get("skills") or "",
    "about_me_summary": sections.get("about_summary") or sections.get("about_me_summary_raw") or "",
    "experience": sections.get("experience") or "",
}

vector = encode_weighted_resume(case, model, prefix="query")
results = client.query_points(
    collection_name="cv_reviews",
    query=vector,
    limit=5,
).points

for hit in results:
    print(hit.score, hit.payload.get("role"), hit.payload.get("summary"))
```

### 6. Test PDF text cleaning

Sample PDFs live in `tests/`. Run the cleaner on one:

```bash
python tests/test_clean_cv_text.py
```

Edit `file_url` in that script to point at a different PDF.

### 7. Run tests

The project includes a pytest test suite covering critical parsing, JSON recovery,
Telegram feedback grouping, and database repository operations:

```bash
pytest tests/
```

All 58 tests must pass. New features should include tests.

### 7. RAG chat (separate workflow)

The `main.py` script provides a standalone RAG chat interface over text files in the `knowledge/` directory:

```bash
python main.py
# Choose: 1 - index, 2 - chat
```

## Project structure

```
CVLens/
├── app.py                 # Qdrant indexing + similarity search for CV cases
├── main.py                # Standalone RAG chat over knowledge/*.txt (separate workflow)
├── response.py            # LLM client for JSON field extraction from CV headers
├── api/
│   ├── main.py            # FastAPI REST API for resumes and prompts
│   └── schemas.py         # Pydantic schemas for API models
├── db/
│   ├── connection.py      # Database connection setup
│   ├── models.py          # SQLAlchemy models (Resume, Prompt)
│   ├── resumes_repo.py    # Resume repository CRUD operations
│   └── prompts_repo.py    # Prompt repository CRUD operations
├── extract/
│   ├── parser.py          # PDF parsing, section split, case builder
│   └── data/
│       ├── result.json    # Telegram chat export (input)
│       ├── cases.json     # Parsed CV + feedback cases (output)
│       └── files/         # PDF files referenced by the export
├── web/
│   ├── index.html         # Main web UI
│   ├── upload.html        # Upload interface
│   ├── prompts.html       # Prompt management interface
│   ├── css/               # Stylesheets
│   └── js/                # JavaScript modules
├── tests/                 # Unit tests (pytest) + sample CV PDFs
├── alembic/               # Database migration files
├── alembic.ini            # Alembic configuration
├── core/
│   ├── config.py          # Application settings (pydantic-settings)
│   └── rate_limit.py      # Token-bucket rate limiter for API endpoints
├── .env.example           # Configuration template (copy to .env)
├── requirements.txt
└── README.md
```

## Embedding model

Cases are encoded with the E5 prefix convention:

- **Indexing:** `passage: {field_name}:\n{field_text}`
- **Query:** `query: {field_name}:\n{field_text}`

Default model in `app.py`: `intfloat/multilingual-e5-base` (768 dimensions, cosine distance).

Field-weighted embeddings are used to prioritize important fields:
- `role_position`: weight 3.0
- `skills`: weight 3.0
- `about_me_summary`: weight 1.0
- `experience`: weight 1.0

## API Endpoints

### Resumes

- `POST /resumes/upload` — Upload Telegram export and PDF files (rate-limited: 10/min)
- `POST /resumes/search` — Search similar CVs via vector similarity (rate-limited: 10/min)
- `POST /resumes/reindex` — Reindex all resumes from SQLite to Qdrant (rate-limited: 10/min)
- `GET /resumes` — List resumes with pagination and filters (rate-limited: 60/min)
- `GET /resumes/{resume_id}` — Get full resume details (rate-limited: 60/min)
- `POST /resumes/{resume_id}/feedback/recompute` — Recompute feedback with a model (rate-limited: 10/min)
- `DELETE /resumes/{resume_id}` — Delete a resume (rate-limited: 60/min)
- `GET /resumes/llms` — List distinct LLM models used (rate-limited: 60/min)
- `GET /resumes/llm-options` — Get LLM configuration options (rate-limited: 60/min)

### Prompts

- `GET /prompts/{name}` — List all versions of a prompt
- `GET /prompts/{name}/latest` — Get the latest version of a prompt
- `POST /prompts/{name}` — Create a new version of a prompt

Prompt names: `intro_extraction`, `feedback_extraction`

## Notes

- Non-English CVs (English ratio below 90% in the first 300 characters) are skipped during parsing.
- CVs with no clear feedback (feedback_sections is null) are parsed but not saved to the database.
- The database uses SQLite with Alembic for migrations. Run `alembic upgrade head` after installation.
- `main.py` is a generic RAG CLI over text files in `knowledge/` and uses a separate embedding model (`intfloat/multilingual-e5-small`) and collection (`cv_reviews`) for a different purpose than CV similarity search.
- The web UI is a static HTML/JS interface that communicates with the FastAPI backend.

### Reliability & security notes

- **Rate limiting:** LLM-heavy endpoints (`/resumes/upload`, `/resumes/search`, `/resumes/reindex`,
  `/resumes/recompute`, `/resumes/review`, `/review-rules/rebuild`) are rate-limited to
  10 requests/minute per client IP or API token. Read endpoints allow 60 requests/minute.
  HTTP 429 with `Retry-After` header is returned when the limit is exceeded.
- **Graceful shutdown:** The FastAPI app uses a `lifespan` handler that disposes the
  SQLAlchemy engine and closes the Qdrant client on shutdown.
- **Lazy initialization:** External clients (Qdrant, Groq, OpenRouter) are created on
  first use, not at import time. The modules can be imported without network access.
- **PDF validation:** Uploaded files with `.pdf` extension are validated for the `%PDF-`
  magic bytes; non-PDF files are skipped during upload.
- **Logging:** The application uses Python's `logging` module instead of `print()`.
  Configure log levels as needed for your deployment.
- **Secrets:** `.env` and `.env_keys` are gitignored. Use `.env.example` as a template.

## License

MIT — see [LICENSE](LICENSE).
