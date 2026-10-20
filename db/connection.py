from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from core.config import settings

# Absolute path from settings: the DB must not depend on the working
# directory of whatever process happens to launch the server.
DATABASE_URL = f"sqlite:///{settings.db_path}"

# check_same_thread=False + a real connection pool: needed so multiple
# worker threads (e.g. parser.py's parallel CV processing) can each get
# their own pooled connection instead of sharing one across threads.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=10,
)


# Enable SQLite foreign key enforcement (off by default) and WAL mode
# (better concurrent read/write behavior with a web backend).
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_session() -> Iterator[Session]:
    """
    Context-manager session for CLI / background code paths (parser,
    vector_search, LLM helpers) where there is no HTTP request to hang
    a FastAPI dependency off. FastAPI endpoints use api.deps.get_db instead.

    Usage:
        with get_session() as session:
            session.add(obj)
    Commits on success, rolls back on exception, always closes.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()