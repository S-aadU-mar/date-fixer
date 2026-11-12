# Monitoring and dashboard

The existing CSVs seed a local SQLite database automatically on the first
manual poll. The Streamlit dashboard provides a read-only city catalog, city
detail views, source records, validation history, notifications, and live
ArcGIS zoning maps when an ArcGIS endpoint is available.

1. Start the app from the repository root:

   ```powershell
   streamlit run app\data_cleaner.py
   ```

2. Select **Run poller now**. On the first run, CSV files directly under
   `data\` are imported automatically, deduplicated, and their ArcGIS/PDF URLs
   are preserved.
3. Select a city row to view its imported metadata, source URLs, validation
   history, and available zoning layers. ArcGIS layers are loaded from the
   source endpoint when the detail view is opened; they are not yet cached or
   converted into a common geometry format.
4. Each source is checked with the existing ArcGIS/PDF date extraction logic.
   The database records baselines, unchanged checks, updates, and review
   failures.

The generated database is `data\monitoring.sqlite3` and is ignored by Git.
Update events are stored in the `alert_events` table for later delivery through
email, Slack, or an authenticated dashboard. Polling is currently manual;
automatic scheduling, employee authentication, alert acknowledgement, and
cached geometry retrieval remain deferred.

## Current frontend decision

Streamlit remains the prototype frontend. The application is ready for
evaluation of alternative frontend technologies, but not for a migration yet.
Before moving to a separate frontend, establish a concrete need for multiple
concurrent users, permissions, large geometry datasets, or frequent
interactive map filtering. A migration should also preserve the existing
SQLite query and monitoring behavior while adding a standalone nightly poll
process and a supported production data store if concurrent writes require it.
