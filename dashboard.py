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

SOURCE_COLORS = {
    "upwork":      "#14a800",
    "linkedin":    "#0a66c2",
    "greenhouse":  "#3bba4c",
    "lever":       "#5db7de",
    "producthunt": "#da552f",
    "rss":         "#f59e0b",
    "unknown":     "#718096",
}

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
        stats["new_signal"] = session.query(Company).filter_by(status="NEW_SIGNAL").count()

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

        # Signal source breakdown
        source_rows = session.execute(
            text("SELECT source, COUNT(*) as cnt FROM signals GROUP BY source ORDER BY cnt DESC")
        ).fetchall()
        stats["signal_sources"] = {row[0] or "unknown": row[1] for row in source_rows}

        # Signal type breakdown
        type_rows = session.execute(
            text("SELECT signal_type, COUNT(*) as cnt FROM signals GROUP BY signal_type ORDER BY cnt DESC")
        ).fetchall()
        stats["signal_types"] = {row[0] or "UNKNOWN": row[1] for row in type_rows}

        stats["upwork_signals"] = session.query(Signal).filter_by(source="upwork").count()

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
            "Website": c.website or "",
            "LinkedIn": c.linkedin_url or "",
            "GitHub": c.github_url or "",
            "Created": c.created_at,
            "Updated": c.updated_at,
        } for c in companies]
    return pd.DataFrame(data) if data else pd.DataFrame()


@st.cache_data(ttl=30)
def load_signals() -> pd.DataFrame:
    """Load all signals as a DataFrame."""
    init_db()
    with get_session() as session:
        signals = (
            session.query(Signal, Company.name.label("company_name"))
            .join(Company, Signal.company_id == Company.id)
            .order_by(Signal.detected_at.desc())
            .limit(500)
            .all()
        )
        data = [{
            "Company": s.company_name,
            "Type": s.Signal.signal_type,
            "Source": s.Signal.source or "",
            "Title": s.Signal.title or "",
            "URL": s.Signal.raw_url or "",
            "Detected": s.Signal.detected_at,
        } for s in signals]
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
st.sidebar.markdown("---")
page = st.sidebar.radio(
    "Navigate",
    ["📊 Overview", "📡 Signals", "🏢 Companies", "📧 Emails", "💬 Replies", "⚙️ Settings"],
)

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("Signal Sources")
for source, color in SOURCE_COLORS.items():
    st.sidebar.markdown(
        f'<span style="color:{color}; font-size:0.8rem;">● {source.capitalize()}</span>',
        unsafe_allow_html=True
    )


# ── Pages ──────────────────────────────────────────────────

if page == "📊 Overview":
    st.title("Pipeline Overview")

    stats = load_stats()

    # KPI Row 1
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📡 Total Signals", stats["total_signals"])
    col2.metric("🆕 Unprocessed", stats["new_signal"])
    col3.metric("✅ Qualified", stats["qualified"])
    col4.metric("❌ Rejected", stats["rejected"])
    col5.metric("🏢 Companies", stats["total_companies"])

    st.divider()

    # KPI Row 2
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("👤 Valid Contacts", stats["contacts"])
    col2.metric("📧 Emails Sent", stats["emails_sent"])
    col3.metric("📅 Scheduled", stats["emails_scheduled"])
    col4.metric("💬 Replies", stats["replies"])
    col5.metric("📈 Reply Rate", f"{stats['reply_rate']}%")

    st.divider()

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("Pipeline Funnel")
        pipeline = load_pipeline_counts()
        non_zero = {k: v for k, v in pipeline.items() if v > 0}
        if non_zero:
            funnel_data = pd.DataFrame({
                "Stage": list(non_zero.keys()),
                "Count": list(non_zero.values()),
            })
            st.bar_chart(funnel_data.set_index("Stage"))
        else:
            st.info("No companies in pipeline yet.")

    with col_right:
        st.subheader("📡 Signal Sources")
        sources = stats.get("signal_sources", {})
        if sources:
            src_df = pd.DataFrame({
                "Source": list(sources.keys()),
                "Signals": list(sources.values()),
            })
            st.dataframe(src_df, use_container_width=True, hide_index=True)
            if stats["upwork_signals"] > 0:
                st.success(f"🟢 Upwork: **{stats['upwork_signals']}** signals collected")
            else:
                st.warning("⚠️ No Upwork signals yet — run the watcher")
        else:
            st.info("No signals yet.")

    st.divider()

    st.subheader("Signal Type Breakdown")
    types = stats.get("signal_types", {})
    if types:
        type_df = pd.DataFrame({
            "Signal Type": list(types.keys()),
            "Count": list(types.values()),
        })
        st.bar_chart(type_df.set_index("Signal Type"))
    else:
        st.info("No signals recorded yet.")


