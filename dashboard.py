"""
dashboard.py — Streamlit Dashboard

Visualizes the entire pipeline: signals, companies, emails, replies.

Usage:
    streamlit run dashboard.py
"""

import json

import streamlit as st
import pandas as pd
from sqlalchemy import func, text

from database import get_session, init_db
from models import Company, Signal, Contact, Research, Email, ReplyLog, Campaign, Setting


# ── Page Config ────────────────────────────────────────────
st.set_page_config(
    page_title="Sales Machine Dashboard",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Custom CSS ─────────────────────────────────────────────
st.markdown("""
<style>
    .stMetric {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 1rem;
        border-radius: 0.75rem;
        border: 1px solid rgba(255,255,255,0.1);
    }
    .stMetric label {
        color: #a0aec0 !important;
    }
    .stMetric [data-testid="stMetricValue"] {
        color: #e2e8f0 !important;
    }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0f23 0%, #1a1a3e 100%);
    }
    h1 {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
</style>
""", unsafe_allow_html=True)


# ── Data Loading ───────────────────────────────────────────
@st.cache_data(ttl=30)  # Refresh every 30 seconds
def load_stats() -> dict:
    """Load pipeline statistics."""
    init_db()
    stats = {}
    with get_session() as session:
        stats["total_signals"] = session.query(Signal).count()
        stats["total_companies"] = session.query(Company).count()
        stats["enriched"] = session.query(Company).filter_by(status="ENRICHED").count()
        stats["qualified"] = session.query(Company).filter(
            Company.status.in_(["QUALIFIED", "CONTACT_FOUND", "EMAIL_VERIFIED",
                                "RESEARCH_DONE", "EMAIL_READY", "EMAIL_SENT", "REPLIED"])
        ).count()
        stats["rejected"] = session.query(Company).filter_by(status="REJECTED").count()
        stats["contacts"] = session.query(Contact).filter_by(verified="VALID").count()
        stats["emails_sent"] = session.query(Email).filter_by(status="SENT").count()
        stats["emails_scheduled"] = session.query(Email).filter_by(status="SCHEDULED").count()
        stats["replies"] = session.query(ReplyLog).count()

        # Reply rate
        if stats["emails_sent"] > 0:
            # Count unique companies that replied vs. companies we emailed
            companies_emailed = session.query(func.count(func.distinct(Email.company_id))).filter(
                Email.status == "SENT"
            ).scalar()
            companies_replied = session.query(func.count(func.distinct(ReplyLog.company_id))).scalar()
            stats["reply_rate"] = (
                round(companies_replied / companies_emailed * 100, 1)
                if companies_emailed > 0 else 0
            )
        else:
            stats["reply_rate"] = 0

    return stats


@st.cache_data(ttl=30)
def load_pipeline_counts() -> dict[str, int]:
    """Load count of companies at each status."""
    init_db()
    statuses = [
        "NEW_SIGNAL", "ENRICHED", "QUALIFIED", "REJECTED",
        "CONTACT_FOUND", "EMAIL_VERIFIED", "RESEARCH_DONE",
        "EMAIL_READY", "EMAIL_SENT", "REPLIED",
    ]
    counts = {}
    with get_session() as session:
        for status in statuses:
            counts[status] = session.query(Company).filter_by(status=status).count()
    return counts


@st.cache_data(ttl=30)
def load_companies() -> pd.DataFrame:
    """Load all companies as a DataFrame."""
    init_db()
    with get_session() as session:
        companies = session.query(Company).order_by(Company.updated_at.desc()).all()
        data = [{
            "ID": c.id,
            "Name": c.name,
            "Industry": c.industry or "",
            "Country": c.country or "",
            "Employees": c.employee_count or 0,
            "ICP Score": c.icp_score,
            "Status": c.status,
            "Created": c.created_at,
            "Updated": c.updated_at,
        } for c in companies]
    return pd.DataFrame(data) if data else pd.DataFrame()


@st.cache_data(ttl=30)
def load_emails() -> pd.DataFrame:
    """Load recent emails as a DataFrame."""
    init_db()
    with get_session() as session:
        emails = (
            session.query(Email, Contact.email.label("to_email"), Company.name.label("company_name"))
            .join(Contact, Email.contact_id == Contact.id)
            .join(Company, Email.company_id == Company.id)
            .order_by(Email.sent_at.desc().nullslast())
            .limit(100)
            .all()
        )
        data = [{
            "ID": e.Email.id,
            "Company": e.company_name,
            "To": e.to_email,
            "Subject": e.Email.subject,
            "Seq": e.Email.sequence_number,
            "Status": e.Email.status,
            "Scheduled": e.Email.scheduled_at,
            "Sent": e.Email.sent_at,
        } for e in emails]
    return pd.DataFrame(data) if data else pd.DataFrame()


@st.cache_data(ttl=30)
def load_replies() -> pd.DataFrame:
    """Load all replies as a DataFrame."""
    init_db()
    with get_session() as session:
        replies = (
            session.query(ReplyLog, Company.name.label("company_name"))
            .join(Company, ReplyLog.company_id == Company.id)
            .order_by(ReplyLog.detected_at.desc())
            .all()
        )
        data = [{
            "Company": r.company_name,
            "From": r.ReplyLog.reply_from,
            "Subject": r.ReplyLog.reply_subject,
            "Body": (r.ReplyLog.reply_body or "")[:200],
            "Detected": r.ReplyLog.detected_at,
        } for r in replies]
    return pd.DataFrame(data) if data else pd.DataFrame()


# ── Sidebar ────────────────────────────────────────────────
st.sidebar.title("🚀 Sales Machine")
page = st.sidebar.radio(
    "Navigate",
    ["📊 Overview", "🏢 Companies", "📧 Emails", "💬 Replies", "⚙️ Settings"],
)


# ── Pages ──────────────────────────────────────────────────

if page == "📊 Overview":
    st.title("Pipeline Overview")

    stats = load_stats()

    # KPI Cards
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📡 Signals", stats["total_signals"])
    col2.metric("✅ Qualified", stats["qualified"])
    col3.metric("📧 Sent", stats["emails_sent"])
    col4.metric("💬 Replies", stats["replies"])
    col5.metric("📈 Reply Rate", f"{stats['reply_rate']}%")

    st.divider()

    # Pipeline funnel
    st.subheader("Pipeline Funnel")
    pipeline = load_pipeline_counts()

    # Show as a horizontal bar chart
    funnel_data = pd.DataFrame({
        "Stage": list(pipeline.keys()),
        "Count": list(pipeline.values()),
    })
    st.bar_chart(funnel_data.set_index("Stage"))

    # Summary stats
    st.divider()
    col1, col2, col3 = st.columns(3)
    col1.metric("🏢 Total Companies", stats["total_companies"])
    col2.metric("👤 Valid Contacts", stats["contacts"])
    col3.metric("📅 Scheduled Emails", stats["emails_scheduled"])


elif page == "🏢 Companies":
    st.title("Companies")

    df = load_companies()
    if df.empty:
        st.info("No companies found yet. Run the watcher to start collecting signals.")
    else:
        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            status_filter = st.multiselect(
                "Status",
                options=df["Status"].unique().tolist(),
                default=df["Status"].unique().tolist(),
            )
        with col2:
            min_score = st.number_input("Min ICP Score", value=0, min_value=0)
        with col3:
            country_filter = st.multiselect(
                "Country",
                options=sorted(df["Country"].unique().tolist()),
            )

        filtered = df[
            df["Status"].isin(status_filter) &
            (df["ICP Score"] >= min_score)
        ]
        if country_filter:
            filtered = filtered[filtered["Country"].isin(country_filter)]

        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(filtered)} of {len(df)} companies")


