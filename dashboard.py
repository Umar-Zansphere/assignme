"""
dashboard.py — Sales Machine Full Pipeline Dashboard (v2 — Campaign-Driven)

Features:
  - Campaign Builder: natural language → LLM → editable config → create campaign
  - Stage Runner: run any pipeline stage from the dashboard with output capture
  - Campaign Selector: filter all views by selected campaign
  - Pipeline Monitor: live funnel + module data
  - Detailed views for each stage

Usage:
    python -m streamlit run dashboard.py
"""

import json
import os
import sys
import subprocess
import streamlit as st
import pandas as pd
from sqlalchemy import func, text

from database import get_session, init_db
from models import (
    Company, Signal, Contact, Research, Email, ReplyLog,
    Mailbox, DomainHealth, SuppressionList, SendingStats, Campaign,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Page Config ────────────────────────────────────────────
st.set_page_config(
    page_title="Sales Machine — Pipeline Monitor",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Glassmorphism metric cards */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, rgba(26,26,46,0.9) 0%, rgba(22,33,62,0.85) 100%);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    padding: 1rem 1.2rem;
    border-radius: 0.85rem;
    border: 1px solid rgba(255,255,255,0.1);
    box-shadow: 0 4px 20px rgba(0,0,0,0.2);
    transition: transform 0.3s ease, box-shadow 0.3s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-3px);
    box-shadow: 0 12px 32px rgba(102,126,234,0.2);
}
[data-testid="stMetricLabel"] { color: #a0aec0 !important; font-size: 0.78rem !important; letter-spacing: 0.3px !important; }
[data-testid="stMetricValue"] { color: #e2e8f0 !important; font-size: 1.6rem !important; font-weight: 700 !important; }

/* Sidebar */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d0d1a 0%, #1a1a3e 100%);
    border-right: 1px solid rgba(255,255,255,0.06);
}

/* Stage badge */
.stage-badge {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}

/* Status pills */
.pill-new     { background:#2d3748; color:#a0aec0; }
.pill-enriched{ background:#1a365d; color:#63b3ed; }
.pill-qualified{background:#1c4532; color:#68d391; }
.pill-rejected{ background:#742a2a; color:#fc8181; }
.pill-contact { background:#44337a; color:#d6bcfa; }
.pill-verified{ background:#234e52; color:#81e6d9; }
.pill-research{ background:#2a4365; color:#90cdf4; }
.pill-ready   { background:#744210; color:#f6e05e; }
.pill-sent    { background:#1a365d; color:#63b3ed; }
.pill-replied { background:#1c4532; color:#68d391; }

/* Email source badges */
.badge-apify { background:#1c4532; color:#68d391; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; }
.badge-guessed { background:#744210; color:#f6e05e; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; }
.badge-invalid { background:#742a2a; color:#fc8181; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; }

/* Module header */
.module-header {
    background: linear-gradient(90deg, rgba(102,126,234,0.15) 0%, rgba(118,75,162,0.05) 100%);
    border-left: 3px solid #667eea;
    border-radius: 0 8px 8px 0;
    padding: 10px 16px;
    margin: 12px 0 8px 0;
}

/* Detail card */
.detail-card {
    background: linear-gradient(135deg, rgba(26,26,46,0.9) 0%, rgba(22,33,62,0.85) 100%);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 18px;
    margin: 8px 0;
    transition: border-color 0.3s ease;
}
.detail-card:hover { border-color: rgba(102,126,234,0.3); }

/* Run button */
.run-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 6px 16px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.82rem;
    border: none;
    cursor: pointer;
    transition: transform 0.2s, box-shadow 0.2s;
}
.run-btn:hover { transform: scale(1.02); box-shadow: 0 4px 16px rgba(102,126,234,0.3); }

/* Campaign card */
.campaign-card {
    background: linear-gradient(135deg, rgba(26,26,46,0.95) 0%, rgba(22,33,62,0.9) 100%);
    border: 1px solid rgba(102,126,234,0.3);
    border-radius: 12px;
    padding: 20px;
    margin: 12px 0;
}

h1 {
    background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 1.9rem !important;
    font-weight: 800 !important;
    margin-bottom: 0 !important;
}
h2, h3 { color: #e2e8f0 !important; }
hr { border-color: rgba(255,255,255,0.08) !important; }

/* Funnel bar animation */
@keyframes funnel-fill {
    from { width: 0; }
    to { width: var(--bar-width); }
}
.funnel-bar {
    animation: funnel-fill 0.8s ease-out forwards;
    border-radius: 0 6px 6px 0;
    height: 32px;
    display: flex;
    align-items: center;
    padding: 0 12px;
    font-weight: 700;
    font-size: 0.82rem;
    color: white;
    margin: 4px 0;
}
</style>
""", unsafe_allow_html=True)

# ── Constants ──────────────────────────────────────────────
STATUS_COLOR = {
    "NEW_SIGNAL":    "#718096",
    "ENRICHED":      "#4299e1",
    "QUALIFIED":     "#48bb78",
    "REJECTED":      "#fc8181",
    "CONTACT_FOUND": "#9f7aea",
    "EMAIL_VERIFIED":"#38b2ac",
    "RESEARCH_DONE": "#63b3ed",
    "EMAIL_READY":   "#ecc94b",
    "EMAIL_SENT":    "#4299e1",
    "REPLIED":       "#48bb78",
}

SOURCE_COLOR = {
    "linkedin":    "#0a66c2",
    "apollo":      "#6366f1",
    "google_maps": "#34a853",
    "searxng":     "#f59e0b",
    "crunchbase":  "#0288d1",
}

PIPELINE_STAGES = [
    ("📡 Watcher",        "ENRICHED",      "#4299e1"),
    ("🎯 Scorer",         "QUALIFIED",     "#48bb78"),
    ("🔍 Finder",         "CONTACT_FOUND", "#9f7aea"),
    ("✅ Verifier",       "EMAIL_VERIFIED","#38b2ac"),
    ("📚 Research",       "RESEARCH_DONE", "#63b3ed"),
    ("✍️ Email Writer",   "EMAIL_READY",   "#ecc94b"),
    ("📤 Sender",         "EMAIL_SENT",    "#667eea"),
    ("💬 Reply Checker",  "REPLIED",       "#48bb78"),
]

STAGE_SCRIPTS = {
    "📡 Watcher":        "watcher.py",
    "🎯 Scorer":         "scorer.py",
    "🔍 Finder":         "finder.py",
    "✅ Verifier":       "verifier.py",
    "📚 Research":       "research.py",
    "✍️ Email Writer":   "email_writer.py",
    "📤 Sender":         "sender.py",
    "💬 Reply Checker":  "reply_checker.py",
}


# ── Stage Runner ───────────────────────────────────────────
def run_stage(script_name: str, campaign_id: int | None = None) -> tuple[str, str, int]:
    """Run a pipeline stage script and return (stdout, stderr, returncode)."""
    cmd = [sys.executable, os.path.join(BASE_DIR, script_name)]
    if campaign_id and script_name == "watcher.py":
        cmd.append(f"--campaign-id={campaign_id}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=BASE_DIR, timeout=600,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Stage timed out after 10 minutes", 1
    except Exception as e:
        return "", f"Failed to run: {e}", 1


def stage_runner_ui(stage_label: str, campaign_id: int | None = None):
    """Render the stage runner button and output display."""
    script = STAGE_SCRIPTS.get(stage_label)
    if not script:
        return

    col_run, col_info = st.columns([1, 4])
    with col_run:
        if st.button(f"▶️ Run {stage_label.split(' ', 1)[1] if ' ' in stage_label else stage_label}",
                      key=f"run_{script}", use_container_width=True):
            with st.spinner(f"Running {stage_label}..."):
                stdout, stderr, code = run_stage(script, campaign_id)

            if code == 0:
                st.success(f"✅ {stage_label} completed successfully")
            else:
                st.error(f"❌ {stage_label} failed (exit code {code})")

            with st.expander("📋 Output", expanded=(code != 0)):
                if stdout:
                    st.code(stdout[-3000:], language="text")
                if stderr:
                    st.code(stderr[-3000:], language="text")

            st.cache_data.clear()
            st.rerun()

    with col_info:
        st.caption(f"Script: `{script}`")


# ── Data Loaders ───────────────────────────────────────────
@st.cache_data(ttl=20)
def load_stats(campaign_id: int | None = None):
    init_db()
    with get_session() as s:
        base_q = s.query(Company)
        if campaign_id:
            base_q = base_q.filter_by(campaign_id=campaign_id)

        stats = {
            "total_signals":     s.query(Signal).count(),
            "total_companies":   base_q.count(),
            "qualified":         base_q.filter(Company.status.in_(
                                   ["QUALIFIED","CONTACT_FOUND","EMAIL_VERIFIED",
                                    "RESEARCH_DONE","EMAIL_READY","EMAIL_SENT","REPLIED"])).count(),
            "rejected":          base_q.filter_by(status="REJECTED").count(),
            "contacts_found":    s.query(Contact).count(),
            "contacts_verified": s.query(Contact).filter(Contact.verified.in_(
                                   ["PROSPEO_VERIFIED", "VALID", "PATTERN_ACCEPTED",
                                    "SMTP_VERIFIED", "WEB_SCRAPED", "APOLLO_VERIFIED"])).count(),
            "contacts_no_email": s.query(Contact).filter(
                                   (Contact.email == None) | (Contact.email == "")).count(),
            "research_done":     s.query(Research).count(),
            "emails_sent":       s.query(Email).filter_by(status="SENT").count(),
            "emails_scheduled":  s.query(Email).filter_by(status="SCHEDULED").count(),
            "emails_draft":      s.query(Email).filter_by(status="DRAFT").count(),
            "replies":           s.query(ReplyLog).count(),
            "new_signal":        base_q.filter_by(status="NEW_SIGNAL").count(),
            "phone_only":        base_q.filter_by(contact_channel="PHONE_ONLY").count(),
            "research_awaiting": base_q.filter(Company.status.in_(["EMAIL_VERIFIED"])).count(),
        }
        # reply rate
        c_emailed = s.query(func.count(func.distinct(Email.company_id))).filter_by(status="SENT").scalar() or 0
        c_replied = s.query(func.count(func.distinct(ReplyLog.company_id))).scalar() or 0
        stats["reply_rate"] = round(c_replied / c_emailed * 100, 1) if c_emailed else 0

        # signal sources
        stats["signal_sources"] = dict(s.execute(
            text("SELECT source, COUNT(*) FROM signals GROUP BY source ORDER BY COUNT(*) DESC")
        ).fetchall())

        # Pipeline funnel (cumulative)
        _FUNNEL_CUMULATIVE = {
            "ENRICHED":       ["ENRICHED", "QUALIFIED", "REJECTED",
                               "CONTACT_FOUND", "EMAIL_VERIFIED", "RESEARCH_DONE",
                               "EMAIL_READY", "EMAIL_SENT", "REPLIED"],
            "QUALIFIED":      ["QUALIFIED", "CONTACT_FOUND", "EMAIL_VERIFIED",
                               "RESEARCH_DONE", "EMAIL_READY", "EMAIL_SENT", "REPLIED"],
            "CONTACT_FOUND":  ["CONTACT_FOUND", "EMAIL_VERIFIED", "RESEARCH_DONE",
                               "EMAIL_READY", "EMAIL_SENT", "REPLIED"],
            "EMAIL_VERIFIED": ["EMAIL_VERIFIED", "RESEARCH_DONE",
                               "EMAIL_READY", "EMAIL_SENT", "REPLIED"],
            "RESEARCH_DONE":  ["RESEARCH_DONE", "EMAIL_READY", "EMAIL_SENT", "REPLIED"],
            "EMAIL_READY":    ["EMAIL_READY", "EMAIL_SENT", "REPLIED"],
            "EMAIL_SENT":     ["EMAIL_SENT", "REPLIED"],
            "REPLIED":        ["REPLIED"],
        }
        stats["pipeline"] = {}
        for _, status, _ in PIPELINE_STAGES:
            statuses = _FUNNEL_CUMULATIVE.get(status, [status])
            q = base_q.filter(Company.status.in_(statuses))
            stats["pipeline"][status] = q.count()
        stats["pipeline"]["REJECTED"] = stats["rejected"]

        # Campaign count
        stats["campaign_count"] = s.query(Campaign).count()

    return stats


@st.cache_data(ttl=20)
def load_campaigns():
    init_db()
    with get_session() as s:
        campaigns = s.query(Campaign).order_by(Campaign.created_at.desc()).all()
        return [{
            "id": c.id,
            "name": c.name,
            "brief": c.brief or "",
            "is_active": c.is_active,
            "sources": c.source_selection or "[]",
            "created_at": c.created_at,
        } for c in campaigns]


@st.cache_data(ttl=20)
def load_companies(campaign_id: int | None = None):
    init_db()
    with get_session() as s:
        q = s.query(Company).order_by(Company.updated_at.desc())
        if campaign_id:
            q = q.filter_by(campaign_id=campaign_id)
        cos = q.all()
        return pd.DataFrame([{
            "ID":        c.id,
            "Name":      c.name,
            "Industry":  c.industry or "",
            "Country":   c.country or "",
            "Employees": c.employee_count or 0,
            "ICP Score": c.icp_score or 0,
            "Status":    c.status,
            "Channel":   c.contact_channel or "",
            "Source":    c.signal_source or "",
            "Website":   c.website or "",
            "Phone":     c.phone or "",
            "LinkedIn":  c.linkedin_url or "",
            "Created":   c.created_at,
        } for c in cos])


@st.cache_data(ttl=20)
def load_contacts(campaign_id: int | None = None):
    init_db()
    with get_session() as s:
        q = (s.query(Contact, Company.name.label("co"), Company.status.label("co_status"))
                .join(Company, Contact.company_id == Company.id))
        if campaign_id:
            q = q.filter(Company.campaign_id == campaign_id)
        rows = q.order_by(Contact.created_at.desc()).all()
        return pd.DataFrame([{
            "Company":      r.co,
            "Co. Status":   r.co_status,
            "Name":         r.Contact.name or "",
            "Role":         r.Contact.role or "",
            "Email":        r.Contact.email or "",
            "Email Source":  r.Contact.email_source or "UNKNOWN",
            "LinkedIn":     r.Contact.linkedin_url or "",
            "Verified":     r.Contact.verified or "PENDING",
            "Found At":     r.Contact.created_at,
        } for r in rows])


@st.cache_data(ttl=20)
def load_research(campaign_id: int | None = None):
    init_db()
    with get_session() as s:
        q = (s.query(Research, Company.name.label("co"), Company.industry, Company.country)
                .join(Company, Research.company_id == Company.id))
        if campaign_id:
            q = q.filter(Company.campaign_id == campaign_id)
        rows = q.order_by(Research.created_at.desc()).all()
        data = []
        for r in rows:
            pain_points = []
            tech_stack = []
            try:
                pain_points = json.loads(r.Research.pain_points or "[]")
            except Exception:
                pain_points = [r.Research.pain_points] if r.Research.pain_points else []
            try:
                tech_stack = json.loads(r.Research.tech_stack or "[]")
            except Exception:
                tech_stack = [r.Research.tech_stack] if r.Research.tech_stack else []
            raw_data = {}
            try:
                raw_data = json.loads(r.Research.raw_json or "{}")
            except Exception:
                pass
            data.append({
                "Company":       r.co,
                "Industry":      r.industry or "",
                "Country":       r.country or "",
                "Summary":       r.Research.summary or "",
                "Pain Points":   ", ".join(pain_points) if isinstance(pain_points, list) else str(pain_points),
                "Tech Stack":    ", ".join(tech_stack) if isinstance(tech_stack, list) else str(tech_stack),
                "Recent News":   r.Research.recent_news or "",
                "Talking Points": ", ".join(raw_data.get("talking_points", [])) if raw_data.get("talking_points") else "",
                "Urgency":       raw_data.get("urgency", ""),
                "Created":       r.Research.created_at,
            })
        return pd.DataFrame(data)


@st.cache_data(ttl=20)
def load_emails(campaign_id: int | None = None):
    init_db()
    with get_session() as s:
        q = (s.query(Email, Contact.email.label("to_email"), Contact.name.label("contact_name"),
                        Company.name.label("co"))
                .join(Contact, Email.contact_id == Contact.id)
                .join(Company, Email.company_id == Company.id))
        if campaign_id:
            q = q.filter(Company.campaign_id == campaign_id)
        rows = q.order_by(Email.sent_at.desc().nullslast()).limit(200).all()
        return pd.DataFrame([{
            "ID":        r.Email.id,
            "Company":   r.co,
            "Contact":   r.contact_name,
            "To":        r.to_email,
            "Subject":   r.Email.subject or "",
            "Seq":       r.Email.sequence_number,
            "Status":    r.Email.status,
            "Body":      r.Email.body or "",
            "Scheduled": r.Email.scheduled_at,
            "Sent":      r.Email.sent_at,
        } for r in rows])


@st.cache_data(ttl=20)
def load_replies():
    init_db()
    with get_session() as s:
        rows = (s.query(ReplyLog, Company.name.label("co"))
                .join(Company, ReplyLog.company_id == Company.id)
                .order_by(ReplyLog.detected_at.desc()).all())
        return pd.DataFrame([{
            "Company":  r.co,
            "From":     r.ReplyLog.reply_from or "",
            "Subject":  r.ReplyLog.reply_subject or "",
            "Preview":  (r.ReplyLog.reply_body or "")[:200],
            "Body":     r.ReplyLog.reply_body or "",
            "Detected": r.ReplyLog.detected_at,
        } for r in rows])


# ── Helpers ────────────────────────────────────────────────
def status_pill(status: str) -> str:
    color = STATUS_COLOR.get(status, "#718096")
    return f'<span style="background:{color}22;color:{color};padding:2px 10px;border-radius:12px;font-size:0.72rem;font-weight:600;">{status}</span>'


def email_source_badge(source: str) -> str:
    if source in ("PROSPEO_VERIFIED",):
        return '<span class="badge-apify">✅ PROSPEO VERIFIED</span>'
    elif source in ("APOLLO_VERIFIED",):
        return '<span class="badge-apify">✅ APOLLO VERIFIED</span>'
    elif source in ("PROSPEO_CATCH_ALL",):
        return '<span class="badge-guessed">📫 CATCH-ALL</span>'
    elif source in ("PATTERN_ACCEPTED", "VALID", "SMTP_VERIFIED", "WEB_SCRAPED"):
        return '<span class="badge-apify">✅ VERIFIED</span>'
    elif source in ("NOT_FOUND",):
        return '<span class="badge-invalid">❌ NOT FOUND</span>'
    elif source in ("GUESSED", "PATTERN_DERIVED"):
        return '<span class="badge-guessed">⚠️ GUESSED</span>'
    elif source in ("APIFY_VERIFIED",):
        return '<span class="badge-apify">✅ APIFY VERIFIED</span>'
    else:
        return f'<span class="badge-invalid">❓ {source or "UNKNOWN"}</span>'


def module_header(icon: str, name: str, description: str, color: str = "#667eea"):
    st.markdown(
        f'<div class="module-header" style="border-left-color:{color};">'
        f'<span style="font-size:1.1rem;font-weight:700;color:{color};">{icon} {name}</span>'
        f'<span style="color:#718096;font-size:0.8rem;margin-left:12px;">{description}</span>'
        f'</div>',
        unsafe_allow_html=True
    )


def source_pills_html(source_counts: dict) -> str:
    pills = " ".join(
        f'<span style="background:{SOURCE_COLOR.get(s,"#718096")}33;color:{SOURCE_COLOR.get(s,"#a0aec0")};'
        f'padding:2px 10px;border-radius:12px;font-size:0.72rem;font-weight:600;">{s}: {c}</span>'
        for s, c in source_counts.items()
    )
    return pills


# ── Sidebar ────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚀 Sales Machine")
    st.markdown("*Dynamic Campaign Pipeline*")
    st.markdown("---")

    # Campaign selector
    campaigns = load_campaigns()
    if campaigns:
        campaign_names = ["🌐 All Campaigns"] + [f"#{c['id']} — {c['name']}" for c in campaigns]
        selected_campaign = st.selectbox("🎯 Campaign", campaign_names, label_visibility="collapsed")
        if selected_campaign == "🌐 All Campaigns":
            active_campaign_id = None
        else:
            active_campaign_id = int(selected_campaign.split(" — ")[0].replace("#", ""))
    else:
        st.info("No campaigns yet")
        active_campaign_id = None

    st.markdown("---")
    page = st.radio("Navigation", [
        "📊 Pipeline Monitor",
        "🎯 Campaign Builder",
        "📡 Watcher — Signals",
        "🏢 Companies",
        "🎯 Scorer — Qualification",
        "🔍 Contact Discovery",
        "📚 Research — Intelligence",
        "✍️ Email Writer — Drafts",
        "📤 Sender — Sent Emails",
        "💬 Replies",
        "📮 Sending Health",
        "🛡️ Compliance",
        "📬 Mailboxes",
        "⚙️ Settings",
    ], label_visibility="collapsed")
    st.markdown("---")
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Pipeline Stages")
    for label, status, color in PIPELINE_STAGES:
        st.markdown(
            f'<span style="color:{color};font-size:0.78rem;">● {label}</span>',
            unsafe_allow_html=True
        )


# ══════════════════════════════════════════════════════
# 1. PIPELINE MONITOR
# ══════════════════════════════════════════════════════
if page == "📊 Pipeline Monitor":
    st.title("Pipeline Monitor")
    if active_campaign_id:
        camp = next((c for c in campaigns if c["id"] == active_campaign_id), None)
        st.caption(f"Campaign: **{camp['name'] if camp else '?'}** (#{active_campaign_id})")
    else:
        st.caption("All campaigns — real-time view of every pipeline module")

    stats = load_stats(active_campaign_id)

    # ── Top KPIs ──
    c1,c2,c3,c4,c5,c6 = st.columns(6)
    c1.metric("📡 Signals",     stats["total_signals"])
    c2.metric("🏢 Companies",   stats["total_companies"])
    c3.metric("✅ Qualified",    stats["qualified"])
    c4.metric("👤 Contacts",    stats["contacts_found"])
    c5.metric("📧 Emails Sent", stats["emails_sent"])
    c6.metric("💬 Replies",     stats["replies"])

    st.divider()

    # ── Pipeline Funnel (gradient bars) ──
    st.subheader("🔽 Pipeline Funnel")
    st.caption("Each bar = companies that have **reached or passed** this stage. Width proportional to count.")

    max_count = max((stats["pipeline"].get(s, 0) for _, s, _ in PIPELINE_STAGES), default=1) or 1

    for label, status, color in PIPELINE_STAGES:
        cnt = stats["pipeline"].get(status, 0)
        pct = max(5, int(cnt / max_count * 100))  # min 5% width for visibility
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;margin:3px 0;">'
            f'<div style="width:120px;text-align:right;color:#a0aec0;font-size:0.75rem;">{label}</div>'
            f'<div style="flex:1;background:rgba(255,255,255,0.04);border-radius:6px;overflow:hidden;">'
            f'<div style="width:{pct}%;background:linear-gradient(90deg, {color}, {color}88);'
            f'height:28px;border-radius:6px;display:flex;align-items:center;padding:0 10px;'
            f'font-size:0.78rem;font-weight:700;color:white;">{cnt}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # Rejected bar
    rej = stats["pipeline"].get("REJECTED", 0)
    rej_pct = max(5, int(rej / max_count * 100)) if rej else 5
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:3px 0;">'
        f'<div style="width:120px;text-align:right;color:#fc8181;font-size:0.75rem;">❌ Rejected</div>'
        f'<div style="flex:1;background:rgba(255,255,255,0.04);border-radius:6px;overflow:hidden;">'
        f'<div style="width:{rej_pct}%;background:linear-gradient(90deg, #fc8181, #fc818188);'
        f'height:28px;border-radius:6px;display:flex;align-items:center;padding:0 10px;'
        f'font-size:0.78rem;font-weight:700;color:white;">{rej}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Phone-only flag
    if stats.get("phone_only", 0) > 0:
        st.markdown(
            f'<div style="margin-top:8px;padding:6px 14px;background:#744210aa;border:1px solid #f6e05e55;'
            f'border-radius:8px;display:inline-block;">'
            f'<span style="color:#f6e05e;font-weight:700;">{stats["phone_only"]}</span>'
            f'<span style="color:#a0aec0;font-size:0.8rem;margin-left:8px;">📞 Phone-only leads (need manual outreach)</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Module cards (2-per-row) with run buttons ──
    st.subheader("📋 Module Data + Stage Runners")

    for i in range(0, len(PIPELINE_STAGES), 2):
        cols = st.columns(2)
        for j, col in enumerate(cols):
            idx = i + j
            if idx >= len(PIPELINE_STAGES):
                break
            label, status, color = PIPELINE_STAGES[idx]
            cnt = stats["pipeline"].get(status, 0)
            with col:
                module_header(label.split(" ")[0], label.split(" ", 1)[1] if " " in label else label, f"{cnt} companies", color)
                stage_runner_ui(label, active_campaign_id)
        st.divider()


# ══════════════════════════════════════════════════════
# 2. CAMPAIGN BUILDER
# ══════════════════════════════════════════════════════
elif page == "🎯 Campaign Builder":
    st.title("Campaign Builder")
    st.caption("Create a new campaign from a natural language brief — the LLM generates the config, you review & edit.")

    # ── Existing campaigns ──
    if campaigns:
        st.subheader("📋 Existing Campaigns")
        for camp in campaigns:
            sources = json.loads(camp["sources"]) if camp["sources"] else []
            status_icon = "🟢" if camp["is_active"] else "🔴"
            st.markdown(
                f'<div class="campaign-card">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:1.05rem;font-weight:700;color:#e2e8f0;">{status_icon} {camp["name"]}</span>'
                f'<span style="color:#718096;font-size:0.78rem;">#{camp["id"]} • {camp["created_at"]}</span>'
                f'</div>'
                f'<div style="color:#a0aec0;font-size:0.82rem;margin-top:8px;">{camp["brief"][:200] if camp["brief"] else "No brief"}</div>'
                f'<div style="margin-top:8px;">{source_pills_html({s: "✓" for s in sources})}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.divider()

    # ── Create new campaign ──
    st.subheader("✨ Create New Campaign")

    brief = st.text_area(
        "📝 Describe your target audience and offering",
        height=150,
        placeholder=(
            "Example: We are Zansphere, a software solutions company. "
            "We want to find EV motorcycle startups in India that need "
            "custom dashboard software for their smart instrument clusters..."
        ),
    )

    if st.button("🧠 Generate Campaign Config", use_container_width=True, disabled=not brief):
        with st.spinner("🔮 LLM is decomposing your brief into a campaign config..."):
            try:
                from campaign_builder import decompose_brief
                config = decompose_brief(brief)
                st.session_state["campaign_config"] = config
                st.session_state["campaign_brief"] = brief
                st.success("✅ Campaign config generated! Review and edit below.")
            except Exception as e:
                st.error(f"❌ Failed to generate config: {e}")

    # ── Editable config ──
    if "campaign_config" in st.session_state:
        config = st.session_state["campaign_config"]
        st.divider()
        st.subheader("📋 Review & Edit Campaign Config")

        with st.form("campaign_form"):
            # Name
            name = st.text_input("Campaign Name", value=config.get("name", ""))

            col1, col2 = st.columns(2)
            with col1:
                # Target industries
                industries = st.text_area(
                    "Target Industries (one per line)",
                    value="\n".join(config.get("target_industries", [])),
                    height=100,
                )
                # Geography
                geography = st.text_area(
                    "Target Geography (one per line)",
                    value="\n".join(config.get("target_geography", [])),
                    height=80,
                )
                # Company size
                company_size = st.text_input(
                    "Target Company Size (employees)",
                    value=config.get("target_company_size", "1-500"),
                )

            with col2:
                # Target roles
                roles = st.text_area(
                    "Target Roles (one per line, in priority order)",
                    value="\n".join(config.get("target_roles", [])),
                    height=100,
                )
                
                import config as app_config
                
                # Source selection
                builtin_sources = list(app_config.AVAILABLE_DATA_SOURCES.keys())
                current_sources = config.get("source_selection", [])
                
                # Combine builtin with any custom sources generated by the LLM
                all_sources = list(set(builtin_sources + current_sources))
                
                selected_sources = st.multiselect(
                    "Data Sources",
                    all_sources,
                    default=current_sources,
                )
                
                custom_sources_input = st.text_input(
                    "Additional Custom Sources (comma separated, e.g. upwork)",
                    help="Added sources will dynamically appear in the Search Queries section below."
                )
                
                if custom_sources_input:
                    for s in custom_sources_input.split(","):
                        s = s.strip().lower()
                        if s and s not in selected_sources:
                            selected_sources.append(s)
                # Scoring threshold
                threshold = st.number_input(
                    "Scoring Threshold",
                    value=config.get("scoring_threshold", 60),
                    min_value=0, max_value=200,
                )

            st.markdown("---")

            # Search queries
            st.markdown("**🔍 Search Queries per Source**")
            search_queries = {}
            for source in selected_sources:
                queries = config.get("search_queries", {}).get(source, [])
                q_text = st.text_area(
                    f"Queries for {source}",
                    value="\n".join(queries) if isinstance(queries, list) else str(queries),
                    height=80,
                    key=f"q_{source}",
                )
                search_queries[source] = [q.strip() for q in q_text.split("\n") if q.strip()]

            st.markdown("---")

            # Scoring rules
            st.markdown("**📊 Scoring Rules**")
            scoring_rules = config.get("scoring_rules", [])
            rules_json = st.text_area(
                "Scoring Rules (JSON)",
                value=json.dumps(scoring_rules, indent=2),
                height=150,
            )

            st.markdown("---")

            # Email framing
            st.markdown("**✉️ Email Framing**")
            col3, col4 = st.columns(2)
            with col3:
                our_offering = st.text_area(
                    "Our Offering",
                    value=config.get("our_offering", ""),
                    height=80,
                )
                value_prop = st.text_area(
                    "Value Proposition",
                    value=config.get("value_proposition", ""),
                    height=80,
                )
            with col4:
                pitch_angle = st.text_area(
                    "Pitch Angle",
                    value=config.get("pitch_angle", ""),
                    height=80,
                )
                research_qs = st.text_area(
                    "Research Questions (one per line)",
                    value="\n".join(config.get("research_questions", [])),
                    height=80,
                )

            # Exclusion list
            exclusions = st.text_area(
                "Exclusion List (one per line)",
                value="\n".join(config.get("exclusion_list", [])),
                height=60,
            )

            submitted = st.form_submit_button("✅ Create Campaign", use_container_width=True)

            if submitted:
                try:
                    parsed_rules = json.loads(rules_json)
                except Exception:
                    parsed_rules = scoring_rules

                final_config = {
                    "name": name,
                    "target_industries": [i.strip() for i in industries.split("\n") if i.strip()],
                    "target_geography": [g.strip() for g in geography.split("\n") if g.strip()],
                    "target_company_size": company_size,
                    "target_roles": [r.strip() for r in roles.split("\n") if r.strip()],
                    "source_selection": selected_sources,
                    "search_queries": search_queries,
                    "scoring_rules": parsed_rules,
                    "scoring_threshold": threshold,
                    "exclusion_list": [e.strip() for e in exclusions.split("\n") if e.strip()],
                    "our_offering": our_offering,
                    "value_proposition": value_prop,
                    "pitch_angle": pitch_angle,
                    "research_questions": [q.strip() for q in research_qs.split("\n") if q.strip()],
                }

                try:
                    from campaign_builder import create_campaign_from_config
                    with get_session() as session:
                        campaign = create_campaign_from_config(
                            session,
                            final_config,
                            st.session_state.get("campaign_brief", brief),
                        )
                        campaign_id = campaign.id

                    st.success(f"🎉 Campaign **'{name}'** created (#{campaign_id})! You can now run the Watcher.")
                    del st.session_state["campaign_config"]
                    del st.session_state["campaign_brief"]
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Failed to create campaign: {e}")


# ══════════════════════════════════════════════════════
# 3. WATCHER — SIGNALS
# ══════════════════════════════════════════════════════
elif page == "📡 Watcher — Signals":
    module_header("📡", "Watcher", "Stage 1 — Multi-source signal collection", "#f59e0b")
    stage_runner_ui("📡 Watcher", active_campaign_id)
    st.divider()

    stats = load_stats(active_campaign_id)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Total Signals",   stats["total_signals"])
    c2.metric("Companies Found", stats["total_companies"])
    c3.metric("📞 Phone Only",   stats.get("phone_only", 0))
    c4.metric("Campaigns",       stats.get("campaign_count", 0))

    # Signal source breakdown
    if stats.get("signal_sources"):
        st.markdown(source_pills_html(stats["signal_sources"]), unsafe_allow_html=True)

    st.divider()
    df = load_companies(active_campaign_id)
    if df.empty:
        st.info("No signals yet. Create a campaign first, then run the Watcher.")
    else:
        recent = df.head(50)
        st.dataframe(recent[["Name", "Industry", "Country", "Source", "Channel", "Status", "Website"]],
                      use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(recent)} / {len(df)} companies")


# ══════════════════════════════════════════════════════
# 4. COMPANIES
# ══════════════════════════════════════════════════════
elif page == "🏢 Companies":
    module_header("🏢", "Companies", "All companies across sources", "#4299e1")

    df = load_companies(active_campaign_id)
    if df.empty:
        st.info("No companies yet. Run the watcher first.")
    else:
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Companies", len(df))
        c2.metric("Industries",      df["Industry"].nunique())
        c3.metric("Countries",       df["Country"].nunique())
        c4.metric("Avg ICP Score",   round(df["ICP Score"].mean(), 1) if len(df) > 0 else 0)

        st.divider()

        col1, col2, col3 = st.columns(3)
        with col1:
            status_opts = sorted(df["Status"].unique().tolist())
            st_sel = st.multiselect("Status", status_opts, default=status_opts)
        with col2:
            source_opts = sorted(df["Source"].unique().tolist())
            src_sel = st.multiselect("Source", source_opts, default=source_opts) if source_opts else source_opts
        with col3:
            channel_opts = sorted(df["Channel"].unique().tolist())
            ch_sel = st.multiselect("Channel", channel_opts, default=channel_opts) if channel_opts else channel_opts

        filtered = df[df["Status"].isin(st_sel)]
        if src_sel:
            filtered = filtered[filtered["Source"].isin(src_sel)]
        if ch_sel:
            filtered = filtered[filtered["Channel"].isin(ch_sel)]

        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.caption(f"{len(filtered)} companies shown")

        # Charts
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Top Industries")
            ind_counts = df[df["Industry"] != ""]["Industry"].value_counts().head(8).reset_index()
            if not ind_counts.empty:
                ind_counts.columns = ["Industry", "Count"]
                st.bar_chart(ind_counts.set_index("Industry"))
        with col2:
            st.subheader("Source Breakdown")
            src_counts = df[df["Source"] != ""]["Source"].value_counts().reset_index()
            if not src_counts.empty:
                src_counts.columns = ["Source", "Count"]
                st.bar_chart(src_counts.set_index("Source"))


# ══════════════════════════════════════════════════════
# 5. SCORER — QUALIFICATION
# ══════════════════════════════════════════════════════
elif page == "🎯 Scorer — Qualification":
    module_header("🎯", "Scorer", "Stage 2 — Dynamic ICP scoring from campaign rules", "#48bb78")
    stage_runner_ui("🎯 Scorer", active_campaign_id)
    st.divider()

    df = load_companies(active_campaign_id)
    scored = df[df["Status"].isin(["QUALIFIED","REJECTED","CONTACT_FOUND","EMAIL_VERIFIED",
                                   "RESEARCH_DONE","EMAIL_READY","EMAIL_SENT","REPLIED"])]

    if scored.empty:
        st.info("No scored companies yet. Run the Scorer.")
    else:
        qualified = scored[scored["Status"] != "REJECTED"]
        rejected  = scored[scored["Status"] == "REJECTED"]

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Scored",    len(scored))
        c2.metric("✅ Qualified", len(qualified))
        c3.metric("❌ Rejected",  len(rejected))
        c4.metric("Pass Rate", f"{round(len(qualified)/len(scored)*100)}%" if len(scored) else "—")

        st.divider()

        tab_q, tab_r = st.tabs([f"✅ Qualified ({len(qualified)})", f"❌ Rejected ({len(rejected)})"])
        with tab_q:
            st.dataframe(
                qualified[["Name","Industry","Country","Employees","ICP Score","Status","Source"]]
                .sort_values("ICP Score", ascending=False),
                use_container_width=True, hide_index=True
            )
        with tab_r:
            st.dataframe(
                rejected[["Name","Industry","Country","Employees","ICP Score","Source"]]
                .sort_values("ICP Score", ascending=False),
                use_container_width=True, hide_index=True
            )

        st.divider()
        st.subheader("ICP Score Distribution")
        score_df = scored[["Name","ICP Score","Status"]].sort_values("ICP Score")
        st.bar_chart(score_df.set_index("Name")["ICP Score"])


# ══════════════════════════════════════════════════════
# 6. CONTACT DISCOVERY
# ══════════════════════════════════════════════════════
elif page == "🔍 Contact Discovery":
    module_header("🔍", "Contact Discovery", "Stages 3 & 4 — Finds decision makers, enriches email", "#9f7aea")
    stage_runner_ui("🔍 Finder", active_campaign_id)
    st.divider()

    df = load_contacts(active_campaign_id)
    if df.empty:
        st.info("No contacts yet. Run the Finder.")
    else:
        _VERIFIED_STATUSES = ["PROSPEO_VERIFIED", "VALID", "PATTERN_ACCEPTED", "SMTP_VERIFIED", "WEB_SCRAPED", "APOLLO_VERIFIED"]
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Contacts",  len(df))
        c2.metric("✅ Verified",     len(df[df["Verified"].isin(_VERIFIED_STATUSES)]))
        c3.metric("❌ No Email",     len(df[df["Email"] == ""]))
        c4.metric("📫 Catch-All",   len(df[df["Verified"]=="PROSPEO_CATCH_ALL"]) if "PROSPEO_CATCH_ALL" in df["Verified"].values else 0)

        st.divider()

        # Email source breakdown
        if "Email Source" in df.columns:
            source_counts = df["Email Source"].value_counts().reset_index()
            source_counts.columns = ["Source", "Count"]
            for _, row in source_counts.iterrows():
                st.markdown(f"{email_source_badge(row['Source'])} — **{row['Count']}** contacts", unsafe_allow_html=True)

        st.divider()
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(df)} contacts shown")


# ══════════════════════════════════════════════════════
# 7. RESEARCH — INTELLIGENCE
# ══════════════════════════════════════════════════════
elif page == "📚 Research — Intelligence":
    module_header("📚", "Research", "Stage 5 — Campaign-aware LLM research", "#63b3ed")
    stage_runner_ui("📚 Research", active_campaign_id)
    st.divider()

    df = load_research(active_campaign_id)
    if df.empty:
        st.info("No research done yet. Run the Research stage.")
    else:
        c1,c2,c3 = st.columns(3)
        c1.metric("Companies Researched", len(df))
        c2.metric("With Tech Stack",      len(df[df["Tech Stack"] != ""]))
        c3.metric("High Urgency",         len(df[df["Urgency"] == "high"]) if "Urgency" in df.columns else 0)

        st.divider()
        st.dataframe(df[["Company","Industry","Country","Summary","Pain Points","Tech Stack","Urgency"]],
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("🔍 Deep Dive")
        if not df.empty:
            company_sel = st.selectbox("Select company", df["Company"].tolist())
            row = df[df["Company"] == company_sel].iloc[0]

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**📋 Summary**")
                st.info(row["Summary"] or "—")
                st.markdown("**🔧 Tech Stack**")
                if row["Tech Stack"]:
                    for tech in row["Tech Stack"].split(", "):
                        st.markdown(f"  • {tech}")
                else:
                    st.write("—")
            with col2:
                st.markdown("**😤 Pain Points**")
                if row["Pain Points"]:
                    for pp in row["Pain Points"].split(", "):
                        st.markdown(f"  • {pp}")
                else:
                    st.write("—")
                if row.get("Talking Points"):
                    st.markdown("**💬 Talking Points**")
                    for tp in row["Talking Points"].split(", "):
                        st.markdown(f"  • {tp}")


# ══════════════════════════════════════════════════════
# 8. EMAIL WRITER — DRAFTS
# ══════════════════════════════════════════════════════
elif page == "✍️ Email Writer — Drafts":
    module_header("✍️", "Email Writer", "Stage 6 — Campaign-aware personalized emails", "#ecc94b")
    stage_runner_ui("✍️ Email Writer", active_campaign_id)
    st.divider()

    df = load_emails(active_campaign_id)
    if df.empty:
        st.info("No emails written yet. Run the Email Writer.")
    else:
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Emails", len(df))
        c2.metric("Drafts",       len(df[df["Status"]=="DRAFT"]))
        c3.metric("Scheduled",    len(df[df["Status"]=="SCHEDULED"]))
        c4.metric("Sent",         len(df[df["Status"]=="SENT"]))

        st.divider()

        status_sel = st.multiselect("Status Filter", df["Status"].unique().tolist(),
                                    default=df["Status"].unique().tolist())
        filtered = df[df["Status"].isin(status_sel)]
        st.dataframe(filtered[["Company","Contact","To","Subject","Seq","Status","Scheduled","Sent"]],
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📨 Email Preview & Editor")

        if not filtered.empty:
            company_list = filtered["Company"].unique().tolist()
            sel_company = st.selectbox("Select company", company_list, key="email_company")
            company_emails = filtered[filtered["Company"] == sel_company]

            for _, row in company_emails.iterrows():
                seq_label = {0: "Initial Email", 1: "Follow-up 1", 2: "Follow-up 2"}.get(row["Seq"], f"Email #{row['Seq']}")
                status_icon = {"SCHEDULED": "📅", "SENT": "✅", "DRAFT": "📝", "FAILED": "❌"}.get(row["Status"], "📧")

                with st.expander(f"{status_icon} {seq_label} — {row['Subject']} [{row['Status']}]", expanded=(row["Seq"] == 0)):
                    st.markdown(f"**To:** {row['To']}  |  **Status:** {status_pill(row['Status'])}", unsafe_allow_html=True)

                    can_edit = row["Status"] in ("SCHEDULED", "DRAFT")

                    new_subject = st.text_input("Subject", value=row["Subject"],
                                                key=f"subj_{row['ID']}", disabled=not can_edit)
                    new_body = st.text_area("Body", value=row["Body"], height=200,
                                           key=f"body_{row['ID']}", disabled=not can_edit)

                    if can_edit:
                        col_save, col_cancel = st.columns([1, 3])
                        with col_save:
                            if st.button("💾 Save", key=f"save_{row['ID']}"):
                                try:
                                    with get_session() as session:
                                        email_record = session.query(Email).filter_by(id=row["ID"]).first()
                                        if email_record:
                                            email_record.subject = new_subject
                                            email_record.body = new_body
                                    st.success("Saved!")
                                    st.cache_data.clear()
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Save failed: {e}")
                        with col_cancel:
                            if row["Status"] == "SCHEDULED":
                                if st.button("🚫 Cancel", key=f"cancel_{row['ID']}"):
                                    try:
                                        with get_session() as session:
                                            email_record = session.query(Email).filter_by(id=row["ID"]).first()
                                            if email_record:
                                                email_record.status = "CANCELLED"
                                        st.cache_data.clear()
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Cancel failed: {e}")


# ══════════════════════════════════════════════════════
# 9. SENDER — SENT EMAILS
# ══════════════════════════════════════════════════════
elif page == "📤 Sender — Sent Emails":
    module_header("📤", "Sender", "Stage 7 — Delivers emails via SMTP/Gmail API", "#667eea")
    stage_runner_ui("📤 Sender", active_campaign_id)
    st.divider()

    df = load_emails(active_campaign_id)
    sent = df[df["Status"]=="SENT"] if not df.empty else pd.DataFrame()
    scheduled = df[df["Status"]=="SCHEDULED"] if not df.empty else pd.DataFrame()

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Sent",       len(sent))
    c2.metric("Scheduled",  len(scheduled))
    c3.metric("Failed",     len(df[df["Status"]=="FAILED"]) if not df.empty else 0)
    stats = load_stats(active_campaign_id)
    c4.metric("Reply Rate", f"{stats['reply_rate']}%")

    st.divider()
    tab1, tab2, tab3 = st.tabs(["📤 Sent", "📅 Scheduled", "🎯 Selective Send"])

    with tab1:
        if sent.empty:
            st.info("No emails sent yet. Run the Sender or use Selective Send below.")
        else:
            st.dataframe(sent[["Company","Contact","To","Subject","Seq","Sent"]],
                         use_container_width=True, hide_index=True)

    with tab2:
        if scheduled.empty:
            st.info("No emails scheduled.")
        else:
            st.dataframe(scheduled[["Company","Contact","To","Subject","Seq","Scheduled"]],
                         use_container_width=True, hide_index=True)

    # ── Selective Send ─────────────────────────────────────────────────
    with tab3:
        st.subheader("🎯 Selective Send")
        st.caption(
            "Choose one or more scheduled emails to send immediately, "
            "bypassing the scheduler. All compliance and circuit-breaker "
            "checks still apply."
        )

        if scheduled.empty:
            st.info("No scheduled emails available. Run the Email Writer first to generate a sequence.")
        else:
            # ── Filter ──
            search = st.text_input(
                "🔍 Filter by company or email address",
                placeholder="e.g. Acme or john@acme.com",
                key="selective_filter",
            )
            filtered = scheduled.copy()
            if search.strip():
                mask = (
                    filtered["Company"].str.contains(search, case=False, na=False)
                    | filtered["To"].str.contains(search, case=False, na=False)
                    | filtered["Contact"].str.contains(search, case=False, na=False)
                )
                filtered = filtered[mask]

            if filtered.empty:
                st.warning("No scheduled emails match the filter.")
            else:
                # Build readable labels for the multiselect
                def _label(row):
                    seq_name = {0: "Initial", 1: "Follow-up 1", 2: "Follow-up 2"}.get(row["Seq"], f"Seq {row['Seq']}")
                    return f"#{row['ID']}  {row['Company']}  →  {row['To']}  [{seq_name}]"

                filtered["_label"] = filtered.apply(_label, axis=1)
                label_to_id = dict(zip(filtered["_label"], filtered["ID"]))

                selected_labels = st.multiselect(
                    f"Select emails to send  ({len(filtered)} available)",
                    options=list(label_to_id.keys()),
                    placeholder="Pick one or more emails…",
                    key="selective_picks",
                )

                if selected_labels:
                    selected_ids = [label_to_id[lbl] for lbl in selected_labels]

                    # Preview table
                    preview_df = filtered[filtered["ID"].isin(selected_ids)][
                        ["Company", "Contact", "To", "Subject", "Seq", "Scheduled"]
                    ]
                    st.dataframe(preview_df, use_container_width=True, hide_index=True)

                    dry_run_toggle = st.checkbox("🔍 Dry run (preview only, don't actually send)", value=False)

                    col_send, col_cancel = st.columns([1, 4])
                    if col_send.button(
                        "📨 Send Selected" if not dry_run_toggle else "🔍 Preview Send",
                        type="primary",
                        use_container_width=True,
                        key="do_selective_send",
                    ):
                        from sending_orchestrator import request_send
                        from models import Email as EmailModel, Contact as ContactModel, Company as CompanyModel, Campaign as CampaignModel

                        results = {"sent": [], "blocked": [], "failed": []}
                        progress = st.progress(0, text="Preparing…")

                        with get_session() as s:
                            for idx, email_id in enumerate(selected_ids):
                                progress.progress(
                                    (idx + 1) / len(selected_ids),
                                    text=f"Processing {idx+1}/{len(selected_ids)}…",
                                )
                                email_obj = s.query(EmailModel).filter_by(id=email_id).first()
                                if not email_obj:
                                    results["failed"].append((email_id, "Email record not found"))
                                    continue
                                if email_obj.status != "SCHEDULED":
                                    results["blocked"].append((email_id, f"Status is {email_obj.status}, not SCHEDULED"))
                                    continue

                                campaign_obj = None
                                if email_obj.campaign_id:
                                    campaign_obj = s.query(CampaignModel).filter_by(id=email_obj.campaign_id).first()

                                if dry_run_toggle:
                                    contact_obj = s.query(ContactModel).filter_by(id=email_obj.contact_id).first()
                                    to_addr = contact_obj.email if contact_obj else "?"
                                    results["sent"].append((email_id, f"[DRY RUN] Would send to {to_addr}: {email_obj.subject}"))
                                else:
                                    try:
                                        result = request_send(s, email_obj, campaign_obj)
                                        if result.sent:
                                            results["sent"].append((email_id, f"✅ Sent via {result.mailbox.email} (ID: {result.message_id})"))
                                        elif result.blocked:
                                            results["blocked"].append((email_id, result.reason))
                                        else:
                                            results["failed"].append((email_id, result.reason))
                                    except Exception as exc:
                                        results["failed"].append((email_id, str(exc)))

                        progress.empty()
                        st.cache_data.clear()

                        if results["sent"]:
                            st.success(f"{'[DRY RUN] ' if dry_run_toggle else ''}✅ {len(results['sent'])} email(s) sent")
                            for eid, msg in results["sent"]:
                                st.write(f"  • #{eid} — {msg}")

                        if results["blocked"]:
                            st.warning(f"⚠️ {len(results['blocked'])} email(s) blocked by compliance / circuit breakers:")
                            for eid, msg in results["blocked"]:
                                st.write(f"  • #{eid} — {msg}")

                        if results["failed"]:
                            st.error(f"❌ {len(results['failed'])} email(s) failed:")
                            for eid, msg in results["failed"]:
                                st.write(f"  • #{eid} — {msg}")

                        if not dry_run_toggle:
                            st.rerun()
                else:
                    st.info("Select at least one email above to enable sending.")


# ══════════════════════════════════════════════════════
# 10. REPLIES
# ══════════════════════════════════════════════════════
elif page == "💬 Replies":
    module_header("💬", "Reply Checker", "Stage 8 — IMAP inbox monitoring", "#48bb78")
    stage_runner_ui("💬 Reply Checker", active_campaign_id)
    st.divider()

    stats = load_stats(active_campaign_id)
    df = load_replies()

    c1,c2,c3 = st.columns(3)
    c1.metric("Replies", stats["replies"])
    c2.metric("Emails Sent", stats["emails_sent"])
    c3.metric("Reply Rate", f"{stats['reply_rate']}%")

    st.divider()
    if df.empty:
        st.info("No replies detected yet.")
    else:
        st.dataframe(df[["Company","From","Subject","Detected"]], use_container_width=True, hide_index=True)
        st.divider()
        for _, row in df.iterrows():
            with st.expander(f"📩 {row['Company']} — {row['Subject']}"):
                st.markdown(f"**From:** {row['From']}")
                st.write(row.get("Body", row.get("Preview", "")))


# ══════════════════════════════════════════════════════
# 11. SENDING HEALTH
# ══════════════════════════════════════════════════════
elif page == "📮 Sending Health":
    st.title("Sending Health")
    st.caption("Domain authentication, mailbox pool health, and compliance metrics")

    init_db()
    with get_session() as s:
        all_mailboxes = s.query(Mailbox).order_by(Mailbox.domain, Mailbox.email).all()
        all_domains = s.query(DomainHealth).order_by(DomainHealth.domain).all()
        total_suppressed = s.query(SuppressionList).count()

        from utils import utcnow
        today = utcnow().strftime("%Y-%m-%d")
        today_sent = s.query(func.coalesce(func.sum(SendingStats.sent_count), 0)).filter_by(date=today).scalar() or 0
        today_bounces = s.query(func.coalesce(func.sum(SendingStats.bounce_count), 0)).filter_by(date=today).scalar() or 0

    c1, c2, c3, c4 = st.columns(4)
    active_mb = len([m for m in all_mailboxes if m.is_active and m.status != "PAUSED"])
    c1.metric("📬 Active Mailboxes", f"{active_mb}/{len(all_mailboxes)}")
    c2.metric("📤 Sent Today", today_sent)
    c3.metric("🔙 Bounces Today", today_bounces)
    c4.metric("🚫 Suppressed", total_suppressed)

    st.divider()

    if all_domains:
        st.subheader("🌐 Domain Health")
        for domain in all_domains:
            spf_icon = "✅" if domain.spf_valid else "❌"
            dkim_icon = "✅" if domain.dkim_valid else "❌"
            dmarc_icon = "✅" if domain.dmarc_valid else "❌"
            color = {"VERIFIED": "#48bb78", "PENDING_VERIFICATION": "#ecc94b", "DEGRADED": "#fc8181"}.get(domain.status, "#718096")
            last_checked = domain.dns_last_checked.strftime("%Y-%m-%d %H:%M UTC") if domain.dns_last_checked else "Never"
            with st.expander(f"🌐 {domain.domain}  —  {domain.status}", expanded=True):
                st.markdown(
                    f'<div style="color:#a0aec0;font-size:0.85rem;margin-bottom:8px;">'
                    f'SPF {spf_icon} &nbsp;•&nbsp; DKIM {dkim_icon} &nbsp;•&nbsp; DMARC {dmarc_icon} &nbsp;•&nbsp; '
                    f'Sent: {domain.total_sent or 0} &nbsp;•&nbsp; Bounce rate: {(domain.bounce_rate or 0)*100:.2f}% &nbsp;•&nbsp; '
                    f'Spam rate: {(domain.spam_rate or 0)*100:.3f}% &nbsp;•&nbsp; Last checked: {last_checked}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # ── Verify DNS button ──
                if st.button(f"🔍 Verify DNS for {domain.domain}", key=f"verify_{domain.domain}"):
                    with st.spinner(f"Running DNS checks for {domain.domain}..."):
                        try:
                            from dns_checker import verify_domain
                            result = verify_domain(domain.domain)
                            st.cache_data.clear()
                            if result.all_passed:
                                st.success(f"✅ All DNS checks passed — {domain.domain} is ready for sending")
                            else:
                                st.warning(f"⚠️ DNS issues found for {domain.domain}")
                            # Show checklist
                            for chk in result.checks:
                                icon = "✅" if chk["status"] == "✓" else "❌"
                                with st.container():
                                    st.markdown(
                                        f"**{icon} {chk['record_type']}** — `{chk['name']}` ({chk['dns_type']})"
                                    )
                                    st.caption(chk["value"] or "—")
                                    if chk["status"] != "✓":
                                        st.info(f"💡 Fix: {chk['instruction']}")
                        except Exception as e:
                            st.error(f"DNS check failed: {e}")

    else:
        st.info("No sending domains configured yet. Add a mailbox first — its domain will appear here.")

    # ── Add domain for verification ──
    st.divider()
    st.subheader("🔍 Verify a Domain")
    st.caption("Enter any sending domain to run SPF / DKIM / DMARC checks and register it in the health tracker.")
    with st.form("verify_domain_form"):
        new_domain = st.text_input("Domain (e.g. yourcompany.com)", placeholder="yourcompany.com")
        submitted = st.form_submit_button("Run Verification", use_container_width=True)
        if submitted and new_domain.strip():
            dom = new_domain.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
            with st.spinner(f"Checking DNS for {dom}..."):
                try:
                    from dns_checker import verify_domain
                    result = verify_domain(dom)
                    st.cache_data.clear()
                    if result.all_passed:
                        st.success(f"✅ All DNS checks passed for {dom}")
                    else:
                        st.warning(f"⚠️ DNS issues found for {dom}")
                    for chk in result.checks:
                        icon = "✅" if chk["status"] == "✓" else "❌"
                        st.markdown(f"**{icon} {chk['record_type']}** ({chk['dns_type']}) — {chk['value'] or '—'}")
                        if chk["status"] != "✓":
                            st.info(f"💡 {chk['instruction']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Verification failed: {e}")


# ══════════════════════════════════════════════════════
# 12. COMPLIANCE
# ══════════════════════════════════════════════════════
elif page == "🛡️ Compliance":
    st.title("Compliance — Suppression List")
    st.caption("Global do-not-contact register")

    init_db()
    with get_session() as s:
        total = s.query(SuppressionList).count()
        by_reason = dict(
            s.query(SuppressionList.reason, func.count(SuppressionList.id))
            .group_by(SuppressionList.reason).all()
        )

    c1, c2, c3 = st.columns(3)
    c1.metric("🚫 Total Suppressed", total)
    c2.metric("📧 Unsubscribed", by_reason.get("UNSUBSCRIBED", 0))
    c3.metric("🔙 Hard Bounces", by_reason.get("HARD_BOUNCE", 0))

    st.divider()

    tab1, tab2 = st.tabs(["📋 View List", "➕ Add Entry"])

    with tab1:
        with get_session() as s:
            entries = s.query(SuppressionList).order_by(SuppressionList.created_at.desc()).limit(500).all()
            if entries:
                df = pd.DataFrame([{
                    "Email": e.email or "—",
                    "Domain": e.domain or "—",
                    "Reason": e.reason,
                    "Source": e.source or "—",
                    "Added": e.created_at,
                } for e in entries])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No entries in the suppression list.")

    with tab2:
        with st.form("add_suppression"):
            email_input = st.text_input("Email address")
            reason_input = st.selectbox("Reason", [
                "MANUAL_BLOCK", "DO_NOT_CONTACT", "UNSUBSCRIBED",
                "HARD_BOUNCE", "SPAM_COMPLAINT", "INVALID_ADDRESS",
            ])
            submitted = st.form_submit_button("Add to Suppression List")
            if submitted and email_input:
                from compliance import suppress_email
                with get_session() as s:
                    suppress_email(s, email_input, reason_input, "dashboard")
                st.success(f"**{email_input}** suppressed")
                st.cache_data.clear()
                st.rerun()


# ══════════════════════════════════════════════════════
# 13. MAILBOXES
# ══════════════════════════════════════════════════════
elif page == "📬 Mailboxes":
    st.title("Mailbox Management")
    st.caption("Add, configure, and manage your sending mailbox pool")

    init_db()

    # ── Add Mailbox ──
    st.subheader("➕ Add New Mailbox")
    add_tab1, add_tab2 = st.tabs(["🔗 Google OAuth", "🔧 Manual SMTP"])

    with add_tab1:
        with st.form("add_google_mailbox"):
            g_email = st.text_input("Email address", placeholder="john@yourcompany.com")
            g_display = st.text_input("Display name", placeholder="John from Acme")
            submitted = st.form_submit_button("Create & Connect Google", use_container_width=True)
            if submitted and g_email:
                domain = g_email.split("@")[-1] if "@" in g_email else ""
                from config import GOOGLE_CLIENT_ID, UNSUBSCRIBE_SERVER_PORT
                if not GOOGLE_CLIENT_ID:
                    st.error("❌ GOOGLE_CLIENT_ID not set in .env")
                else:
                    with get_session() as s:
                        existing = s.query(Mailbox).filter_by(email=g_email).first()
                        if existing:
                            st.info(f"Mailbox **{g_email}** already exists.")
                        else:
                            new_mb = Mailbox(
                                email=g_email,
                                display_name=g_display or g_email.split("@")[0],
                                domain=domain, provider="google",
                                smtp_host="smtp.gmail.com", smtp_port=465,
                                imap_host="imap.gmail.com", imap_port=993,
                            )
                            s.add(new_mb)
                            s.flush()
                            dh = s.query(DomainHealth).filter_by(domain=domain).first()
                            if not dh:
                                s.add(DomainHealth(domain=domain))
                            mailbox_id = new_mb.id
                    oauth_url = f"http://localhost:{UNSUBSCRIBE_SERVER_PORT}/oauth/google/authorize?mailbox_id={mailbox_id}"
                    st.success(f"✅ Mailbox created. Redirecting to Google OAuth...")
                    st.link_button("🔗 Connect Google", oauth_url)

    with add_tab2:
        _PRESETS = {
            "Hostinger": ("smtp.hostinger.com", 465, "imap.hostinger.com"),
            "Gmail (App Password)": ("smtp.gmail.com", 465, "imap.gmail.com"),
            "Outlook / O365": ("smtp.office365.com", 587, "outlook.office365.com"),
            "Zoho": ("smtp.zoho.com", 465, "imap.zoho.com"),
            "Custom": ("", 587, ""),
        }
        preset_choice = st.selectbox("Provider preset", list(_PRESETS.keys()), index=0)
        _ph, _pp, _ih = _PRESETS[preset_choice]

        with st.form("add_smtp_mailbox"):
            col1, col2 = st.columns(2)
            with col1:
                mb_email = st.text_input("Email", placeholder="john@getacme.com")
                mb_smtp_host = st.text_input("SMTP host", value=_ph)
                mb_smtp_port = st.number_input("SMTP port", value=_pp)
            with col2:
                mb_smtp_user = st.text_input("SMTP username (leave blank = email)")
                mb_smtp_pass = st.text_input("SMTP password", type="password")
                mb_imap_host = st.text_input("IMAP host", value=_ih)

            submitted = st.form_submit_button("Add SMTP Mailbox", use_container_width=True)
            if submitted and mb_email:
                domain = mb_email.split("@")[-1]
                with get_session() as s:
                    existing = s.query(Mailbox).filter_by(email=mb_email).first()
                    if existing:
                        st.error(f"Mailbox **{mb_email}** already exists")
                    else:
                        s.add(Mailbox(
                            email=mb_email, display_name=mb_email.split("@")[0],
                            domain=domain, provider="smtp",
                            smtp_host=mb_smtp_host, smtp_port=mb_smtp_port,
                            smtp_username=mb_smtp_user or mb_email, smtp_password=mb_smtp_pass,
                            imap_host=mb_imap_host, imap_port=993,
                        ))
                        dh = s.query(DomainHealth).filter_by(domain=domain).first()
                        if not dh:
                            s.add(DomainHealth(domain=domain))
                        st.success(f"✅ Mailbox **{mb_email}** added")
                st.cache_data.clear()
                st.rerun()

    st.divider()

    # ── Existing Mailboxes ──
    st.subheader("📬 Configured Mailboxes")
    with get_session() as s:
        mailboxes = s.query(Mailbox).order_by(Mailbox.domain, Mailbox.email).all()

    if mailboxes:
        for mb in mailboxes:
            status_icon = "🟢" if mb.is_active and mb.status != "PAUSED" else "🔴"
            with st.expander(f"{status_icon} {mb.email} — {mb.warmup_status}"):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Provider:** {mb.provider}")
                col1.write(f"**Domain:** {mb.domain}")
                col2.write(f"**Warmup:** {mb.warmup_status}")
                col2.write(f"**Status:** {mb.status}")
                col3.write(f"**Rep:** {mb.reputation_score or 50}/100")
                col3.write(f"**Total Sent:** {mb.total_sent or 0}")

                act1, act2, act3 = st.columns(3)

                # ── Pause / Resume ──
                if mb.status != "PAUSED":
                    if act1.button("⏸️ Pause", key=f"pause_{mb.id}"):
                        with get_session() as s:
                            obj = s.query(Mailbox).filter_by(id=mb.id).first()
                            # Preserve warmup state so resume can restore it
                            obj.status = "PAUSED"
                            # Only flip warmup to PAUSED if it's in an active sending phase
                            if obj.warmup_status not in ("WARMUP_PENDING", "PAUSED"):
                                obj.warmup_status = "PAUSED"
                        st.cache_data.clear()
                        st.rerun()
                else:
                    if act1.button("▶️ Resume", key=f"resume_{mb.id}"):
                        with get_session() as s:
                            obj = s.query(Mailbox).filter_by(id=mb.id).first()
                            obj.status = "ACTIVE"
                            # Restore warmup to RECOVERY so it ramps back up gracefully
                            if obj.warmup_status == "PAUSED":
                                obj.warmup_status = "RECOVERY"
                        st.cache_data.clear()
                        st.rerun()

                # ── Activate Warmup (only when still WARMUP_PENDING) ──
                if mb.warmup_status == "WARMUP_PENDING" and mb.status != "PAUSED":
                    if act2.button("🔥 Activate Warmup", key=f"activate_{mb.id}",
                                   help="Start the warmup process — mailbox will begin sending at low volume"):
                        with get_session() as s:
                            from policy_engine import apply_warmup_transition
                            obj = s.query(Mailbox).filter_by(id=mb.id).first()
                            apply_warmup_transition(s, obj, force_state="WARMUP_ACTIVE")
                        st.cache_data.clear()
                        st.success(f"🔥 Warmup started for {mb.email}")
                        st.rerun()

                # ── Delete ──
                if act3.button("🗑️ Delete", key=f"delete_{mb.id}",
                               help="Permanently remove this mailbox"):
                    with get_session() as s:
                        obj = s.query(Mailbox).filter_by(id=mb.id).first()
                        if obj:
                            s.delete(obj)
                    st.cache_data.clear()
                    st.rerun()
    else:
        st.info("No mailboxes configured yet.")


# ══════════════════════════════════════════════════════
# 14. SETTINGS
# ══════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.title("Settings")

    tab1, tab2, tab3, tab4 = st.tabs(["🧠 LLM Provider", "🎯 Scoring", "🔗 Sources", "⏰ Schedule"])

    with tab1:
        from openrouter_client import get_provider_info
        info = get_provider_info()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Provider", info["provider"].upper())
        c2.metric("Model", info["model"])
        c3.metric("API Key", "✅ Set" if info["has_api_key"] else "❌ Not set")
        c4.metric("Temperature", info["temperature"])

        st.markdown(
            f'<div class="detail-card">'
            f'<div style="font-weight:700;color:#e2e8f0;margin-bottom:8px;">🔌 Active Configuration</div>'
            f'<div style="color:#a0aec0;font-size:0.85rem;">'
            f'<b>Base URL:</b> <code>{info["base_url"]}</code><br>'
            f'<b>Primary Model:</b> <code>{info["model"]}</code><br>'
            f'<b>Fast Model:</b> <code>{info["fast_model"]}</code><br>'
            f'<b>Timeout:</b> {info["timeout"]}s'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        st.divider()
        st.subheader("📋 Supported Providers")
        st.caption("Change provider in `.env` → set `LLM_PROVIDER` and `LLM_API_KEY`. Base URL and model auto-configure.")
        st.dataframe(pd.DataFrame([
            {"Provider": "ollama",     "Default Model": "qwen3:14b",                   "API Key": "Not needed",    "Base URL": "http://localhost:11434/v1",       "Notes": "Local, free, self-hosted"},
            {"Provider": "groq",       "Default Model": "llama-3.3-70b-versatile",     "API Key": "gsk_...",       "Base URL": "https://api.groq.com/openai/v1", "Notes": "Fast inference, free tier"},
            {"Provider": "openrouter", "Default Model": "meta-llama/llama-3.3-70b-instruct", "API Key": "sk-or-v1-...", "Base URL": "https://openrouter.ai/api/v1", "Notes": "100+ models, pay-per-use"},
            {"Provider": "openai",     "Default Model": "gpt-4o-mini",                 "API Key": "sk-...",        "Base URL": "https://api.openai.com/v1",      "Notes": "GPT-4o, GPT-4o-mini"},
            {"Provider": "deepseek",   "Default Model": "deepseek-chat",               "API Key": "sk-...",        "Base URL": "https://api.deepseek.com/v1",    "Notes": "Cheap, good for JSON"},
            {"Provider": "custom",     "Default Model": "(set manually)",               "API Key": "(varies)",      "Base URL": "(set LLM_BASE_URL)",             "Notes": "Any OpenAI-compatible API"},
        ]), use_container_width=True, hide_index=True)

    with tab2:
        from config import ICP_SCORING_RULES, ICP_SCORE_THRESHOLD
        st.write(f"**Default Threshold:** `{ICP_SCORE_THRESHOLD}` (overridden per campaign)")
        rules_df = pd.DataFrame({"Rule": list(ICP_SCORING_RULES.keys()),
                                 "Points": list(ICP_SCORING_RULES.values())})
        st.dataframe(rules_df, use_container_width=True, hide_index=True)
        st.caption("These are fallback rules. Campaign-specific rules take priority.")

    with tab3:
        st.subheader("Universal Data Sources")
        st.dataframe(pd.DataFrame([
            {"Source": "Google Maps",  "Provider": "Apify Actor",   "Best For": "Local/physical businesses",    "Status": "✅ Active"},
            {"Source": "Apollo.io",    "Provider": "Apollo API",    "Best For": "B2B companies + contacts",     "Status": "✅ Active"},
            {"Source": "SearXNG",      "Provider": "Self-hosted",   "Best For": "General web search (catch-all)","Status": "✅ Active"},
            {"Source": "LinkedIn",     "Provider": "Apify Actor",   "Best For": "Companies with job postings",  "Status": "✅ Active"},
            {"Source": "Crunchbase",   "Provider": "SearXNG search","Best For": "Funded startups",              "Status": "🔧 Planned"},
        ]), use_container_width=True, hide_index=True)

    with tab4:
        st.dataframe(pd.DataFrame([
            {"Module":"watcher",      "Interval":"On demand",    "Description":"Multi-source signal collection per campaign"},
            {"Module":"scorer",       "Interval":"Every 2 hours","Description":"Dynamic ICP scoring from campaign rules"},
            {"Module":"finder",       "Interval":"Every 4 hours","Description":"4-strategy contact discovery"},
            {"Module":"verifier",     "Interval":"Every 4 hours","Description":"Prospeo email verification"},
            {"Module":"research",     "Interval":"Every 4 hours","Description":"Campaign-aware LLM research"},
            {"Module":"email_writer", "Interval":"Every 4 hours","Description":"Pitch-aligned email generation"},
            {"Module":"sender",       "Interval":"Every 1 hour", "Description":"SMTP/Gmail API sending"},
            {"Module":"reply_checker","Interval":"Every 30 min", "Description":"IMAP inbox monitoring"},
        ]), use_container_width=True, hide_index=True)

    st.divider()
    if st.button("🔄 Refresh Cache"):
        st.cache_data.clear()
        st.rerun()

