from pathlib import Path
import sqlite3

import streamlit as st

from monitoring import (
    DEFAULT_DB_PATH,
    import_csv_files,
    list_latest_poll_results,
    poll_database,
)


st.set_page_config(
    page_title="Zoning Source Poller",
    page_icon="✓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 2.5rem; padding-bottom: 3rem; }
    .hero { padding: 1.4rem 1.6rem; border-radius: 18px; color: white;
            background: linear-gradient(120deg, #FF3B3B, #2534B0); margin-bottom: 1.5rem; }
    .hero h1 { margin: 0; font-size: 2.25rem; }
    .hero p { margin: .35rem 0 0; opacity: .85; }
    [data-testid="stMetric"] {
        background: rgba(255, 255, 255, .08);
        border: 1px solid rgba(255, 255, 255, .18);
        padding: .8rem;
        border-radius: 12px;
    }
    [data-testid="stMetricLabel"] { color: inherit !important; }
    [data-testid="stMetricValue"] { color: inherit !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="hero"><h1>Zoning Source Poller</h1>'
    "<p>Check ArcGIS endpoints and PDFs for newly published zoning source dates.</p></div>",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Manual poll")
    st.caption("Run the poller now. The first run initializes the source database automatically.")
    run_poller = st.button("Run poller now", type="primary", use_container_width=True)

if run_poller:
    source_files = sorted(Path(__file__).resolve().parents[1].joinpath("data").glob("*.csv"))
    try:
        if not DEFAULT_DB_PATH.exists():
            import_csv_files(source_files)
        progress_bar = st.progress(0, text="Starting poller...")
        poll_status = st.empty()

        def update_poll_progress(current: int, total: int, message: str) -> None:
            fraction = current / total if total else 0
            progress_bar.progress(min(fraction, 1.0), text=message)
            poll_status.caption(message)

        result = poll_database(progress=update_poll_progress)
        progress_bar.progress(1.0, text="Poller complete")
        poll_status.success("Poller finished successfully.")
        st.session_state["poll_result"] = result
        st.session_state["poll_notifications"] = list_latest_poll_results()
        st.sidebar.success(
            f"Checked {result['checked']:,}; updated {result['updated']:,}; "
            f"reviews {result['review']:,}."
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        st.sidebar.error(f"Poll failed: {type(exc).__name__}: {exc}")

poll_result = st.session_state.get("poll_result")
notifications = st.session_state.get("poll_notifications")
if poll_result is None or notifications is None:
    st.info("Ready to poll. Use **Run poller now** in the sidebar.")
    st.stop()

st.subheader("Latest poll")
metrics = st.columns(3)
metrics[0].metric("Sources checked", f"{poll_result['checked']:,}")
metrics[1].metric("Updation flags", f"{poll_result['updated']:,}")
metrics[2].metric("Issues for review", f"{poll_result['review']:,}")

st.divider()
st.subheader("Notifications")
st.caption("Updation flags and issues detected by the latest poll.")

if notifications.empty:
    st.success("No updation flags or issues detected.")
else:
    update_notifications = notifications[notifications["status"].eq("updated")]
    issue_notifications = notifications[notifications["status"].ne("updated")]
    notification_columns = st.columns(2)
    with notification_columns[0]:
        st.markdown(f"#### Updation flags ({len(update_notifications)})")
        if update_notifications.empty:
            st.success("No updation flags detected.")
        for _, row in update_notifications.iterrows():
            st.success(
                f"**{row['city']}, {row['state']}** updated — "
                f"{row['observed_date'] or 'new source value'}"
            )
    with notification_columns[1]:
        st.markdown(f"#### Issues ({len(issue_notifications)})")
        if issue_notifications.empty:
            st.success("No issues detected.")
        for _, row in issue_notifications.iterrows():
            details = row["issue"] or f"Source status: {row['status']}"
            st.warning(f"**{row['city']}, {row['state']}** — {details}")

with st.expander("View latest poll details"):
    st.dataframe(notifications, use_container_width=True, hide_index=True)
