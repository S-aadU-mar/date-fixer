import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.monitoring import connect, import_csv_files, poll_database


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "monitoring.sqlite3"
        self.csv_path = Path(self.temp_dir.name) / "locations.csv"
        pd.DataFrame(
            [
                {
                    "100 - IDs": "1",
                    "100 - Cities": "Example",
                    "73 - Counties": "Example County",
                    "20 - States": "CA",
                    "100 - Endpoint_Links": "https://example.test/FeatureServer/0",
                    "FLUM_Link": "",
                    "Source": "ArcGIS",
                },
                {
                    "100 - IDs": "1",
                    "100 - Cities": "Example",
                    "73 - Counties": "Example County",
                    "20 - States": "CA",
                    "100 - Endpoint_Links": "https://example.test/FeatureServer/0",
                    "FLUM_Link": "",
                    "Source": "ArcGIS",
                },
            ]
        ).to_csv(self.csv_path, index=False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_import_is_idempotent(self):
        first = import_csv_files([self.csv_path], self.db_path)
        second = import_csv_files([self.csv_path], self.db_path)
        self.assertEqual(first["locations"], 1)
        self.assertEqual(first["sources"], 1)
        self.assertEqual(second["locations"], 1)
        self.assertEqual(second["sources"], 0)

    @patch("app.monitoring.find_date", return_value=(date(2026, 1, 2), "endpoint", "https://example.test/FeatureServer/0", [], []))
    def test_first_poll_is_baseline(self, find_date):
        import_csv_files([self.csv_path], self.db_path)
        result = poll_database(self.db_path)
        self.assertEqual(result["updated"], 0)
        connection = connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT status FROM observations").fetchone()[0], "baseline")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0], 0)
        finally:
            connection.close()

    @patch("app.monitoring.find_date", side_effect=[
        (date(2026, 1, 2), "endpoint", "https://example.test/FeatureServer/0", [], []),
        (date(2026, 2, 2), "endpoint", "https://example.test/FeatureServer/0", [], []),
    ])
    def test_newer_date_creates_alert(self, find_date):
        import_csv_files([self.csv_path], self.db_path)
        poll_database(self.db_path)
        result = poll_database(self.db_path)
        self.assertEqual(result["updated"], 1)
        connection = connect(self.db_path)
        self.assertEqual(connection.execute("SELECT status FROM observations ORDER BY id DESC").fetchone()[0], "updated")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0], 1)
        connection.close()


if __name__ == "__main__":
    unittest.main()
