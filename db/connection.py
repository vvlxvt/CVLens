from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

# from db.models import Base

DB_PATH = "resumes.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# check_same_thread=False + a real connection pool: needed so multiple
# worker threads (e.g. parser.py's parallel CV processing) can each get
# their own pooled connection instead of sharing one across threads.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=10,
)


# Enable SQLite foreign key enforcement (off by default) and WAL mode
# (better concurrent read/write behavior once a web backend is added).
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


# def init_db():
#     """Create all tables if they don't exist. For schema changes later, use Alembic instead."""
#     Base.metadata.create_all(bind=engine)


@contextmanager
def get_session():
    """
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