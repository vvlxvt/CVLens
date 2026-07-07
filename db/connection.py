from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker



DB_PATH = "resumes.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, future=True)


# Enable SQLite foreign key enforcement (off by default) and WAL mode
# (better concurrent read/write behavior once a web backend is added).
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# from db.models import Base
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