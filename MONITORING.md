# Monitoring foundation

The existing CSVs seed a local SQLite database automatically on the first
manual poll. The dashboard exposes only the poller workflow.

1. Start the app from the repository root:

   ```powershell
   streamlit run app\data_cleaner.py
   ```

2. Select **Run poller now**. On the first run, CSV files directly under
   `data\` are imported automatically, deduplicated, and their ArcGIS/PDF URLs
   are preserved.
3. Each source is checked with the existing ArcGIS/PDF date extraction logic.
   The database records baselines, unchanged checks, updates, and review
   failures.

The generated database is `data\monitoring.sqlite3` and is ignored by Git.
Update events are stored in the `alert_events` table for later delivery through
email, Slack, or an authenticated dashboard. Automatic scheduling, employee
authentication, and live map rendering are intentionally deferred.
