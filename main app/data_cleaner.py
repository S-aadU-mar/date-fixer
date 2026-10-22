import streamlit as st
import pandas as pd
import csv
from io import StringIO
from functions import main

st.title("Data Accuracy Helper")
st.subheader("This tool crawls API endpoints to verify dates, source types, zone names, etc.")

# user uploads csv here
uploaded_file = st.file_uploader("Choose a file (you can only select a single file)", ".csv", None, False)

if uploaded_file is not None:
    input_df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
    
    # gate crawling and comparison process behind button
    if st.button("Run check"):
        with st.spinner("Crawling endpoints..."):
            # run the process and save the report
            report_df = main(input_df)
        st.session_state["report_df"] = report_df
        
# show last report (survives reruns)
if "report_df" in st.session_state:
    report_df = st.session_state["report_df"] # pull stored df back out of session and into local variable
    st.dataframe(report_df) # render report as interactive table
    st.download_button( # download button to save report as CSV
        "Download report",
        report_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="Report.csv",
        mime="text/csv" 
    )
    