elif page == "📡 Signals":
    st.title("Signal Explorer")
    st.caption("All raw signals collected by the watcher (latest 500).")

    df = load_signals()
    if df.empty:
        st.info("No signals found yet. Run `python watcher.py` to collect signals.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            source_filter = st.multiselect(
                "Filter by Source",
                options=sorted(df["Source"].unique().tolist()),
                default=sorted(df["Source"].unique().tolist()),
            )
        with col2:
            type_filter = st.multiselect(
                "Filter by Type",
                options=sorted(df["Type"].unique().tolist()),
                default=sorted(df["Type"].unique().tolist()),
            )

        filtered = df[
            df["Source"].isin(source_filter) &
            df["Type"].isin(type_filter)
        ]

        source_counts = filtered["Source"].value_counts()
        pills = " ".join(
            f'<span style="background:{SOURCE_COLORS.get(s,"#718096")};color:#fff;'
            f'padding:2px 10px;border-radius:12px;font-size:0.75rem;margin:2px;">'
            f'{s}: {c}</span>'
            for s, c in source_counts.items()
        )
        st.markdown(pills, unsafe_allow_html=True)
        st.markdown("")

        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(filtered)} of {len(df)} signals")

        upwork_df = filtered[filtered["Source"] == "upwork"]
        if not upwork_df.empty:
            st.divider()
            st.subheader(f"🟢 Upwork Signals ({len(upwork_df)})")
            with st.expander("View Upwork job listings", expanded=False):
                for _, row in upwork_df.iterrows():
                    st.markdown(f"**{row['Company']}** — {row['Title']}")
                    if row["URL"]:
                        st.markdown(f"🔗 [{row['URL']}]({row['URL']})")
                    st.markdown("---")


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

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Shown", len(filtered))
        col2.metric("Avg ICP Score", round(filtered["ICP Score"].mean(), 1) if not filtered.empty else 0)
        col3.metric("Countries", filtered["Country"].nunique())
        col4.metric("Industries", filtered["Industry"].nunique())

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
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Sent", len(df[df["Status"] == "SENT"]))
        col2.metric("Scheduled", len(df[df["Status"] == "SCHEDULED"]))
        col3.metric("Failed", len(df[df["Status"] == "FAILED"]))
        col4.metric("Drafts", len(df[df["Status"] == "DRAFT"]))


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
    st.subheader("🔗 Signal Sources Configured")
    sources_table = [
        {"Source": "Greenhouse", "Type": "Job Board", "Signal": "JOB_POSTING", "Status": "✅ Active"},
        {"Source": "Lever", "Type": "Job Board", "Signal": "JOB_POSTING", "Status": "✅ Active"},
        {"Source": "LinkedIn", "Type": "Job Search", "Signal": "JOB_POSTING", "Status": "✅ Active"},
        {"Source": "Upwork", "Type": "Freelance Job Board", "Signal": "JOB_POSTING", "Status": "✅ Active"},
        {"Source": "Product Hunt", "Type": "Product Launches", "Signal": "PRODUCT_LAUNCH", "Status": "✅ Active"},
        {"Source": "RSS (TechCrunch)", "Type": "News Feed", "Signal": "NEWS", "Status": "✅ Active"},
    ]
    st.dataframe(pd.DataFrame(sources_table), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Pipeline Schedule")
    schedule_data = [
        {"Module": "watcher", "Interval": "Every 6 hours", "Description": "Collects signals from all sources incl. Upwork"},
        {"Module": "enrichment", "Interval": "Every 2 hours", "Description": "Enriches company data via LLM"},
        {"Module": "scorer", "Interval": "Every 2 hours", "Description": "ICP scoring & qualification"},
        {"Module": "finder", "Interval": "Every 4 hours", "Description": "Finds decision maker contacts"},
        {"Module": "verifier", "Interval": "Every 4 hours", "Description": "Verifies email addresses"},
        {"Module": "research", "Interval": "Every 4 hours", "Description": "Deep company research via LLM"},
        {"Module": "email_writer", "Interval": "Every 4 hours", "Description": "Generates personalised emails"},
        {"Module": "sender", "Interval": "Every 1 hour", "Description": "Sends scheduled emails"},
        {"Module": "reply_checker", "Interval": "Every 30 min", "Description": "Checks inbox for replies"},
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
