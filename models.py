"""
models.py — SQLAlchemy ORM models for all pipeline tables.

Status flow:
    NEW_SIGNAL → ENRICHED → QUALIFIED/REJECTED → CONTACT_FOUND
    → EMAIL_VERIFIED → RESEARCH_DONE → EMAIL_READY → EMAIL_SENT → REPLIED
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ── Companies ──────────────────────────────────────────────
class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    website = Column(String)
    industry = Column(String)
    country = Column(String)
    employee_count = Column(Integer)
    linkedin_url = Column(String)
    github_url = Column(String)
    icp_score = Column(Integer, default=0)
    status = Column(String, default="NEW_SIGNAL", index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    signals = relationship("Signal", back_populates="company", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="company", cascade="all, delete-orphan")
    research = relationship("Research", back_populates="company", uselist=False, cascade="all, delete-orphan")
    emails = relationship("Email", back_populates="company", cascade="all, delete-orphan")
    reply_logs = relationship("ReplyLog", back_populates="company", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Company(id={self.id}, name='{self.name}', status='{self.status}')>"


# ── Signals ────────────────────────────────────────────────
class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    signal_type = Column(String, nullable=False)   # JOB_POSTING, PRODUCT_LAUNCH, NEWS, FUNDING
    source = Column(String)                         # greenhouse, producthunt, techcrunch, etc.
    title = Column(String)
    description = Column(Text)
    raw_url = Column(String)
    detected_at = Column(DateTime, default=_utcnow)

    # Relationship
    company = relationship("Company", back_populates="signals")

    __table_args__ = (
        UniqueConstraint("company_id", "signal_type", "raw_url", name="uq_signal"),
    )

    def __repr__(self):
        return f"<Signal(id={self.id}, type='{self.signal_type}', source='{self.source}')>"


# ── Contacts ──────────────────────────────────────────────
class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String)
    role = Column(String)                           # CTO, Engineering Manager, etc.
    email = Column(String)
    linkedin_url = Column(String)
    verified = Column(String)                       # VALID, INVALID, None=pending
    created_at = Column(DateTime, default=_utcnow)

    # Relationships
    company = relationship("Company", back_populates="contacts")
    emails = relationship("Email", back_populates="contact", cascade="all, delete-orphan")
    reply_logs = relationship("ReplyLog", back_populates="contact", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Contact(id={self.id}, name='{self.name}', role='{self.role}')>"


# ── Research ──────────────────────────────────────────────
class Research(Base):
    __tablename__ = "research"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, unique=True)
    summary = Column(Text)
    pain_points = Column(Text)                      # JSON array
    tech_stack = Column(Text)                       # JSON array
    recent_news = Column(Text)
    raw_json = Column(Text)                         # Full LLM response
    created_at = Column(DateTime, default=_utcnow)

    # Relationship
    company = relationship("Company", back_populates="research")

    def __repr__(self):
        return f"<Research(id={self.id}, company_id={self.company_id})>"


# ── Campaigns ─────────────────────────────────────────────
class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    created_at = Column(DateTime, default=_utcnow)
    is_active = Column(Integer, default=1)

    # Relationships
    emails = relationship("Email", back_populates="campaign")

    def __repr__(self):
        return f"<Campaign(id={self.id}, name='{self.name}')>"


# ── Emails ────────────────────────────────────────────────
class Email(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    sequence_number = Column(Integer, default=0)    # 0=initial, 1=followup1, 2=followup2
    subject = Column(String)
    body = Column(Text)
    status = Column(String, default="DRAFT", index=True)  # DRAFT, SCHEDULED, SENT, FAILED, CANCELLED
    scheduled_at = Column(DateTime)
    sent_at = Column(DateTime)
    message_id = Column(String)                     # SMTP Message-ID for reply tracking

    # Relationships
    company = relationship("Company", back_populates="emails")
    contact = relationship("Contact", back_populates="emails")
    campaign = relationship("Campaign", back_populates="emails")
    reply_logs = relationship("ReplyLog", back_populates="email", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Email(id={self.id}, seq={self.sequence_number}, status='{self.status}')>"


# ── Reply Logs ────────────────────────────────────────────
class ReplyLog(Base):
    __tablename__ = "reply_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email_id = Column(Integer, ForeignKey("emails.id"))
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"))
    reply_subject = Column(String)
    reply_body = Column(Text)
    reply_from = Column(String)
    detected_at = Column(DateTime, default=_utcnow)

    # Relationships
    email = relationship("Email", back_populates="reply_logs")
    company = relationship("Company", back_populates="reply_logs")
    contact = relationship("Contact", back_populates="reply_logs")

    def __repr__(self):
        return f"<ReplyLog(id={self.id}, company_id={self.company_id})>"


# ── Settings ──────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return f"<Setting(key='{self.key}', value='{self.value}')>"
