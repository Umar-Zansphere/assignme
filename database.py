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

    # ── Migrations (SQLAlchemy create_all won't alter existing tables) ──
    try:
        with engine.connect() as conn:
            # Contacts: add email_source column
            result = conn.execute(text("PRAGMA table_info(contacts)"))
            columns = [row[1] for row in result.fetchall()]
            if "email_source" not in columns:
                conn.execute(text("ALTER TABLE contacts ADD COLUMN email_source TEXT"))

            # Emails: add sending infrastructure columns
            result = conn.execute(text("PRAGMA table_info(emails)"))
            email_cols = [row[1] for row in result.fetchall()]
            if "mailbox_id" not in email_cols:
                conn.execute(text("ALTER TABLE emails ADD COLUMN mailbox_id INTEGER REFERENCES mailboxes(id)"))
            if "unsubscribe_token" not in email_cols:
                conn.execute(text("ALTER TABLE emails ADD COLUMN unsubscribe_token TEXT"))
            if "blocked_reason" not in email_cols:
                conn.execute(text("ALTER TABLE emails ADD COLUMN blocked_reason TEXT"))

            # Mailboxes: add OAuth columns
            result = conn.execute(text("PRAGMA table_info(mailboxes)"))
            mb_cols = [row[1] for row in result.fetchall()]
            if mb_cols:  # table exists
                if "oauth_access_token" not in mb_cols:
                    conn.execute(text("ALTER TABLE mailboxes ADD COLUMN oauth_access_token TEXT"))
                if "oauth_refresh_token" not in mb_cols:
                    conn.execute(text("ALTER TABLE mailboxes ADD COLUMN oauth_refresh_token TEXT"))
                if "oauth_token_expiry" not in mb_cols:
                    conn.execute(text("ALTER TABLE mailboxes ADD COLUMN oauth_token_expiry TEXT"))
                if "oauth_connected" not in mb_cols:
                    conn.execute(text("ALTER TABLE mailboxes ADD COLUMN oauth_connected INTEGER DEFAULT 0"))

            conn.commit()
    except Exception:
        pass  # Non-SQLite or columns already exist


if __name__ == "__main__":
    init_db()
    print("[OK] Database initialized successfully.")
