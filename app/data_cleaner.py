import pandas as pd
import streamlit as st

from functions import main, validate_input_columns


st.set_page_config(
    page_title="Data Accuracy Dashboard",
    page_icon="✓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 2.5rem; padding-bottom: 3rem; }
    .hero { padding: 1.4rem 1.6rem; border-radius: 18px; color: white;
            background: linear-gradient(120deg, #FF4D4D, #754CC7); margin-bottom: 1.5rem; }
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
    '<div class="hero"><h1>Data Accuracy Dashboard</h1>'
    "<p>Validate endpoint dates and zone field names with a clear, review-ready report.</p></div>",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Run a check")
    uploaded_file = st.file_uploader("Upload a CSV file", type=["csv"])
    st.caption("Your file is processed in this session and is not modified.")
    run_check = st.button("Run validation", type="primary", use_container_width=True)

if uploaded_file is not None:
    try:
        input_df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
        st.error(f"Could not read this CSV: {exc}")
        st.stop()

    column_problems = validate_input_columns(input_df)
    if column_problems:
        st.error("This CSV cannot be processed:")
        for problem in column_problems:
            st.error(problem)
        st.stop()

    st.info(f"Loaded **{len(input_df):,} rows** and **{len(input_df.columns):,} columns**.")
    with st.expander("Preview uploaded data", expanded=False):
        st.dataframe(input_df.head(10), use_container_width=True, hide_index=True)

    if run_check:
        progress_bar = st.progress(0, text="Starting validation...")
        status = st.empty()

        def update_progress(current, total, message):
            fraction = current / total if total else 0
            progress_bar.progress(min(fraction, 1.0), text=message)
            status.caption(message)

        try:
            with st.spinner("Checking endpoints and comparing values..."):
                report_df = main(input_df, progress=update_progress)
            progress_bar.progress(1.0, text="Validation complete")
            status.success("Validation finished successfully.")
            st.session_state["report_df"] = report_df
        except (SystemExit, ValueError, KeyError) as exc:
            progress_bar.empty()
            status.empty()
            st.error(str(exc))

report_df = st.session_state.get("report_df")
if report_df is None:
    st.markdown("### Ready when you are")
    st.write("Upload a CSV from the sidebar, then run validation to generate the dashboard.")
    st.stop()

is_zone = (
    report_df["source"].fillna("").str.contains("Zone_Code_FN", case=False, regex=False)
    | report_df["issue"].fillna("").str.contains(
        "Zone_Code_FN", case=False, regex=False
    )
)
is_change = report_df["updation status"].eq("Updation")
is_review = report_df["issue"].fillna("").ne("")
date_changes = report_df[is_change & ~is_zone]
zone_changes = report_df[is_change & is_zone]
date_reviews = report_df[is_review & ~is_zone]
zone_reviews = report_df[is_review & is_zone]

st.divider()
st.subheader("Validation summary")
st.caption(
    "The cards below summarize the report: changes are detected differences, "
    "while reviews are items that need manual attention."
)
metrics = st.columns(5)
metrics[0].metric("Report items", f"{len(report_df):,}")
metrics[1].metric("Date changes", f"{len(date_changes):,}")
metrics[2].metric("Zone changes", f"{len(zone_changes):,}")
metrics[3].metric("Date reviews", f"{len(date_reviews):,}")
metrics[4].metric("Zone reviews", f"{len(zone_reviews):,}")

st.download_button(
    "Download complete report",
    report_df.to_csv(index=False).encode("utf-8-sig"),
    file_name="Report.csv",
    mime="text/csv",
)

st.subheader("Results")
tabs = st.tabs(
    [
        f"All results ({len(report_df)})",
        f"Date changes ({len(date_changes)})",
        f"Zone changes ({len(zone_changes)})",
        f"Date reviews ({len(date_reviews)})",
        f"Zone reviews ({len(zone_reviews)})",
    ]
)


def show_report(data, key):
    if data.empty:
        st.success("No items in this view.")
        return
    st.dataframe(
        data,
        column_config={
            "issue": st.column_config.TextColumn(
                "issue",
                width=500,
                help="Full issue details for this report item",
            ),
        },
        use_container_width=True,
        hide_index=True,
    )
    issues = data[data["issue"].fillna("").ne("")]
    if not issues.empty:
        with st.expander(f"View full issue details ({len(issues)})"):
            for _, row in issues.iterrows():
                location = f"CSV line {row['csv_line']} - {row['city']}"
                st.markdown(f"**{location}**")
                st.write(row["issue"])
    st.download_button(
        "Download this view",
        data.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{key}.csv",
        mime="text/csv",
        key=f"download-{key}",
    )


with tabs[0]:
    show_report(report_df, "complete-report")
with tabs[1]:
    show_report(date_changes, "date-changes")
with tabs[2]:
    show_report(zone_changes, "zone-changes")
with tabs[3]:
    show_report(date_reviews, "date-reviews")
with tabs[4]:
    show_report(zone_reviews, "zone-reviews")
