"""
database.py — SQLAlchemy engine and session management.

Usage:
    from database import get_session

    with get_session() as session:
        companies = session.query(Company).all()
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session

from config import DATABASE_URL

# SQLite needs WAL mode for concurrent reads and check_same_thread=False
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False, "timeout": 30.0}

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
        cursor.execute("PRAGMA busy_timeout=30000")
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
    # Migrate: add email_source column if missing (SQLAlchemy create_all won't alter existing tables)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(contacts)"))
            columns = [row[1] for row in result.fetchall()]
            if "email_source" not in columns:
                conn.execute(text("ALTER TABLE contacts ADD COLUMN email_source TEXT"))
                conn.commit()
    except Exception:
        pass  # Non-SQLite or column already exists


if __name__ == "__main__":
    init_db()
    print("[OK] Database initialized successfully.")
