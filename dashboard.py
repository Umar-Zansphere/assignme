"""
dashboard.py — Sales Machine Full Pipeline Dashboard

Shows data from every pipeline module:
  watcher → enrichment → scorer → finder → verifier
  → research → email_writer → sender → reply_checker

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
    "upwork":      "#14a800",
    "linkedin":    "#0a66c2",
    "greenhouse":  "#3bba4c",
    "lever":       "#5db7de",
    "producthunt": "#da552f",
    "rss":         "#f59e0b",
}

PIPELINE_STAGES = [
    ("📡 Watcher",        "NEW_SIGNAL",    "#718096"),
    ("🔬 Enrichment",     "ENRICHED",      "#4299e1"),
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
            "contacts_verified": s.query(Contact).filter_by(verified="VALID").count(),
            "research_done":     s.query(Research).count(),
            "emails_sent":       s.query(Email).filter_by(status="SENT").count(),
            "emails_scheduled":  s.query(Email).filter_by(status="SCHEDULED").count(),
            "emails_draft":      s.query(Email).filter_by(status="DRAFT").count(),
            "replies":           s.query(ReplyLog).count(),
            "new_signal":        s.query(Company).filter_by(status="NEW_SIGNAL").count(),
            "upwork_signals":    s.query(Signal).filter_by(source="upwork").count(),
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
        rows = (s.query(Signal, Company.name.label("co"))
                .join(Company, Signal.company_id == Company.id)
                .order_by(Signal.detected_at.desc()).limit(500).all())
        return pd.DataFrame([{
            "Company":  r.co,
            "Type":     r.Signal.signal_type,
            "Source":   r.Signal.source or "",
            "Title":    r.Signal.title or "",
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
        rows = (s.query(Contact, Company.name.label("co"))
                .join(Company, Contact.company_id == Company.id)
                .order_by(Contact.created_at.desc()).all())
        return pd.DataFrame([{
            "Company":   r.co,
            "Name":      r.Contact.name or "",
            "Role":      r.Contact.role or "",
            "Email":     r.Contact.email or "",
            "LinkedIn":  r.Contact.linkedin_url or "",
            "Verified":  r.Contact.verified or "PENDING",
            "Found At":  r.Contact.created_at,
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
            data.append({
                "Company":     r.co,
                "Industry":    r.industry or "",
                "Country":     r.country or "",
                "Summary":     r.Research.summary or "",
                "Pain Points": ", ".join(pain_points) if isinstance(pain_points, list) else str(pain_points),
                "Tech Stack":  ", ".join(tech_stack) if isinstance(tech_stack, list) else str(tech_stack),
                "Recent News": r.Research.recent_news or "",
                "Created":     r.Research.created_at,
            })
        return pd.DataFrame(data)


@st.cache_data(ttl=20)
def load_emails():
    init_db()
    with get_session() as s:
        rows = (s.query(Email, Contact.email.label("to_email"), Company.name.label("co"))
                .join(Contact, Email.contact_id == Contact.id)
                .join(Company, Email.company_id == Company.id)
                .order_by(Email.sent_at.desc().nullslast()).limit(200).all())
        return pd.DataFrame([{
            "Company":   r.co,
            "To":        r.to_email,
            "Subject":   r.Email.subject or "",
            "Seq":       r.Email.sequence_number,
            "Status":    r.Email.status,
            "Body":      (r.Email.body or "")[:120] + "…" if r.Email.body and len(r.Email.body) > 120 else (r.Email.body or ""),
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
            "Detected": r.ReplyLog.detected_at,
        } for r in rows])


# ── Helpers ────────────────────────────────────────────────
def status_pill(status: str) -> str:
    color = STATUS_COLOR.get(status, "#718096")
    return f'<span style="background:{color}22;color:{color};padding:2px 10px;border-radius:12px;font-size:0.72rem;font-weight:600;">{status}</span>'


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
    st.markdown("---")
    page = st.radio("", [
        "📊 Pipeline Monitor",
        "📡 Watcher — Signals",
        "🔬 Enrichment — Companies",
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
    st.caption("Real-time view of every module and how data flows through the pipeline.")

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
        module_header("📡", "Watcher", "Collects buying signals from job boards & RSS", "#f59e0b")
        sources = stats.get("signal_sources", {})
        types   = stats.get("signal_types", {})
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Total Signals",  stats["total_signals"])
        mc2.metric("Sources Active", len(sources))
        mc3.metric("Upwork Signals", stats["upwork_signals"])
        if sources:
            src_df = pd.DataFrame({"Source": list(sources.keys()), "Count": list(sources.values())})
            st.dataframe(src_df, hide_index=True, use_container_width=True, height=120)

    with r1c2:
        module_header("🔬", "Enrichment", "Classifies company industry & country via LLM", "#4299e1")
        enriched = s_enriched = stats["total_companies"] - stats["new_signal"] - stats["rejected"]
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Enriched",    enriched)
        mc2.metric("Pending",     stats["new_signal"])
        mc3.metric("Rejected",    stats["rejected"])
        co_df = load_companies()
        if not co_df.empty:
            ind_counts = co_df["Industry"].value_counts().head(5).reset_index()
            ind_counts.columns = ["Industry", "Count"]
            st.dataframe(ind_counts, hide_index=True, use_container_width=True, height=120)

    st.divider()
    r2c1, r2c2 = st.columns(2)

    with r2c1:
        module_header("🎯", "Scorer", "ICP rule-based scoring & qualification", "#48bb78")
        mc1,mc2,mc3 = st.columns(3)
        total_scored = stats["qualified"] + stats["rejected"]
        mc1.metric("Qualified", stats["qualified"])
        mc2.metric("Rejected",  stats["rejected"])
        mc3.metric("Pass Rate", f"{round(stats['qualified']/total_scored*100)}%" if total_scored else "—")
        co_df = load_companies()
        if not co_df.empty:
            q_df = co_df[co_df["Status"].isin(["QUALIFIED","CONTACT_FOUND","EMAIL_VERIFIED",
                                                "RESEARCH_DONE","EMAIL_READY","EMAIL_SENT","REPLIED"])]
            if not q_df.empty:
                score_dist = q_df["ICP Score"].describe()[["min","mean","max"]].round(1)
                sc1,sc2,sc3 = st.columns(3)
                sc1.metric("Min Score", int(score_dist["min"]))
                sc2.metric("Avg Score", round(score_dist["mean"],1))
                sc3.metric("Max Score", int(score_dist["max"]))

    with r2c2:
        module_header("🔍", "Finder + ✅ Verifier", "Finds & verifies decision maker emails", "#9f7aea")
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Contacts Found",    stats["contacts_found"])
        mc2.metric("Emails Verified",   stats["contacts_verified"])
        invalid = stats["contacts_found"] - stats["contacts_verified"]
        mc3.metric("Invalid/Pending",   invalid)
        ct_df = load_contacts()
        if not ct_df.empty:
            role_counts = ct_df["Role"].value_counts().head(5).reset_index()
            role_counts.columns = ["Role", "Count"]
            st.dataframe(role_counts, hide_index=True, use_container_width=True, height=120)
        else:
            st.info("No contacts found yet.")

    st.divider()
    r3c1, r3c2 = st.columns(2)

    with r3c1:
        module_header("📚", "Research", "Deep LLM research: pain points, tech stack, hooks", "#63b3ed")
        mc1,mc2 = st.columns(2)
        mc1.metric("Companies Researched", stats["research_done"])
        mc2.metric("Awaiting Research",    max(0, stats["qualified"] - stats["research_done"]))
        res_df = load_research()
        if not res_df.empty:
            st.dataframe(res_df[["Company","Industry","Pain Points"]].head(4),
                         hide_index=True, use_container_width=True, height=130)
        else:
            st.info("No research done yet.")

    with r3c2:
        module_header("✍️", "Email Writer", "Personalised multi-touch email sequences", "#ecc94b")
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Drafts",     stats["emails_draft"])
        mc2.metric("Scheduled",  stats["emails_scheduled"])
        mc3.metric("Sent",       stats["emails_sent"])
        em_df = load_emails()
        if not em_df.empty:
            seq_counts = em_df["Seq"].value_counts().sort_index().reset_index()
            seq_counts.columns = ["Sequence", "Count"]
            seq_counts["Sequence"] = seq_counts["Sequence"].map(
                {0:"Initial",1:"Follow-up 1",2:"Follow-up 2"}).fillna("Other")
            st.dataframe(seq_counts, hide_index=True, use_container_width=True, height=120)
        else:
            st.info("No emails written yet.")

    st.divider()
    r4c1, r4c2 = st.columns(2)

    with r4c1:
        module_header("📤", "Sender", "Schedules and delivers emails via SMTP", "#667eea")
        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("Emails Sent",    stats["emails_sent"])
        mc2.metric("Scheduled",      stats["emails_scheduled"])
        mc3.metric("Reply Rate",     f"{stats['reply_rate']}%")
        em_df = load_emails()
        sent = em_df[em_df["Status"]=="SENT"] if not em_df.empty else pd.DataFrame()
        if not sent.empty:
            st.dataframe(sent[["Company","To","Subject","Sent"]].head(4),
                         hide_index=True, use_container_width=True, height=130)
        else:
            st.info("No emails sent yet.")

    with r4c2:
        module_header("💬", "Reply Checker", "Monitors inbox for replies via IMAP", "#48bb78")
        mc1,mc2 = st.columns(2)
        mc1.metric("Replies Detected", stats["replies"])
        mc2.metric("Reply Rate",       f"{stats['reply_rate']}%")
        rep_df = load_replies()
        if not rep_df.empty:
            st.dataframe(rep_df[["Company","From","Subject","Detected"]].head(4),
                         hide_index=True, use_container_width=True, height=130)
        else:
            st.info("No replies yet — replies will appear here automatically.")


# ══════════════════════════════════════════════════════
# 2. WATCHER — SIGNALS
# ══════════════════════════════════════════════════════
elif page == "📡 Watcher — Signals":
    module_header("📡", "Watcher", "Stage 1 — Collects buying signals from all sources", "#f59e0b")
    st.caption("Data stored in: `signals` table  |  Fields: company, signal_type, source, title, url, detected_at")

    stats = load_stats()
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Total Signals",  stats["total_signals"])
    c2.metric("Sources Active", len(stats.get("signal_sources", {})))
    c3.metric("Upwork",         stats["upwork_signals"])
    c4.metric("Companies Found",stats["total_companies"])

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

        # Upwork drill-down
        upwork_df = filtered[filtered["Source"] == "upwork"]
        if not upwork_df.empty:
            st.divider()
            st.subheader(f"🟢 Upwork Job Listings ({len(upwork_df)})")
            with st.expander("View Upwork listings", expanded=True):
                for _, row in upwork_df.iterrows():
                    st.markdown(f"**{row['Company']}** — {row['Title']}")
                    if row["URL"]:
                        st.markdown(f"🔗 [{row['URL']}]({row['URL']})")
                    st.markdown("---")


# ══════════════════════════════════════════════════════
# 3. ENRICHMENT — COMPANIES
# ══════════════════════════════════════════════════════
elif page == "🔬 Enrichment — Companies":
    module_header("🔬", "Enrichment", "Stage 2 — LLM classifies industry, country, employee count", "#4299e1")
    st.caption("Data stored in: `companies` table  |  Fields: industry, country, employee_count, website, linkedin_url, github_url")

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

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Top Industries")
            ind_counts = df["Industry"].value_counts().head(8).reset_index()
            ind_counts.columns = ["Industry", "Count"]
            st.bar_chart(ind_counts.set_index("Industry"))
        with col2:
            st.subheader("Top Countries")
            co_counts = df["Country"].value_counts().head(8).reset_index()
            co_counts.columns = ["Country", "Count"]
            st.bar_chart(co_counts.set_index("Country"))


# ══════════════════════════════════════════════════════
# 4. SCORER — QUALIFICATION
# ══════════════════════════════════════════════════════
elif page == "🎯 Scorer — Qualification":
    module_header("🎯", "Scorer", "Stage 3 — ICP rule-based scoring, qualifies or rejects companies", "#48bb78")
    st.caption("Data stored in: `companies` table  |  Fields: icp_score, status (QUALIFIED / REJECTED)")

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
                qualified[["Name","Industry","Country","Employees","ICP Score","Status"]]
                .sort_values("ICP Score", ascending=False),
                use_container_width=True, hide_index=True
            )

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
# 5. FINDER + VERIFIER — CONTACTS
# ══════════════════════════════════════════════════════
elif page == "🔍 Finder & ✅ Verifier — Contacts":
    module_header("🔍", "Finder + Verifier", "Stages 4 & 5 — Finds decision maker contacts, verifies email deliverability", "#9f7aea")
    st.caption("Data stored in: `contacts` table  |  Fields: name, role, email, linkedin_url, verified")

    df = load_contacts()
    if df.empty:
        st.info("No contacts yet. Run `python finder.py` then `python verifier.py`")
    else:
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Contacts",  len(df))
        c2.metric("✅ Verified",     len(df[df["Verified"]=="VALID"]))
        c3.metric("❌ Invalid",      len(df[df["Verified"]=="INVALID"]))
        c4.metric("⏳ Pending",      len(df[df["Verified"]=="PENDING"]))

        st.divider()

        ver_sel = st.multiselect("Filter by Verification",
                                 ["VALID","INVALID","PENDING"],
                                 default=["VALID","INVALID","PENDING"])
        filtered = df[df["Verified"].isin(ver_sel)]

        # Colour-code Verified column
        def color_verified(val):
            colors = {"VALID": "color:#48bb78;font-weight:600",
                      "INVALID": "color:#fc8181;font-weight:600",
                      "PENDING": "color:#ecc94b;font-weight:600"}
            return colors.get(val, "")

        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.caption(f"{len(filtered)} contacts shown")

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
# 6. RESEARCH — INTELLIGENCE
# ══════════════════════════════════════════════════════
elif page == "📚 Research — Intelligence":
    module_header("📚", "Research", "Stage 6 — Deep LLM research: summaries, pain points, tech stack, personalization hooks", "#63b3ed")
    st.caption("Data stored in: `research` table  |  Fields: summary, pain_points, tech_stack, recent_news, raw_json")

    df = load_research()
    if df.empty:
        st.info("No research done yet. Run `python research.py`")
    else:
        c1,c2 = st.columns(2)
        c1.metric("Companies Researched", len(df))
        c2.metric("With Tech Stack",      len(df[df["Tech Stack"] != ""]))

        st.divider()
        st.dataframe(df[["Company","Industry","Country","Pain Points","Tech Stack"]],
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("🔍 Deep Dive per Company")
        company_sel = st.selectbox("Select company", df["Company"].tolist())
        row = df[df["Company"] == company_sel].iloc[0]
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**📋 Summary**")
            st.write(row["Summary"] or "—")
            st.markdown(f"**🔧 Tech Stack**")
            st.write(row["Tech Stack"] or "—")
        with col2:
            st.markdown(f"**😤 Pain Points**")
            st.write(row["Pain Points"] or "—")
            st.markdown(f"**📰 Recent News**")
            st.write(row["Recent News"] or "—")


# ══════════════════════════════════════════════════════
# 7. EMAIL WRITER — DRAFTS
# ══════════════════════════════════════════════════════
elif page == "✍️ Email Writer — Drafts":
    module_header("✍️", "Email Writer", "Stage 7 — Generates personalised multi-touch email sequences", "#ecc94b")
    st.caption("Data stored in: `emails` table  |  Fields: subject, body, sequence_number, status, scheduled_at")

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

        status_sel = st.multiselect("Status", df["Status"].unique().tolist(),
                                    default=df["Status"].unique().tolist())
        filtered = df[df["Status"].isin(status_sel)]
        st.dataframe(filtered[["Company","To","Subject","Seq","Status","Scheduled","Sent"]],
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📨 Full Email Preview")
        if not filtered.empty:
            sel_idx = st.selectbox("Select email",
                                   [f"{r['Company']} — {r['Subject']} (Seq {r['Seq']})"
                                    for _, r in filtered.iterrows()])
            idx = [f"{r['Company']} — {r['Subject']} (Seq {r['Seq']})"
                   for _, r in filtered.iterrows()].index(sel_idx)
            row = filtered.iloc[idx]
            st.markdown(f"**To:** {row['To']}  |  **Subject:** {row['Subject']}  |  **Status:** {row['Status']}")
            st.text_area("Body", value=row["Body"], height=200, disabled=True)


# ══════════════════════════════════════════════════════
# 8. SENDER — SENT EMAILS
# ══════════════════════════════════════════════════════
elif page == "📤 Sender — Sent Emails":
    module_header("📤", "Sender", "Stage 8 — Delivers scheduled emails via SMTP", "#667eea")
    st.caption("Data stored in: `emails` table  |  Fields: status=SENT, sent_at, message_id")

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

    tab1, tab2 = st.tabs(["📤 Sent", "📅 Scheduled"])
    with tab1:
        if sent.empty:
            st.info("No emails sent yet. Run `python sender.py`")
        else:
            st.dataframe(sent[["Company","To","Subject","Seq","Sent"]],
                         use_container_width=True, hide_index=True)
    with tab2:
        if scheduled.empty:
            st.info("No emails scheduled yet.")
        else:
            st.dataframe(scheduled[["Company","To","Subject","Seq","Scheduled"]],
                         use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════
# 9. REPLY CHECKER — REPLIES
# ══════════════════════════════════════════════════════
elif page == "💬 Reply Checker — Replies":
    module_header("💬", "Reply Checker", "Stage 9 — Monitors inbox via IMAP and logs replies", "#48bb78")
    st.caption("Data stored in: `reply_logs` table  |  Fields: reply_from, reply_subject, reply_body, detected_at")

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
        st.subheader("Reply Details")
        for _, row in df.iterrows():
            with st.expander(f"📩 {row['Company']} — {row['Subject']}"):
                st.write(f"**From:** {row['From']}")
                st.write(f"**Detected:** {row['Detected']}")
                st.divider()
                st.write(row["Preview"])


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
        st.dataframe(pd.DataFrame([
            {"Source": "Greenhouse",      "Type": "Job Board",           "Signal": "JOB_POSTING",    "Status": "✅ Active"},
            {"Source": "Lever",           "Type": "Job Board",           "Signal": "JOB_POSTING",    "Status": "✅ Active"},
            {"Source": "LinkedIn",        "Type": "Job Search",          "Signal": "JOB_POSTING",    "Status": "✅ Active"},
            {"Source": "Upwork",          "Type": "Freelance Job Board", "Signal": "JOB_POSTING",    "Status": "✅ Active"},
            {"Source": "Product Hunt",    "Type": "Product Launches",    "Signal": "PRODUCT_LAUNCH", "Status": "✅ Active"},
            {"Source": "RSS (TechCrunch)","Type": "News Feed",           "Signal": "NEWS",           "Status": "✅ Active"},
        ]), use_container_width=True, hide_index=True)

    with tab3:
        st.dataframe(pd.DataFrame([
            {"Module":"watcher",      "Interval":"Every 6 hours", "Description":"Collect signals (Greenhouse, Lever, LinkedIn, Upwork, PH, RSS)"},
            {"Module":"enrichment",   "Interval":"Every 2 hours", "Description":"LLM enriches industry, country, employee count"},
            {"Module":"scorer",       "Interval":"Every 2 hours", "Description":"ICP rule-based scoring and qualification"},
            {"Module":"finder",       "Interval":"Every 4 hours", "Description":"Google search + LLM to find decision maker contacts"},
            {"Module":"verifier",     "Interval":"Every 4 hours", "Description":"SMTP verification of email deliverability"},
            {"Module":"research",     "Interval":"Every 4 hours", "Description":"Deep LLM research: pain points, tech stack, hooks"},
            {"Module":"email_writer", "Interval":"Every 4 hours", "Description":"Write personalised 3-touch email sequences"},
            {"Module":"sender",       "Interval":"Every 1 hour",  "Description":"Send scheduled emails via SMTP"},
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

