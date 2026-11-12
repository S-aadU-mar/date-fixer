# Monitoring foundation

The existing CSVs can be imported into a local SQLite database, then checked
manually from the Streamlit sidebar.

1. Start the app from the repository root:

   ```powershell
   streamlit run app\data_cleaner.py
   ```

2. Select **Import source CSVs**. This imports CSV files directly under
   `data\`, deduplicates locations, and preserves ArcGIS/PDF URLs.
3. Select **Poll imported sources**. Each source is checked with the existing
   ArcGIS/PDF date extraction logic. The database records baselines, unchanged
   checks, updates, and review failures.

The generated database is `data\monitoring.sqlite3` and is ignored by Git.
Update events are stored in the `alert_events` table for later delivery through
email, Slack, or an authenticated dashboard. Automatic scheduling, employee
authentication, and live map rendering are intentionally deferred.
