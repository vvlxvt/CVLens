"""Test configuration and shared fixtures for CVLens test suite."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import settings
from db.models import Base
from api.main import app
from api.deps import get_db


@pytest.fixture(scope="session")
def temp_db_path():
    """Path to a temporary SQLite database file for the test session."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


@pytest.fixture(scope="session")
def test_engine(temp_db_path):
    """SQLAlchemy engine bound to a temporary SQLite database."""
    engine = create_engine(
        f"sqlite:///{temp_db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(test_engine):
    """SQLAlchemy session with automatic rollback after each test.

    Each test gets a clean session; changes are rolled back so tests
    don't interfere with each other.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with the DB dependency overridden to use the test session."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()