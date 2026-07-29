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
        ├─► app.py  ──►  Qdrant collection (cv_reviews)
        │                  │
        │                  └─► search_similar_cv()  ──►  top-N similar CVs + feedback
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

Create a `.env` file in the project root:

```env
# Qdrant
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your-api-key
QDRANT_VERIFY_SSL=false

# LLM provider: ollama (default), openai (Groq-compatible), or official_openai
LLM_PROVIDER=ollama
OLLAMA_NUM_GPU=0

# Groq (used by response.py when LLM_PROVIDER=openai)
GROQ_API_KEY=your-groq-key
GROQ_MODEL=llama-3.3-70b-versatile

# OpenAI official API (also appears in the selectable model list)
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-5.1

# Database (SQLite - default, no additional config needed)
# Database file: resumes.db (created automatically)
```

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

```bash
# Reindex all resumes from SQLite to Qdrant
python app.py --reindex

# Search for similar CVs using a PDF
python app.py --pdf path/to/cv.pdf --limit 5

# Search with skills filter
python app.py --pdf path/to/cv.pdf --skills "python,django" --limit 10
```

To search programmatically, reuse the helpers in `app.py`:

```python
from app import client, load_embedding_model, load_cv_from_pdf, build_resume_text, search_similar_cv

model = load_embedding_model()
case = load_cv_from_pdf("path/to/cv.pdf")
resume_text = build_resume_text(case)

results = search_similar_cv(
    client,
    collection_name="cv_reviews",
    embedding_model=model,
    case=case,
    limit=5,
    skills_filter=["python", "django"],
)

for hit in results:
    print(hit.score, hit.payload["role"], hit.payload["feedback_summary"])
```

### 6. Test PDF text cleaning

Sample PDFs live in `tests/`. Run the cleaner on one:

```bash
python tests/test_clean_cv_text.py
```

Edit `file_url` in that script to point at a different PDF.

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
├── tests/                 # Sample CV PDFs and cleaning script
├── alembic/               # Database migration files
├── alembic.ini            # Alembic configuration
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

- `POST /resumes/upload` — Upload Telegram export and PDF files
- `GET /resumes` — List resumes with pagination and filters
- `GET /resumes/{resume_id}` — Get full resume details
- `DELETE /resumes/{resume_id}` — Delete a resume

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

## License

MIT — see [LICENSE](LICENSE).
