# CVLens — Agent Instructions

This file provides guidance for AI agents working on the CVLens codebase.
Read this before making changes.

## Project Overview

CVLens extracts structured data from PDF resumes, pairs them with recruiter
feedback from Telegram chat exports, and indexes results in Qdrant for
semantic similarity search. Built with Python 3.10+, FastAPI, SQLAlchemy,
Alembic, and Qdrant.

## Key Files & Modules

| Path | Purpose |
|------|---------|
| `api/main.py` | FastAPI app factory, lifespan handler, middleware, router registration |
| `api/deps.py` | DB session dependency (`get_db`), Bearer token auth (`require_token`) |
| `api/routers/resumes.py` | Resume CRUD, upload, search, reindex, recompute, list |
| `api/routers/reviews.py` | CV review endpoint (few-shot RAG), review feedback |
| `api/routers/review_rules.py` | Auto-learned review rule sets (build + browse) |
| `api/routers/prompts.py` | Prompt version management |
| `api/routers/pages.py` | HTML page routes for the web UI |
| `api/schemas.py` | Pydantic request/response schemas |
| `api/reviewing.py` | Shared helpers: prompt building, LLM review generation, JSON parsing |
| `core/config.py` | Pydantic-settings based configuration (`.env` + env vars) |
| `core/rate_limit.py` | Token-bucket rate limiter (LLM + read endpoints) |
| `db/connection.py` | SQLAlchemy engine, session factory, SQLite PRAGMAs |
| `db/models.py` | SQLAlchemy models |
| `db/resumes_repo.py` | Resume CRUD + pagination + section filtering |
| `db/prompts_repo.py` | Prompt CRUD + versioning |
| `db/reviews_repo.py` | CVReview CRUD + feedback |
| `db/review_rules_repo.py` | Rule set CRUD + diffs |
| `response.py` | LLM client: Groq → OpenRouter → Ollama fallback, JSON recovery |
| `extract/parser.py` | PDF parsing, section detection, Telegram message grouping |
| `vector_search.py` | Qdrant client (lazy), embedding model (lazy), reindex, search |
| `scripts/rag_cli.py` | Standalone RAG CLI (separate workflow) |
| `web/` | Static HTML/JS frontend |## Coding Conventions

### Language
- **Comments and docstrings:** English only
- **Variable/function names:** English, snake_case
- **User-facing strings:** Can be Russian or English (web UI is mixed)
- **Commit messages:** Conventional Commits (fix:, feat:, test:, refactor:)

### Error Handling
- **No bare `except Exception`** — catch specific exceptions (PDF errors, network errors, ValueError, etc.)
- **Use `logging` module** instead of `print()` — module-level `logger = logging.getLogger(__name__)`
- **Structured error returns:** Return dicts with `_error_type`/`_error_message` instead of opaque `None`
- **FastAPI endpoints:** Use `HTTPException` with appropriate status codes (400, 404, 413, 429, 502)

### Database
- **SQLAlchemy 2.0 style** — `select()`, `session.scalars()`, `Mapped[mapped_column]`
- **Session management:** Use `get_db()` dependency in FastAPI routes, `get_session()` context manager in CLI/scripts
- **JSON filtering:** Use SQLite JSON1 `json_each()` + `EXISTS` for `feedback_sections` filtering — never load all rows into Python
- **Migrations:** Alembic — run `alembic upgrade head` after model changes

### External Clients (Lazy Initialization)
- **QdrantClient:** Use `get_qdrant_client()` from `vector_search.py` — never instantiate at module level
- **Groq/OpenAI clients:** Use `get_groq_client()` / `get_openrouter_client()` from `response.py`
- **Graceful shutdown:** FastAPI `lifespan` handler disposes engine and closes Qdrant client

### Rate Limiting
- **LLM-heavy endpoints:** 10 requests/minute (`llm_rate_limiter.check`)
- **Read endpoints:** 60 requests/minute (`read_rate_limiter.check`)
- Add `dependencies=[Depends(llm_rate_limiter.check)]` or `read_rate_limiter.check` to new route decorators

### Testing
- **Framework:** pytest
- **Fixtures:** `tests/conftest.py` provides `db_session` (SQLite in-memory) and `client` (TestClient)
- **Test location:** `tests/test_*.py`
- **Run:** `pytest tests/`
- **All tests must pass** before committing

### File Encoding
- All Python files use UTF-8
- Line endings: CRLF on Windows (git handles conversion)

## Common Tasks

### Add a new API endpoint
1. Define Pydantic schemas in `api/schemas.py`
2. Add route in the appropriate `api/routers/*.py` file
3. Add `dependencies=[Depends(require_token)]` for data routes
4. Add `dependencies=[Depends(llm_rate_limiter.check)]` for LLM-heavy endpoints
5. Add tests in `tests/`
6. Update `README.md` API Endpoints section

### Add a new database model
1. Define model in `db/models.py` (inherit from `Base`)
2. Create Alembic migration: `alembic revision --autogenerate -m "description"`
3. Run `alembic upgrade head`
4. Add repository functions in `db/*_repo.py`
5. Add tests

### Change LLM provider logic
1. Edit `response.py` — `generate_response()`, `_call_groq()`, `_call_openrouter()`, `_call_ollama()`
2. Clients are lazy — use `get_groq_client()` / `get_openrouter_client()`
3. Fallback chain: Groq → OpenRouter → Ollama
4. Add tests in `tests/test_json_parsing.py` for JSON recovery logic

### Modify CV parsing
1. Edit `extract/parser.py` — section detection, message grouping, case building
2. Admin names are in `ADMINS` set
3. Non-English CVs (< 90% English) are skipped
4. Add tests in `tests/test_clean_cv_text.py` and `tests/test_message_grouping.py`

## Files to Never Commit

- `.env` — contains real secrets (gitignored)
- `.env_keys` — duplicate secrets (gitignored)
- `resumes.db` — SQLite database (gitignored via `resumes*`)
- `*.pdf` in `extract/data/` — user CVs (gitignored)
- `.venv/`, `__pycache__/`, `.ruff_cache/`, `.pytest_cache/`

## Useful Commands

```bash
# Run tests
pytest tests/

# Run API server
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run migrations
alembic upgrade head
```