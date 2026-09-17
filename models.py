"""
models.py — SQLAlchemy ORM models for all pipeline tables.

Status flow:
    NEW_SIGNAL → ENRICHED → QUALIFIED/REJECTED → CONTACT_FOUND
    → EMAIL_VERIFIED → RESEARCH_DONE → EMAIL_READY → EMAIL_SENT → REPLIED

Sending infrastructure models:
    Mailbox          — First-class sending resource with health metrics
    DomainHealth     — Domain-level DNS authentication and reputation
    SuppressionList  — Global/campaign/account do-not-contact register
    SendingStats     — Per-mailbox hourly send counters
    MessageEvent     — Event pipeline for bounces/complaints/delivery
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, ForeignKey, UniqueConstraint,
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
    name = Column(String, nullable=False)
    website = Column(String)
    industry = Column(String)
    country = Column(String)
    employee_count = Column(Integer)
    linkedin_url = Column(String)
    github_url = Column(String)
    icp_score = Column(Integer, default=0)
    status = Column(String, default="NEW_SIGNAL", index=True)

    # Campaign linkage — which campaign sourced this company
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), index=True)

    # Multi-source tracking
    signal_source = Column(String)             # Primary source: "google_maps", "apollo", "linkedin", "searxng", "crunchbase"
    signal_sources_json = Column(Text)         # JSON list of ALL sources that found this company (for multi-signal bonus)
    contact_channel = Column(String, default="DISCOVERY_NEEDED")
    # EMAIL = source already gave us email
    # PHONE_ONLY = only phone available (flagged for manual outreach)
    # DISCOVERY_NEEDED = no contact info yet

    # Extra source fields
    phone = Column(String)                     # Phone from Google Maps / IndiaMART etc.
    address = Column(String)                   # Physical address from Google Maps
    rating = Column(Float)                     # Google Maps / Yelp rating
    category = Column(String)                  # Google Maps category

    # Prospeo enrichment (populated by finder.py via /enrich-person)
    description_ai = Column(Text)              # AI-generated company description from Prospeo
    tech_stack_json = Column(Text)             # JSON list of tech stack names e.g. ["React", "AWS"]
    active_job_titles = Column(Text)           # JSON list of active job posting titles
    keywords = Column(Text)                    # JSON list of company keyword tags
    funding_stage = Column(String)             # Seed, Series A, etc.
    revenue_range = Column(String)             # e.g. "$1M-$10M"
    is_b2b = Column(Integer, default=0)        # 1 if Prospeo flags as B2B company
    prospeo_enriched = Column(Integer, default=0)  # 1 = Prospeo data already stored

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    campaign = relationship("Campaign", back_populates="companies")
    signals = relationship("Signal", back_populates="company", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="company", cascade="all, delete-orphan")
    research = relationship("Research", back_populates="company", uselist=False, cascade="all, delete-orphan")
    emails = relationship("Email", back_populates="company", cascade="all, delete-orphan")
    reply_logs = relationship("ReplyLog", back_populates="company", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("name", "campaign_id", name="uq_company_campaign"),
    )

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
    email_source = Column(String)                   # PROSPEO_VERIFIED, NOT_FOUND, etc.
    verified = Column(String)                       # VALID, INVALID, PROSPEO_VERIFIED, None=pending

    # Prospeo enrichment (populated by finder.py via /enrich-person)
    headline = Column(String)                       # LinkedIn headline e.g. "CTO at Acme Corp"
    timezone = Column(String)                       # e.g. "America/New_York" — used to time sends
    city = Column(String)                           # contact's city
    prospeo_id = Column(String)                     # Prospeo's internal person ID for dedup

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
    """Dynamic campaign configuration — drives every pipeline stage.

    Created by Stage 0 (campaign_builder.py): user inputs natural language
    brief → LLM decomposes into structured config → user reviews & edits.
    """
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    status = Column(String, default="ACTIVE")  # ACTIVE, PAUSED, COMPLETED, ARCHIVED
    is_active = Column(Integer, default=1)      # Legacy compat — derived from status

    # Stage 0 input
    brief = Column(Text)                       # Original natural language input from user

    # Target profile (LLM-generated, user-reviewed)
    target_industries = Column(Text)           # JSON list: ["Electric Vehicles", "E-Mobility"]
    target_geography = Column(Text)            # JSON list: ["India", "USA"]
    target_company_size = Column(String)        # e.g. "1-200"
    target_roles = Column(Text)                # JSON list: ["Founder", "CTO", "Owner"]

    # Pipeline caps
    target_verified_emails = Column(Integer)    # Stop finding contacts once N verified emails reached (null=unlimited)

    # Data source config (LLM-generated)
    search_queries = Column(Text)              # JSON dict: {"google_maps": ["..."], "apollo": ["..."], ...}
    source_selection = Column(Text)            # JSON list: ["google_maps", "apollo", "searxng"]

    # Scoring rules (LLM-generated, user-editable)
    scoring_rules = Column(Text)               # JSON list: [{"rule": "...", "weight": 30, "type": "keyword"}, ...]
    scoring_threshold = Column(Integer, default=60)
    exclusion_list = Column(Text)              # JSON list: ["Staffing", "Dealership"]

    # Email framing (LLM-generated, user-editable)
    our_offering = Column(Text)                # What we sell
    value_proposition = Column(Text)           # Why they should care
    pitch_angle = Column(Text)                 # How to frame the outreach
    research_questions = Column(Text)          # JSON list: ["What dashboard do they use?", ...]

    # Soft delete
    deleted_at = Column(DateTime)              # Null = active, timestamp = soft-deleted

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    companies = relationship("Company", back_populates="campaign")
    emails = relationship("Email", back_populates="campaign")

    def __repr__(self):
        return f"<Campaign(id={self.id}, name='{self.name}', status='{self.status}')>"


# ── Emails ────────────────────────────────────────────────
class Email(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    mailbox_id = Column(Integer, ForeignKey("mailboxes.id"))        # which mailbox sent this
    sequence_number = Column(Integer, default=0)    # 0=initial, 1=followup1, 2=followup2
    subject = Column(String)
    body = Column(Text)
    status = Column(String, default="DRAFT", index=True)  # DRAFT, SCHEDULED, SENT, FAILED, CANCELLED, BLOCKED
    scheduled_at = Column(DateTime)
    sent_at = Column(DateTime)
    message_id = Column(String)                     # SMTP Message-ID for reply tracking
    unsubscribe_token = Column(String, unique=True, index=True)  # unique per email for RFC 8058
    blocked_reason = Column(String)                 # reason if status=BLOCKED (suppressed, capped, etc.)

    # Relationships
    company = relationship("Company", back_populates="emails")
    contact = relationship("Contact", back_populates="emails")
    campaign = relationship("Campaign", back_populates="emails")
    mailbox = relationship("Mailbox", back_populates="emails")
    reply_logs = relationship("ReplyLog", back_populates="email", cascade="all, delete-orphan")
    events = relationship("MessageEvent", back_populates="email", cascade="all, delete-orphan")

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


# ── Email Patterns (Domain Pattern Cache) ─────────────────
class EmailPattern(Base):
    __tablename__ = "email_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String, unique=True, nullable=False, index=True)
    pattern = Column(String, nullable=False)        # "first.last", "flast", "first", etc.
    confidence = Column(Integer, default=1)          # how many confirmed emails matched this
    catch_all = Column(String, default="UNKNOWN")   # YES, NO, UNKNOWN
    sample_email = Column(String)                    # e.g. "john.doe@acme.com" — for reference
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return f"<EmailPattern(domain='{self.domain}', pattern='{self.pattern}', confidence={self.confidence})>"


# ── Mailboxes (Sending Pool) ──────────────────────────────
class Mailbox(Base):
    """First-class sending resource. Each mailbox has its own SMTP/IMAP
    credentials, warmup state, health metrics, and daily/hourly caps.
    Mailboxes belong to a sending domain and are rotated by the
    sending orchestrator."""
    __tablename__ = "mailboxes"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    email           = Column(String, unique=True, nullable=False)   # john@getacme.com
    display_name    = Column(String)                                 # "John from Acme"
    domain          = Column(String, nullable=False, index=True)     # getacme.com

    # Provider connection
    provider        = Column(String, default="smtp")                 # smtp, google
    smtp_host       = Column(String)
    smtp_port       = Column(Integer, default=587)
    smtp_username   = Column(String)
    smtp_password   = Column(String)
    smtp_use_tls    = Column(Integer, default=1)
    imap_host       = Column(String)
    imap_port       = Column(Integer, default=993)
    imap_username   = Column(String)
    imap_password   = Column(String)

    # Google OAuth tokens (used when provider=google)
    oauth_access_token  = Column(Text)                               # short-lived access token
    oauth_refresh_token = Column(Text)                               # long-lived refresh token
    oauth_token_expiry  = Column(DateTime)                           # when access token expires
    oauth_connected     = Column(Integer, default=0)                 # 1=OAuth granted

    # Status & warmup (state machine)
    status          = Column(String, default="ACTIVE")               # ACTIVE, PAUSED, DISABLED
    warmup_status   = Column(String, default="WARMUP_PENDING")       # WARMUP_PENDING → WARMUP_ACTIVE → ESTABLISHED → HEALTHY → DEGRADED → PAUSED → RECOVERY
    warmup_started_at = Column(DateTime)

    # Health metrics (recalculated by event_processor)
    reputation_score  = Column(Integer, default=50)                  # 0-100 operational risk score (ours, not Google's)
    bounce_rate       = Column(Float, default=0.0)
    spam_rate         = Column(Float, default=0.0)
    reply_rate        = Column(Float, default=0.0)

    # Counters (rolling, updated by event_processor)
    total_sent        = Column(Integer, default=0)
    total_delivered   = Column(Integer, default=0)
    total_bounced     = Column(Integer, default=0)
    total_hard_bounced = Column(Integer, default=0)
    total_soft_bounced = Column(Integer, default=0)
    total_complaints  = Column(Integer, default=0)
    total_replies     = Column(Integer, default=0)

    # Timestamps
    days_active     = Column(Integer, default=0)
    last_sent_at    = Column(DateTime)
    created_at      = Column(DateTime, default=_utcnow)
    updated_at      = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    is_active       = Column(Integer, default=1)

    # Relationships
    emails = relationship("Email", back_populates="mailbox")
    events = relationship("MessageEvent", back_populates="mailbox")
    stats  = relationship("SendingStats", back_populates="mailbox", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Mailbox(id={self.id}, email='{self.email}', warmup='{self.warmup_status}')>"


# ── Domain Health ─────────────────────────────────────────
class DomainHealth(Base):
    """Domain-level authentication status and aggregate reputation.
    Each sending domain must pass SPF/DKIM/DMARC verification before
    campaigns can start."""
    __tablename__ = "domain_health"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    domain          = Column(String, unique=True, nullable=False, index=True)  # getacme.com
    is_sending_domain = Column(Integer, default=1)                              # 1=sending, 0=blocked primary

    # DNS authentication status
    spf_valid       = Column(Integer, default=0)
    dkim_valid      = Column(Integer, default=0)
    dmarc_valid     = Column(Integer, default=0)
    dns_last_checked = Column(DateTime)

    # Aggregate health (rolled up from mailboxes on this domain)
    total_sent      = Column(Integer, default=0)
    bounce_rate     = Column(Float, default=0.0)
    spam_rate       = Column(Float, default=0.0)
    reputation_score = Column(Integer, default=50)

    # Status
    status          = Column(String, default="PENDING_VERIFICATION")
    # PENDING_VERIFICATION, VERIFIED, DEGRADED, BLOCKED
    created_at      = Column(DateTime, default=_utcnow)
    updated_at      = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return f"<DomainHealth(domain='{self.domain}', status='{self.status}')>"


# ── Suppression List ──────────────────────────────────────
class SuppressionList(Base):
    """Global do-not-contact register with scoped suppression.

    Suppression reasons:
        UNSUBSCRIBED    — recipient clicked unsubscribe
        HARD_BOUNCE     — address does not exist (permanent)
        SPAM_COMPLAINT  — recipient marked as spam (permanent)
        DO_NOT_CONTACT  — manually added (permanent unless admin reverses)
        LEGAL_REQUEST   — GDPR/legal removal request (permanent)
        INVALID_ADDRESS — address format invalid (permanent)
        MANUAL_BLOCK    — admin block
        DOMAIN_BLOCKED  — entire domain blocked

    Suppression scopes:
        GLOBAL   — blocked across all campaigns
        CAMPAIGN — blocked for a specific campaign
        ACCOUNT  — blocked for the entire account/org
    """
    __tablename__ = "suppression_list"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    email       = Column(String, index=True)                        # specific email, or null for domain block
    domain      = Column(String, index=True)                        # for domain-level blocks
    reason      = Column(String, nullable=False)                    # see reasons above
    source      = Column(String)                                    # "unsubscribe_link", "bounce_handler", "manual", etc.
    scope       = Column(String, default="GLOBAL")                  # GLOBAL, CAMPAIGN, ACCOUNT
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))       # only set if scope=CAMPAIGN
    created_at  = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("email", "scope", "campaign_id", name="uq_suppression"),
    )

    def __repr__(self):
        return f"<SuppressionList(email='{self.email}', reason='{self.reason}', scope='{self.scope}')>"


# ── Sending Stats ─────────────────────────────────────────
class SendingStats(Base):
    """Per-mailbox hourly send counters for rate limiting and analytics."""
    __tablename__ = "sending_stats"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    mailbox_id      = Column(Integer, ForeignKey("mailboxes.id"), nullable=False)
    date            = Column(String, nullable=False)              # "2026-08-31"
    hour            = Column(Integer, default=0)                  # 0-23
    sent_count      = Column(Integer, default=0)
    bounce_count    = Column(Integer, default=0)
    complaint_count = Column(Integer, default=0)
    reply_count     = Column(Integer, default=0)

    # Relationships
    mailbox = relationship("Mailbox", back_populates="stats")

    __table_args__ = (
        UniqueConstraint("mailbox_id", "date", "hour", name="uq_stats_hour"),
    )

    def __repr__(self):
        return f"<SendingStats(mailbox={self.mailbox_id}, date='{self.date}', hour={self.hour}, sent={self.sent_count})>"


# ── Message Events ────────────────────────────────────────
class MessageEvent(Base):
    """Event pipeline record for every outbound message.
    Every send outcome (delivered, bounced, complaint, unsubscribed)
    is recorded here and feeds the event_processor for health recalc."""
    __tablename__ = "message_events"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    email_id        = Column(Integer, ForeignKey("emails.id"), nullable=False)
    mailbox_id      = Column(Integer, ForeignKey("mailboxes.id"))
    event_type      = Column(String, nullable=False, index=True)  # SENT, DELIVERED, BOUNCED, COMPLAINT, UNSUBSCRIBED
    bounce_type     = Column(String)                              # HARD, SOFT (only for BOUNCED events)
    bounce_reason   = Column(String)                              # SMTP error message
    provider_message_id = Column(String)                          # provider's own message ID
    created_at      = Column(DateTime, default=_utcnow)

    # Relationships
    email   = relationship("Email", back_populates="events")
    mailbox = relationship("Mailbox", back_populates="events")

    def __repr__(self):
        return f"<MessageEvent(email_id={self.email_id}, type='{self.event_type}')>"
