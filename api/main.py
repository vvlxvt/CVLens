"""CVLens FastAPI application: web UI + REST API over the parsed-CV DB."""

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.deps import require_token
from api.routers import pages, prompts, resumes, review_rules, reviews
from core.config import settings

app = FastAPI(
    title="CVLens API",
    version="0.1.0",
    description="CRUD access to the parsed-CV / recruiter-feedback DB, for the future web UI.",
)

# Optional cross-origin access for a separately-hosted frontend; an empty
# cors_origins list (default) keeps the API same-origin only.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Static assets for the web UI.
BASE_DIR = Path(__file__).resolve().parent.parent
app.mount(
    "/static/css",
    StaticFiles(directory=str(BASE_DIR / "web" / "css")),
    name="static_css",
)
app.mount(
    "/static/js", StaticFiles(directory=str(BASE_DIR / "web" / "js")), name="static_js"
)

# The token is embedded into served pages so the same-origin UI's fetch
# wrapper (web/js/auth.js) can send Authorization headers; the pages
# themselves are static shells and expose nothing else.
pages.templates.env.globals["api_token"] = settings.api_token


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(pages.router)
app.include_router(resumes.router, dependencies=[Depends(require_token)])
app.include_router(reviews.router, dependencies=[Depends(require_token)])
app.include_router(review_rules.router, dependencies=[Depends(require_token)])
app.include_router(prompts.router, dependencies=[Depends(require_token)])