elif page == "📧 Emails":
    st.title("Email Log")

    df = load_emails()
    if df.empty:
        st.info("No emails generated yet. Run the pipeline to start sending.")
    else:
        # Status filter
        status_filter = st.multiselect(
            "Email Status",
            options=df["Status"].unique().tolist(),
            default=df["Status"].unique().tolist(),
        )
        filtered = df[df["Status"].isin(status_filter)]
        st.dataframe(filtered, use_container_width=True, hide_index=True)

        # Stats
        col1, col2, col3 = st.columns(3)
        col1.metric("Sent", len(df[df["Status"] == "SENT"]))
        col2.metric("Scheduled", len(df[df["Status"] == "SCHEDULED"]))
        col3.metric("Failed", len(df[df["Status"] == "FAILED"]))


elif page == "💬 Replies":
    st.title("Replies")

    df = load_replies()
    if df.empty:
        st.info("No replies detected yet.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Reply Details")
        for _, row in df.iterrows():
            with st.expander(f"📩 {row['Company']} — {row['Subject']}"):
                st.write(f"**From:** {row['From']}")
                st.write(f"**Detected:** {row['Detected']}")
                st.write("---")
                st.write(row["Body"])


elif page == "⚙️ Settings":
    st.title("Settings")

    st.subheader("ICP Scoring Weights")
    st.info("Edit scoring weights in your `.env` file or `config.py`. Dashboard editing coming soon.")

    from config import ICP_SCORING_RULES, ICP_SCORE_THRESHOLD

    st.write(f"**Qualification Threshold:** {ICP_SCORE_THRESHOLD}")
    st.write("**Scoring Rules:**")
    rules_df = pd.DataFrame({
        "Rule": list(ICP_SCORING_RULES.keys()),
        "Points": list(ICP_SCORING_RULES.values()),
    })
    st.dataframe(rules_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Pipeline Schedule")
    schedule_data = [
        {"Module": "watcher", "Interval": "Every 6 hours"},
        {"Module": "enrichment", "Interval": "Every 2 hours"},
        {"Module": "scorer", "Interval": "Every 2 hours"},
        {"Module": "finder", "Interval": "Every 4 hours"},
        {"Module": "verifier", "Interval": "Every 4 hours"},
        {"Module": "research", "Interval": "Every 4 hours"},
        {"Module": "email_writer", "Interval": "Every 4 hours"},
        {"Module": "sender", "Interval": "Every 1 hour"},
        {"Module": "reply_checker", "Interval": "Every 30 minutes"},
    ]
    st.dataframe(pd.DataFrame(schedule_data), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Quick Actions")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()

    with col2:
        if st.button("🗑️ Clear Cache"):
            st.cache_data.clear()
            st.success("Cache cleared!")
