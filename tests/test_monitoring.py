import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

import pandas as pd

from app.monitoring import (
    connect,
    count_open_alerts,
    get_location_details,
    import_csv_files,
    list_locations,
    poll_database,
)


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

    def test_locations_and_details_are_available_without_polling(self):
        import_csv_files([self.csv_path], self.db_path)
        locations = list_locations(self.db_path)
        self.assertEqual(
            locations[["city", "county", "state", "url"]].iloc[0].tolist(),
            ["Example", "Example County", "CA", "https://example.test/FeatureServer/0"],
        )
        details = get_location_details(int(locations.iloc[0]["location_id"]), self.db_path)
        self.assertIsNotNone(details)
        self.assertEqual(details["location"]["name"], "Example")
        self.assertEqual(len(details["sources"]), 1)

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

    @patch("app.monitoring.find_date", return_value=(None, None, None, [], ["URL is inaccessible"]))
    def test_review_creates_open_issue_alert(self, find_date):
        import_csv_files([self.csv_path], self.db_path)
        poll_database(self.db_path)
        self.assertEqual(count_open_alerts(self.db_path), 1)

    @patch("app.monitoring.get_json_fallback", return_value={"layers": [{"id": 0}, {"id": 1}]})
    @patch("app.monitoring.session.get")
    def test_load_arcgis_geojson_reads_all_service_layers(self, get, get_json):
        responses = []
        for zone_code in ("A", "B"):
            response = Mock()
            response.json.return_value = {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"ZONE": zone_code}}],
            }
            responses.append(response)
        get.side_effect = responses

        from app.monitoring import load_arcgis_geojson

        geojson, loaded_layers, errors = load_arcgis_geojson(
            "https://example.test/FeatureServer"
        )

        self.assertEqual(len(geojson["features"]), 2)
        self.assertEqual(len(loaded_layers), 2)
        self.assertEqual(errors, [])
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
