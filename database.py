"""
database.py — SQLAlchemy engine and session management.

Usage:
    from database import get_session

    with get_session() as session:
        companies = session.query(Company).all()
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from config import DATABASE_URL

# SQLite needs WAL mode for concurrent reads and check_same_thread=False
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
)

# Enable WAL mode for SQLite (better concurrent access)
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Session:
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """Create all tables. Safe to call multiple times."""
    from models import Base  # noqa: F811
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("[OK] Database initialized successfully.")
