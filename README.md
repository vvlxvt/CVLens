# CVLens

CVLens extracts structured data from PDF resumes, pairs them with expert feedback from Telegram chat exports, and indexes the results in [Qdrant](https://qdrant.tech/) for semantic similarity search. Use it to find historically reviewed CVs that resemble a new candidate and surface relevant feedback.

## Features

- **PDF parsing** — Extract text from CV PDFs with PyMuPDF and normalize layout (headings, bullets, date ranges, whitespace).
- **Section detection** — Split resumes into `skills`, `experience`, and `about_me_summary` using heading aliases.
- **LLM extraction** — Pull `full_name`, `role_position`, and `summary` from the CV header via Ollama or Groq/OpenAI.
- **Feedback pairing** — Match admin replies in a Telegram export to the CV they comment on.
- **Vector search** — Embed cases with `intfloat/multilingual-e5-base` and search similar profiles in Qdrant.

## How it works

```
Telegram export (result.json) + PDF files
        │
        ▼
  extract/parser.py  ──►  cases.json
        │
        ▼
      app.py  ──►  Qdrant collection (cv_reviews)
        │
        ▼
  search_similar_cv()  ──►  top-N similar CVs + feedback
```

Each **case** is a flat JSON object:

| Field | Description |
|-------|-------------|
| `id` | Telegram message ID of the original CV |
| `role_position` | Job title / desired role |
| `skills` | Technical skills block |
| `about_me_summary` | Professional summary |
| `experience` | Work history text |
| `feedback` | Combined expert review(s) from admin replies |

## Requirements

- Python 3.10+
- A [Qdrant](https://qdrant.tech/) instance (cloud or self-hosted)
- One of:
  - [Ollama](https://ollama.com/) with a local model (default: `llama3.2:3b`), or
  - [Groq](https://groq.com/) API key (used by `response.py` when `LLM_PROVIDER=openai`)

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

# LLM provider: ollama (default) or openai (Groq-compatible API in response.py)
LLM_PROVIDER=ollama
OLLAMA_NUM_GPU=0

# Groq (used by response.py when LLM_PROVIDER=openai)
GROQ_API_KEY=your-groq-key

# Optional: OpenAI (used by main.py when LLM_PROVIDER=openai)
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-4o-mini
```

## Usage

### 1. Prepare data

1. Export a Telegram chat that contains CV PDFs and admin feedback replies.
2. Save the export as `extract/data/result.json`.
3. Place referenced PDF files under `extract/data/files/` (this directory is gitignored).

The parser expects admin names defined in `extract/parser.py` (`ADMINS`) and reply chains where an admin message replies to a PDF message.

### 2. Build cases

```bash
python -m extract.parser
```

Writes structured cases to `extract/data/cases.json`.

### 3. Index and search in Qdrant

```bash
python app.py
```

This script:

- Creates or recreates the `cv_reviews` collection
- Embeds all cases from `cases.json`
- Runs a sample similarity search against a hard-coded CV object

To search programmatically, reuse the helpers in `app.py`:

```python
from app import client, model, build_resume_text, search_similar_cv

results = search_similar_cv(
    client,
    collection_name="cv_reviews",
    embedding_model=model,
    resume_text=build_resume_text(my_cv_dict),
    limit=5,
)

for hit in results:
    print(hit.score, hit.payload["role"], hit.payload["feedback"])
```

### 4. Test PDF text cleaning

Sample PDFs live in `tests/`. Run the cleaner on one:

```bash
python tests/test_clean_cv_text.py
```

Edit `file_url` in that script to point at a different PDF.

## Project structure

```
CVLens/
├── app.py                 # Qdrant indexing + similarity search for CV cases
├── main.py                # Standalone RAG chat over knowledge/*.txt (separate workflow)
├── response.py            # LLM client for JSON field extraction from CV headers
├── extract/
│   ├── parser.py          # PDF parsing, section split, case builder
│   └── data/
│       ├── result.json    # Telegram chat export (input)
│       ├── cases.json     # Parsed CV + feedback cases (output)
│       └── files/         # PDF files referenced by the export
├── tests/                 # Sample CV PDFs and cleaning script
├── requirements.txt
└── README.md
```

## Embedding model

Cases are encoded with the E5 prefix convention:

- **Indexing:** `passage: {resume_text}`
- **Query:** `query: {resume_text}`

Default model in `app.py`: `intfloat/multilingual-e5-base` (768 dimensions, cosine distance).

## Notes

- Non-English CVs (English ratio below 90% in the first 300 characters) are skipped during parsing.
- `extract/parser.py` currently limits output to the first 5 cases (`cases[:5]`); adjust in code if you need the full dataset.
- `main.py` is a generic RAG CLI over text files in `knowledge/` and shares the `cv_reviews` collection name but serves a different purpose than CV similarity search.

## License

MIT — see [LICENSE](LICENSE).
