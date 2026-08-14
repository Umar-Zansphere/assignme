"""
dashboard.py — Sales Machine Full Pipeline Dashboard

Shows detailed data from every pipeline module:
  watcher → scorer → finder → verifier
  → research → email_writer → sender → reply_checker

Features:
  - Detailed data views for each stage
  - Editable email content before sending
  - Email source badges (APIFY_VERIFIED / GUESSED)
  - Full pipeline funnel visualization

Usage:
    python -m streamlit run dashboard.py
"""

import json
import streamlit as st
import pandas as pd
from sqlalchemy import func, text

from database import get_session, init_db
from models import Company, Signal, Contact, Research, Email, ReplyLog


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

/* Metric cards */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    padding: 1rem 1.2rem;
    border-radius: 0.75rem;
    border: 1px solid rgba(255,255,255,0.08);
    transition: transform 0.2s, box-shadow 0.2s;
}
[data-testid="stMetric"]:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
[data-testid="stMetricLabel"] { color: #a0aec0 !important; font-size: 0.8rem !important; }
[data-testid="stMetricValue"] { color: #e2e8f0 !important; font-size: 1.7rem !important; font-weight: 700 !important; }

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

/* Status pill colours — injected via markdown */
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
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 16px;
    margin: 8px 0;
}

/* Email source badges */
.badge-apify { background:#1c4532; color:#68d391; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; }
.badge-guessed { background:#744210; color:#f6e05e; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; }
.badge-invalid { background:#742a2a; color:#fc8181; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; }

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


# ── Data Loaders ───────────────────────────────────────────
@st.cache_data(ttl=20)
def load_stats():
    init_db()
    with get_session() as s:
        stats = {
            "total_signals":     s.query(Signal).count(),
            "total_companies":   s.query(Company).count(),
            "qualified":         s.query(Company).filter(Company.status.in_(
                                   ["QUALIFIED","CONTACT_FOUND","EMAIL_VERIFIED",
                                    "RESEARCH_DONE","EMAIL_READY","EMAIL_SENT","REPLIED"])).count(),
            "rejected":          s.query(Company).filter_by(status="REJECTED").count(),
            "contacts_found":    s.query(Contact).count(),
            "contacts_verified": s.query(Contact).filter(Contact.verified.in_(["VALID", "APIFY_VERIFIED"])).count(),
            "contacts_guessed":  s.query(Contact).filter_by(verified="GUESSED").count(),
            "research_done":     s.query(Research).count(),
            "emails_sent":       s.query(Email).filter_by(status="SENT").count(),
            "emails_scheduled":  s.query(Email).filter_by(status="SCHEDULED").count(),
            "emails_draft":      s.query(Email).filter_by(status="DRAFT").count(),
            "replies":           s.query(ReplyLog).count(),
            "new_signal":        s.query(Company).filter_by(status="NEW_SIGNAL").count(),
            "linkedin_signals":  s.query(Signal).filter_by(source="linkedin").count(),
        }
        # reply rate
        c_emailed = s.query(func.count(func.distinct(Email.company_id))).filter_by(status="SENT").scalar() or 0
        c_replied = s.query(func.count(func.distinct(ReplyLog.company_id))).scalar() or 0
        stats["reply_rate"] = round(c_replied / c_emailed * 100, 1) if c_emailed else 0

        # signal sources & types
        stats["signal_sources"] = dict(s.execute(
            text("SELECT source, COUNT(*) FROM signals GROUP BY source ORDER BY COUNT(*) DESC")
        ).fetchall())
        stats["signal_types"] = dict(s.execute(
            text("SELECT signal_type, COUNT(*) FROM signals GROUP BY signal_type ORDER BY COUNT(*) DESC")
        ).fetchall())

        # pipeline funnel
        stats["pipeline"] = {}
        for _, status, _ in PIPELINE_STAGES:
            stats["pipeline"][status] = s.query(Company).filter_by(status=status).count()
        # also count rejected separately
        stats["pipeline"]["REJECTED"] = stats["rejected"]

    return stats


@st.cache_data(ttl=20)
def load_signals():
    init_db()
    with get_session() as s:
        rows = (s.query(Signal, Company.name.label("co"), Company.status.label("co_status"))
                .join(Company, Signal.company_id == Company.id)
                .order_by(Signal.detected_at.desc()).limit(500).all())
        return pd.DataFrame([{
            "Company":  r.co,
            "Status":   r.co_status,
            "Type":     r.Signal.signal_type,
            "Source":   r.Signal.source or "",
            "Title":    r.Signal.title or "",
            "Description": (r.Signal.description or "")[:200],
            "URL":      r.Signal.raw_url or "",
            "Detected": r.Signal.detected_at,
        } for r in rows])


@st.cache_data(ttl=20)
def load_companies():
    init_db()
    with get_session() as s:
        cos = s.query(Company).order_by(Company.updated_at.desc()).all()
        return pd.DataFrame([{
            "ID":        c.id,
            "Name":      c.name,
            "Industry":  c.industry or "",
            "Country":   c.country or "",
            "Employees": c.employee_count or 0,
            "ICP Score": c.icp_score or 0,
            "Status":    c.status,
            "Website":   c.website or "",
            "LinkedIn":  c.linkedin_url or "",
            "GitHub":    c.github_url or "",
            "Created":   c.created_at,
            "Updated":   c.updated_at,
        } for c in cos])


@st.cache_data(ttl=20)
def load_contacts():
    init_db()
    with get_session() as s:
        rows = (s.query(Contact, Company.name.label("co"), Company.status.label("co_status"))
                .join(Company, Contact.company_id == Company.id)
                .order_by(Contact.created_at.desc()).all())
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
def load_research():
    init_db()
    with get_session() as s:
        rows = (s.query(Research, Company.name.label("co"), Company.industry, Company.country)
                .join(Company, Research.company_id == Company.id)
                .order_by(Research.created_at.desc()).all())
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

            # Parse raw_json for talking_points and urgency
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
def load_emails():
    init_db()
    with get_session() as s:
        rows = (s.query(Email, Contact.email.label("to_email"), Contact.name.label("contact_name"),
                        Company.name.label("co"))
                .join(Contact, Email.contact_id == Contact.id)
                .join(Company, Email.company_id == Company.id)
                .order_by(Email.sent_at.desc().nullslast()).limit(200).all())
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
    if source in ("APIFY_VERIFIED",):
        return '<span class="badge-apify">✅ APIFY VERIFIED</span>'
    elif source in ("GUESSED", "PATTERN_DERIVED"):
        return '<span class="badge-guessed">⚠️ GUESSED</span>'
    elif source == "APIFY_UNVERIFIED":
        return '<span class="badge-guessed">🔍 APIFY (UNVERIFIED)</span>'
    else:
        return '<span class="badge-invalid">❓ UNKNOWN</span>'


def module_header(icon: str, name: str, description: str, color: str = "#667eea"):
    st.markdown(
        f'<div class="module-header" style="border-left-color:{color};">'
        f'<span style="font-size:1.1rem;font-weight:700;color:{color};">{icon} {name}</span>'
        f'<span style="color:#718096;font-size:0.8rem;margin-left:12px;">{description}</span>'
        f'</div>',
        unsafe_allow_html=True
    )


def source_pills(df: pd.DataFrame):
    if df.empty or "Source" not in df.columns:
        return
    counts = df["Source"].value_counts()
    pills = " ".join(
        f'<span style="background:{SOURCE_COLOR.get(s,"#718096")}33;color:{SOURCE_COLOR.get(s,"#a0aec0")};'
        f'padding:2px 10px;border-radius:12px;font-size:0.72rem;font-weight:600;">{s}: {c}</span>'
        for s, c in counts.items()
    )
    st.markdown(pills, unsafe_allow_html=True)
    st.write("")


# ── Sidebar ────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚀 Sales Machine")
    st.markdown("*QA Leads Pipeline (USA)*")
    st.markdown("---")
    page = st.radio("Navigation", [
        "📊 Pipeline Monitor",
        "📡 Watcher — Signals",
        "🏢 Companies",
        "🎯 Scorer — Qualification",
        "🔍 Finder & ✅ Verifier — Contacts",
        "📚 Research — Intelligence",
        "✍️ Email Writer — Drafts",
        "📤 Sender — Sent Emails",
        "💬 Reply Checker — Replies",
        "⚙️ Settings",
    ], label_visibility="collapsed")
    st.markdown("---")
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Pipeline Status Flow")
    for label, status, color in PIPELINE_STAGES:
        st.markdown(
            f'<span style="color:{color};font-size:0.78rem;">● {label}</span>',
            unsafe_allow_html=True
        )


# ── Pages ──────────────────────────────────────────────────

# ══════════════════════════════════════════════════════
# 1. PIPELINE MONITOR
# ══════════════════════════════════════════════════════
if page == "📊 Pipeline Monitor":
    st.title("Pipeline Monitor")
    st.caption("Real-time view of every module — QA Leads Pipeline (LinkedIn, USA)")

    stats = load_stats()

    # ── Top KPIs ──
    c1,c2,c3,c4,c5,c6 = st.columns(6)
    c1.metric("📡 Signals",     stats["total_signals"])
    c2.metric("🏢 Companies",   stats["total_companies"])
    c3.metric("✅ Qualified",    stats["qualified"])
    c4.metric("👤 Contacts",    stats["contacts_found"])
    c5.metric("📧 Emails Sent", stats["emails_sent"])
    c6.metric("💬 Replies",     stats["replies"])

    st.divider()

    # ── Pipeline flow diagram (stages + counts) ──
    st.subheader("🔽 Stage-by-Stage Funnel")

    stage_cols = st.columns(len(PIPELINE_STAGES))
    for i, (label, status, color) in enumerate(PIPELINE_STAGES):
        cnt = stats["pipeline"].get(status, 0)
        with stage_cols[i]:
            st.markdown(
                f'<div style="text-align:center;background:{color}18;border:1px solid {color}55;'
                f'border-radius:10px;padding:12px 6px;">'
                f'<div style="font-size:1.5rem;font-weight:800;color:{color};">{cnt}</div>'
                f'<div style="font-size:0.65rem;color:#a0aec0;margin-top:4px;line-height:1.3;">'
                f'{label}</div></div>',
                unsafe_allow_html=True
            )

    # Rejected separately
    rej = stats["pipeline"].get("REJECTED", 0)
    st.markdown(
        f'<div style="margin-top:8px;padding:6px 14px;background:#742a2a22;border:1px solid #fc818155;'
        f'border-radius:8px;display:inline-block;">'
        f'<span style="color:#fc8181;font-weight:700;">{rej}</span>'
        f'<span style="color:#a0aec0;font-size:0.8rem;margin-left:8px;">❌ Rejected by Scorer</span></div>',
        unsafe_allow_html=True
    )

    st.divider()

    # ── Module cards (2-per-row) ──
    st.subheader("📋 Module Data Summary")

    r1c1, r1c2 = st.columns(2)

    with r1c1:
        module_header("📡", "Watcher", "LinkedIn QA signals (USA)", "#f59e0b")
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Total Signals",  stats["total_signals"])
        mc2.metric("LinkedIn",       stats["linkedin_signals"])
        mc3.metric("Companies",      stats["total_companies"])

    with r1c2:
        module_header("🏢", "Companies", "Enriched directly from LinkedIn data", "#4299e1")
        enriched = stats["total_companies"] - stats["new_signal"] - stats["rejected"]
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Enriched",    enriched)
        mc2.metric("Pending",     stats["new_signal"])
        mc3.metric("Rejected",    stats["rejected"])

    st.divider()
    r2c1, r2c2 = st.columns(2)

    with r2c1:
        module_header("🎯", "Scorer", "ICP rule-based scoring & qualification", "#48bb78")
        mc1,mc2,mc3 = st.columns(3)
        total_scored = stats["qualified"] + stats["rejected"]
        mc1.metric("Qualified", stats["qualified"])
        mc2.metric("Rejected",  stats["rejected"])
        mc3.metric("Pass Rate", f"{round(stats['qualified']/total_scored*100)}%" if total_scored else "—")

    with r2c2:
        module_header("🔍", "Finder + ✅ Verifier", "Decision maker emails (Apify + SMTP)", "#9f7aea")
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Contacts Found",    stats["contacts_found"])
        mc2.metric("✅ Verified",       stats["contacts_verified"])
        mc3.metric("⚠️ Guessed",       stats["contacts_guessed"])

    st.divider()
    r3c1, r3c2 = st.columns(2)

    with r3c1:
        module_header("📚", "Research", "Pain points, tech stack, personalization", "#63b3ed")
        mc1,mc2 = st.columns(2)
        mc1.metric("Researched", stats["research_done"])
        mc2.metric("Awaiting",   max(0, stats["qualified"] - stats["research_done"]))

    with r3c2:
        module_header("✍️", "Email Writer + 📤 Sender", "Multi-touch email sequences", "#ecc94b")
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Scheduled", stats["emails_scheduled"])
        mc2.metric("Sent",      stats["emails_sent"])
        mc3.metric("Reply Rate", f"{stats['reply_rate']}%")


# ══════════════════════════════════════════════════════
# 2. WATCHER — SIGNALS (Detailed)
# ══════════════════════════════════════════════════════
elif page == "📡 Watcher — Signals":
    module_header("📡", "Watcher", "Stage 1 — LinkedIn QA job signals (USA)", "#f59e0b")
    st.caption("Data: `signals` table  |  Source: LinkedIn  |  Focus: QA roles, USA")

    stats = load_stats()
    c1,c2,c3 = st.columns(3)
    c1.metric("Total Signals",  stats["total_signals"])
    c2.metric("LinkedIn",       stats["linkedin_signals"])
    c3.metric("Companies Found",stats["total_companies"])

    st.divider()
    df = load_signals()
    if df.empty:
        st.info("No signals yet. Run `python watcher.py`")
    else:
        col1, col2 = st.columns(2)
        with col1:
            src_opts = sorted(df["Source"].unique().tolist())
            src_sel = st.multiselect("Filter by Source", src_opts, default=src_opts)
        with col2:
            type_opts = sorted(df["Type"].unique().tolist())
            type_sel = st.multiselect("Filter by Type", type_opts, default=type_opts)

        filtered = df[df["Source"].isin(src_sel) & df["Type"].isin(type_sel)]
        source_pills(filtered)

        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(filtered)} / {len(df)} signals")

        # ── Detailed Signal Cards ──
        st.divider()
        st.subheader("📋 Signal Details")

        for source_name in src_sel:
            source_df = filtered[filtered["Source"] == source_name]
            if source_df.empty:
                continue

            color = SOURCE_COLOR.get(source_name, "#718096")
            st.markdown(
                f'<div style="background:{color}18;border:1px solid {color}55;border-radius:8px;'
                f'padding:8px 14px;margin:8px 0;">'
                f'<span style="color:{color};font-weight:700;font-size:1rem;">'
                f'🔵 {source_name.title()} ({len(source_df)} signals)</span></div>',
                unsafe_allow_html=True
            )

            with st.expander(f"View all {source_name.title()} listings", expanded=False):
                for _, row in source_df.iterrows():
                    st.markdown(f"**{row['Company']}** — {row['Title']}")
                    if row.get("Description"):
                        st.caption(row["Description"][:150])
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        if row["URL"]:
                            st.markdown(f"🔗 [{row['URL'][:60]}...]({row['URL']})")
                    with col_b:
                        st.caption(f"Status: {row['Status']}")
                    st.markdown("---")


# ══════════════════════════════════════════════════════
# 3. COMPANIES (Enriched from LinkedIn)
# ══════════════════════════════════════════════════════
elif page == "🏢 Companies":
    module_header("🏢", "Companies", "Enriched directly from LinkedIn data — no separate enrichment needed", "#4299e1")
    st.caption("Data: `companies` table  |  All enrichment data (website, industry, employees, LinkedIn URL) comes from LinkedIn job scraper")

    df = load_companies()
    if df.empty:
        st.info("No companies yet. Run the watcher first.")
    else:
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Companies", len(df))
        c2.metric("Industries",      df["Industry"].nunique())
        c3.metric("Countries",       df["Country"].nunique())
        c4.metric("Avg ICP Score",   round(df["ICP Score"].mean(), 1))

        st.divider()

        col1, col2, col3 = st.columns(3)
        with col1:
            industries = sorted(df["Industry"].unique().tolist())
            ind_sel = st.multiselect("Industry", industries)
        with col2:
            countries = sorted(df["Country"].unique().tolist())
            co_sel = st.multiselect("Country", countries)
        with col3:
            status_opts = sorted(df["Status"].unique().tolist())
            st_sel = st.multiselect("Status", status_opts, default=status_opts)

        filtered = df[df["Status"].isin(st_sel)]
        if ind_sel:
            filtered = filtered[filtered["Industry"].isin(ind_sel)]
        if co_sel:
            filtered = filtered[filtered["Country"].isin(co_sel)]

        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.caption(f"{len(filtered)} companies shown")

        # ── Detailed Company Cards ──
        st.divider()
        st.subheader("🏢 Company Details")

        company_sel = st.selectbox("Select a company to view details", filtered["Name"].tolist() if not filtered.empty else [])
        if company_sel:
            row = filtered[filtered["Name"] == company_sel].iloc[0]
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Company:** {row['Name']}")
                st.markdown(f"**Industry:** {row['Industry'] or '—'}")
                st.markdown(f"**Country:** {row['Country'] or '—'}")
                st.markdown(f"**Employees:** {row['Employees'] or '—'}")
            with col2:
                st.markdown(f"**Status:** {status_pill(row['Status'])}", unsafe_allow_html=True)
                st.markdown(f"**ICP Score:** {row['ICP Score']}")
                if row["Website"]:
                    st.markdown(f"**Website:** [{row['Website']}]({row['Website']})")
                if row["LinkedIn"]:
                    st.markdown(f"**LinkedIn:** [{row['LinkedIn']}]({row['LinkedIn']})")
                if row["GitHub"]:
                    st.markdown(f"**GitHub:** [{row['GitHub']}]({row['GitHub']})")

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Top Industries")
            ind_counts = df[df["Industry"] != ""]["Industry"].value_counts().head(8).reset_index()
            if not ind_counts.empty:
                ind_counts.columns = ["Industry", "Count"]
                st.bar_chart(ind_counts.set_index("Industry"))
        with col2:
            st.subheader("Top Countries")
            co_counts = df[df["Country"] != ""]["Country"].value_counts().head(8).reset_index()
            if not co_counts.empty:
                co_counts.columns = ["Country", "Count"]
                st.bar_chart(co_counts.set_index("Country"))


# ══════════════════════════════════════════════════════
# 4. SCORER — QUALIFICATION (Detailed)
# ══════════════════════════════════════════════════════
elif page == "🎯 Scorer — Qualification":
    module_header("🎯", "Scorer", "Stage 3 — ICP rule-based scoring, qualifies or rejects companies", "#48bb78")
    st.caption("Data: `companies` table  |  Fields: icp_score, status (QUALIFIED / REJECTED)")

    df = load_companies()
    scored = df[df["Status"].isin(["QUALIFIED","REJECTED","CONTACT_FOUND","EMAIL_VERIFIED",
                                   "RESEARCH_DONE","EMAIL_READY","EMAIL_SENT","REPLIED"])]

    if scored.empty:
        st.info("No scored companies yet. Run `python scorer.py`")
    else:
        qualified = scored[scored["Status"] != "REJECTED"]
        rejected  = scored[scored["Status"] == "REJECTED"]

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Scored",    len(scored))
        c2.metric("✅ Qualified", len(qualified))
        c3.metric("❌ Rejected",  len(rejected))
        c4.metric("Pass Rate", f"{round(len(qualified)/len(scored)*100)}%")

        st.divider()

        tab_q, tab_r = st.tabs([f"✅ Qualified ({len(qualified)})", f"❌ Rejected ({len(rejected)})"])

        with tab_q:
            st.dataframe(
                qualified[["Name","Industry","Country","Employees","ICP Score","Status","Website"]]
                .sort_values("ICP Score", ascending=False),
                use_container_width=True, hide_index=True
            )

            # Detailed view for qualified companies
            if not qualified.empty:
                st.divider()
                st.subheader("📋 Qualified Company Details")
                q_sel = st.selectbox("Select company", qualified["Name"].tolist(), key="q_detail")
                if q_sel:
                    row = qualified[qualified["Name"] == q_sel].iloc[0]
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown(f"**Industry:** {row['Industry']}")
                        st.markdown(f"**Country:** {row['Country']}")
                        st.markdown(f"**Employees:** {row['Employees']}")
                    with col2:
                        st.markdown(f"**ICP Score:** {row['ICP Score']}")
                        st.markdown(f"**Website:** {row['Website'] or '—'}")
                        st.markdown(f"**Current Status:** {status_pill(row['Status'])}", unsafe_allow_html=True)

        with tab_r:
            st.dataframe(
                rejected[["Name","Industry","Country","Employees","ICP Score"]]
                .sort_values("ICP Score", ascending=False),
                use_container_width=True, hide_index=True
            )

        st.divider()
        st.subheader("ICP Score Distribution")
        score_df = scored[["Name","ICP Score","Status"]].sort_values("ICP Score")
        st.bar_chart(score_df.set_index("Name")["ICP Score"])

        from config import ICP_SCORING_RULES, ICP_SCORE_THRESHOLD
        st.subheader(f"Scoring Rules (threshold = {ICP_SCORE_THRESHOLD})")
        rules_df = pd.DataFrame({"Rule": list(ICP_SCORING_RULES.keys()),
                                 "Points": list(ICP_SCORING_RULES.values())})
        st.dataframe(rules_df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════
# 5. FINDER + VERIFIER — CONTACTS (Detailed)
# ══════════════════════════════════════════════════════
elif page == "🔍 Finder & ✅ Verifier — Contacts":
    module_header("🔍", "Finder + Verifier", "Stages 4 & 5 — Finds decision makers, verifies via Apify or SMTP", "#9f7aea")
    st.caption("Data: `contacts` table  |  Email sources: APIFY_VERIFIED, GUESSED, PATTERN_DERIVED")

    df = load_contacts()
    if df.empty:
        st.info("No contacts yet. Run `python finder.py` then `python verifier.py`")
    else:
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Contacts",  len(df))
        c2.metric("✅ Verified",     len(df[df["Verified"].isin(["VALID", "APIFY_VERIFIED"])]))
        c3.metric("⚠️ Guessed",     len(df[df["Verified"]=="GUESSED"]))
        c4.metric("❌ Invalid",      len(df[df["Verified"]=="INVALID"]))

        st.divider()

        # Email source breakdown
        st.subheader("📧 Email Source Breakdown")
        if "Email Source" in df.columns:
            source_counts = df["Email Source"].value_counts().reset_index()
            source_counts.columns = ["Source", "Count"]
            for _, row in source_counts.iterrows():
                st.markdown(f"{email_source_badge(row['Source'])} — **{row['Count']}** contacts", unsafe_allow_html=True)
        st.write("")

        st.divider()
        ver_sel = st.multiselect("Filter by Verification Status",
                                 df["Verified"].unique().tolist(),
                                 default=df["Verified"].unique().tolist())
        filtered = df[df["Verified"].isin(ver_sel)]

        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.caption(f"{len(filtered)} contacts shown")

        # ── Detailed Contact Cards ──
        st.divider()
        st.subheader("👤 Contact Details")
        for _, row in filtered.iterrows():
            with st.expander(f"**{row['Name']}** — {row['Role']} at {row['Company']}"):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**Name:** {row['Name']}")
                    st.markdown(f"**Role:** {row['Role']}")
                    st.markdown(f"**Email:** `{row['Email']}`")
                    st.markdown(f"**Email Source:** {email_source_badge(row['Email Source'])}", unsafe_allow_html=True)
                with col2:
                    st.markdown(f"**Company:** {row['Company']}")
                    st.markdown(f"**Company Status:** {status_pill(row['Co. Status'])}", unsafe_allow_html=True)
                    st.markdown(f"**Verified:** {row['Verified']}")
                    if row["LinkedIn"]:
                        st.markdown(f"**LinkedIn:** [{row['LinkedIn']}]({row['LinkedIn']})")

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Contact Roles")
            role_counts = df["Role"].value_counts().head(8).reset_index()
            role_counts.columns = ["Role", "Count"]
            st.bar_chart(role_counts.set_index("Role"))
        with col2:
            st.subheader("Verification Breakdown")
            ver_counts = df["Verified"].value_counts().reset_index()
            ver_counts.columns = ["Status", "Count"]
            st.dataframe(ver_counts, hide_index=True, use_container_width=True)


# ══════════════════════════════════════════════════════
# 6. RESEARCH — INTELLIGENCE (Detailed)
# ══════════════════════════════════════════════════════
elif page == "📚 Research — Intelligence":
    module_header("📚", "Research", "Stage 6 — Deep LLM research: summaries, pain points, tech stack, talking points", "#63b3ed")
    st.caption("Data: `research` table  |  Fields: summary, pain_points, tech_stack, recent_news, talking_points, urgency")

    df = load_research()
    if df.empty:
        st.info("No research done yet. Run `python research.py`")
    else:
        c1,c2,c3 = st.columns(3)
        c1.metric("Companies Researched", len(df))
        c2.metric("With Tech Stack",      len(df[df["Tech Stack"] != ""]))
        c3.metric("High Urgency",         len(df[df["Urgency"] == "high"]) if "Urgency" in df.columns else 0)

        st.divider()
        st.dataframe(df[["Company","Industry","Country","Summary","Pain Points","Tech Stack","Urgency"]],
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("🔍 Deep Dive per Company")
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
            st.markdown("**📰 Recent News**")
            st.write(row["Recent News"] or "—")
            if row.get("Talking Points"):
                st.markdown("**💬 Talking Points**")
                for tp in row["Talking Points"].split(", "):
                    st.markdown(f"  • {tp}")
            if row.get("Urgency"):
                urgency_color = {"high": "#fc8181", "medium": "#f6e05e", "low": "#68d391"}.get(row["Urgency"], "#a0aec0")
                st.markdown(f'**Urgency:** <span style="color:{urgency_color};font-weight:700;">{row["Urgency"].upper()}</span>',
                           unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
# 7. EMAIL WRITER — DRAFTS (Editable)
# ══════════════════════════════════════════════════════
elif page == "✍️ Email Writer — Drafts":
    module_header("✍️", "Email Writer", "Stage 7 — Generates & edits personalised email sequences", "#ecc94b")
    st.caption("Data: `emails` table  |  ✏️ Edit subject/body before sending  |  Changes saved to DB")

    df = load_emails()
    if df.empty:
        st.info("No emails written yet. Run `python email_writer.py`")
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
            # Group emails by company for easier browsing
            company_list = filtered["Company"].unique().tolist()
            sel_company = st.selectbox("Select company", company_list, key="email_company")
            company_emails = filtered[filtered["Company"] == sel_company]

            for _, row in company_emails.iterrows():
                seq_label = {0: "Initial Email", 1: "Follow-up 1", 2: "Follow-up 2"}.get(row["Seq"], f"Email #{row['Seq']}")
                status_icon = {"SCHEDULED": "📅", "SENT": "✅", "DRAFT": "📝", "FAILED": "❌", "CANCELLED": "🚫"}.get(row["Status"], "📧")

                with st.expander(f"{status_icon} {seq_label} — {row['Subject']} [{row['Status']}]", expanded=(row["Seq"] == 0)):
                    st.markdown(f"**To:** {row['To']}  |  **Status:** {status_pill(row['Status'])}", unsafe_allow_html=True)

                    # Editable fields — only for SCHEDULED or DRAFT emails
                    can_edit = row["Status"] in ("SCHEDULED", "DRAFT")

                    new_subject = st.text_input(
                        "Subject",
                        value=row["Subject"],
                        key=f"subj_{row['ID']}",
                        disabled=not can_edit
                    )

                    new_body = st.text_area(
                        "Body",
                        value=row["Body"],
                        height=200,
                        key=f"body_{row['ID']}",
                        disabled=not can_edit
                    )

                    if can_edit:
                        col_save, col_cancel = st.columns([1, 3])
                        with col_save:
                            if st.button("💾 Save Changes", key=f"save_{row['ID']}"):
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
                                if st.button("🚫 Cancel Email", key=f"cancel_{row['ID']}"):
                                    try:
                                        with get_session() as session:
                                            email_record = session.query(Email).filter_by(id=row["ID"]).first()
                                            if email_record:
                                                email_record.status = "CANCELLED"
                                        st.success("Email cancelled!")
                                        st.cache_data.clear()
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Cancel failed: {e}")
                    elif row["Status"] == "SENT":
                        st.caption(f"📤 Sent at: {row['Sent']}")


# ══════════════════════════════════════════════════════
# 8. SENDER — SENT EMAILS (Detailed)
# ══════════════════════════════════════════════════════
elif page == "📤 Sender — Sent Emails":
    module_header("📤", "Sender", "Stage 8 — Delivers scheduled emails via SMTP", "#667eea")
    st.caption("Data: `emails` table  |  Status: SENT, sent_at, message_id  |  SMTP SSL (port 465) supported")

    df = load_emails()
    sent = df[df["Status"]=="SENT"] if not df.empty else pd.DataFrame()
    scheduled = df[df["Status"]=="SCHEDULED"] if not df.empty else pd.DataFrame()

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Sent",       len(sent))
    c2.metric("Scheduled",  len(scheduled))
    c3.metric("Failed",     len(df[df["Status"]=="FAILED"]) if not df.empty else 0)
    stats = load_stats()
    c4.metric("Reply Rate", f"{stats['reply_rate']}%")

    st.divider()

    tab1, tab2, tab3 = st.tabs(["📤 Sent", "📅 Scheduled", "❌ Failed"])
    with tab1:
        if sent.empty:
            st.info("No emails sent yet. Run `python sender.py`")
        else:
            st.dataframe(sent[["Company","Contact","To","Subject","Seq","Sent"]],
                         use_container_width=True, hide_index=True)
            # Detailed sent email view
            st.divider()
            for _, row in sent.iterrows():
                with st.expander(f"📤 {row['Company']} — {row['Subject']}"):
                    st.markdown(f"**To:** {row['To']}  |  **Sent:** {row['Sent']}")
                    st.text_area("Body", value=row["Body"], height=150, disabled=True, key=f"sent_{row['ID']}")

    with tab2:
        if scheduled.empty:
            st.info("No emails scheduled yet.")
        else:
            st.dataframe(scheduled[["Company","Contact","To","Subject","Seq","Scheduled"]],
                         use_container_width=True, hide_index=True)

    with tab3:
        failed = df[df["Status"]=="FAILED"] if not df.empty else pd.DataFrame()
        if failed.empty:
            st.info("No failed emails.")
        else:
            st.dataframe(failed[["Company","Contact","To","Subject","Seq","Status"]],
                         use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════
# 9. REPLY CHECKER — REPLIES (Detailed)
# ══════════════════════════════════════════════════════
elif page == "💬 Reply Checker — Replies":
    module_header("💬", "Reply Checker", "Stage 9 — Monitors inbox via IMAP and logs replies", "#48bb78")
    st.caption("Data: `reply_logs` table  |  Fields: reply_from, reply_subject, reply_body, detected_at")

    stats = load_stats()
    df   = load_replies()

    c1,c2,c3 = st.columns(3)
    c1.metric("Replies Detected", stats["replies"])
    c2.metric("Emails Sent",      stats["emails_sent"])
    c3.metric("Reply Rate",       f"{stats['reply_rate']}%")

    st.divider()

    if df.empty:
        st.info("No replies detected yet. Replies appear here automatically when `reply_checker.py` runs and finds inbox responses.")
    else:
        st.dataframe(df[["Company","From","Subject","Detected"]], use_container_width=True, hide_index=True)
        st.divider()
        st.subheader("📩 Reply Details")
        for _, row in df.iterrows():
            with st.expander(f"📩 {row['Company']} — {row['Subject']}"):
                st.markdown(f"**From:** {row['From']}")
                st.markdown(f"**Detected:** {row['Detected']}")
                st.divider()
                st.write(row.get("Body", row.get("Preview", "")))


# ══════════════════════════════════════════════════════
# 10. SETTINGS
# ══════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.title("Settings")

    from config import ICP_SCORING_RULES, ICP_SCORE_THRESHOLD

    tab1, tab2, tab3 = st.tabs(["🎯 Scoring Rules", "🔗 Sources", "⏰ Schedule"])

    with tab1:
        st.write(f"**Qualification Threshold:** `{ICP_SCORE_THRESHOLD}` points")
        rules_df = pd.DataFrame({"Rule": list(ICP_SCORING_RULES.keys()),
                                 "Points": list(ICP_SCORING_RULES.values())})
        st.dataframe(rules_df, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Active Signal Sources")
        st.dataframe(pd.DataFrame([
            {"Source": "LinkedIn",  "Type": "Job Search",  "Signal": "JOB_POSTING",  "Focus": "QA roles, USA",  "Status": "✅ Active"},
        ]), use_container_width=True, hide_index=True)

        st.subheader("Email Finding")
        st.dataframe(pd.DataFrame([
            {"Method": "Apify Email Finder",   "Actor": "overpowered/email-finder",  "Priority": "1 (Primary)",    "Status": "✅ Active"},
            {"Method": "Pattern Guessing",     "Actor": "—",                         "Priority": "2 (Fallback)",   "Status": "✅ Active"},
        ]), use_container_width=True, hide_index=True)

    with tab3:
        st.dataframe(pd.DataFrame([
            {"Module":"watcher",      "Interval":"Every 6 hours", "Description":"Collect QA job signals from LinkedIn (USA) with full company enrichment"},
            {"Module":"scorer",       "Interval":"Every 2 hours", "Description":"ICP rule-based scoring and qualification"},
            {"Module":"finder",       "Interval":"Every 4 hours", "Description":"Google search + LLM + Apify email finder for contacts"},
            {"Module":"verifier",     "Interval":"Every 4 hours", "Description":"SMTP verification (skips Apify-verified emails)"},
            {"Module":"research",     "Interval":"Every 4 hours", "Description":"Deep LLM research: pain points, tech stack, hooks"},
            {"Module":"email_writer", "Interval":"Every 4 hours", "Description":"Write personalised 3-touch email sequences"},
            {"Module":"sender",       "Interval":"Every 1 hour",  "Description":"Send scheduled emails via SMTP (SSL port 465)"},
            {"Module":"reply_checker","Interval":"Every 30 min",  "Description":"Check inbox via IMAP for replies"},
        ]), use_container_width=True, hide_index=True)

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh Cache"):
            st.cache_data.clear()
            st.rerun()
    with col2:
        if st.button("🗑️ Clear Cache"):
            st.cache_data.clear()
            st.success("Cleared!")
