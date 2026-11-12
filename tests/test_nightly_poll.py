import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.nightly_poll import main


class NightlyPollTests(unittest.TestCase):
    def test_successful_poll_returns_zero(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            db_path = data_dir / "monitoring.sqlite3"
            with (
                patch(
                    "app.nightly_poll.import_csv_files",
                    return_value={"rows": 2, "locations": 1, "sources": 1},
                ) as import_csv,
                patch(
                    "app.nightly_poll.poll_database",
                    return_value={"checked": 1, "updated": 0, "review": 0},
                ) as poll,
            ):
                result = main(
                    [
                        "--db-path",
                        str(db_path),
                        "--data-dir",
                        str(data_dir),
                    ]
                )

        self.assertEqual(result, 0)
        import_csv.assert_called_once_with([], db_path)
        poll.assert_called_once_with(db_path)

    @patch(
        "app.nightly_poll.import_csv_files",
        side_effect=sqlite3.Error("database unavailable"),
    )
    def test_poll_failure_returns_nonzero(self, import_csv):
        result = main(["--data-dir", "data"])

        self.assertEqual(result, 1)
        import_csv.assert_called_once()


if __name__ == "__main__":
    unittest.main()
