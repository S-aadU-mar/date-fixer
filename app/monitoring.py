"""SQLite-backed zoning source import and polling."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import requests

try:
    from .functions import TIMEOUT, find_date, get_json_fallback, session, split_urls
except ImportError:
    from functions import TIMEOUT, find_date, get_json_fallback, session, split_urls


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "monitoring.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    external_id TEXT,
    name TEXT NOT NULL,
    county TEXT,
    state TEXT,
    source_key TEXT NOT NULL UNIQUE,
    source_file TEXT,
    raw_metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('arcgis', 'pdf', 'other')),
    url TEXT NOT NULL,
    UNIQUE(location_id, kind, url)
);
CREATE TABLE IF NOT EXISTS poll_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    checked_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    poll_run_id INTEGER NOT NULL REFERENCES poll_runs(id) ON DELETE CASCADE,
    observed_date TEXT,
    source TEXT,
    url TEXT,
    status TEXT NOT NULL,
    issue TEXT,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_location ON observations(location_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_created ON alert_events(created_at DESC);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # Another poll may be finishing its write transaction; the busy timeout
        # still protects normal reads/writes when WAL cannot be enabled.
        pass
    connection.executescript(SCHEMA)
    return connection


def _column(df: pd.DataFrame, *names: str) -> str | None:
    def normalize(value: str) -> str:
        value = re.sub(r"^\s*\d+\s*-\s*", "", value)
        return "".join(ch.lower() for ch in value if ch.isalnum())

    normalized = {normalize(str(column)): column for column in df.columns}
    for name in names:
        key = normalize(name)
        if key in normalized:
            return normalized[key]
    return None


def _value(row: pd.Series, column: str | None) -> str:
    if not column or pd.isna(row.get(column)):
        return ""
    return str(row.get(column)).strip()


def _source_kind(value: str, url: str) -> str:
    text = f"{value} {url}".lower()
    if "pdf" in text or url.lower().split("?", 1)[0].endswith(".pdf"):
        return "pdf"
    if "arcgis" in text or "featureserver" in text or "mapserver" in text:
        return "arcgis"
    return "other"


def _location_key(external_id: str, name: str, county: str, state: str) -> str:
    identity = external_id or "|".join((name.casefold(), county.casefold(), state.casefold()))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def import_csv_files(
    csv_paths: Iterable[str | Path],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    """Import CSV rows idempotently and return row/location/source counts."""
    imported = locations = sources = 0
    connection = connect(db_path)
    now = utc_now()
    try:
        for csv_path in csv_paths:
            path = Path(csv_path)
            dataframe = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
            id_col = _column(dataframe, "IDs", "ID")
            name_col = _column(dataframe, "Cities", "City", "Name")
            county_col = _column(dataframe, "Counties", "County")
            state_col = _column(dataframe, "States", "State")
            endpoint_col = _column(dataframe, "Endpoint_Links", "Endpoint Links")
            pdf_col = _column(dataframe, "FLUM_Links", "FLUM_Link", "PDF_Link")
            kind_col = _column(dataframe, "Source", "Type")
            if not name_col or not state_col:
                continue
            for _, row in dataframe.iterrows():
                imported += 1
                external_id = _value(row, id_col)
                name = _value(row, name_col)
                county = _value(row, county_col)
                state = _value(row, state_col)
                if not name:
                    continue
                source_key = _location_key(external_id, name, county, state)
                metadata = {
                    str(column): _value(row, str(column))
                    for column in dataframe.columns
                    if _value(row, str(column))
                }
                connection.execute(
                    """
                    INSERT INTO locations
                        (external_id, name, county, state, source_key, source_file, raw_metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        external_id=excluded.external_id, name=excluded.name,
                        county=excluded.county, state=excluded.state,
                        source_file=excluded.source_file, raw_metadata=excluded.raw_metadata,
                        updated_at=excluded.updated_at
                    """,
                    (external_id, name, county, state, source_key, path.name,
                     json.dumps(metadata), now, now),
                )
                location_id = connection.execute(
                    "SELECT id FROM locations WHERE source_key=?", (source_key,)
                ).fetchone()["id"]
                urls = []
                urls.extend(("arcgis", url) for url in split_urls(_value(row, endpoint_col)))
                urls.extend(("pdf", url) for url in split_urls(_value(row, pdf_col)))
                if not urls and kind_col:
                    for column in dataframe.columns:
                        for url in split_urls(_value(row, str(column))):
                            urls.append((_source_kind(_value(row, kind_col), url), url))
                for kind, url in urls:
                    before = connection.total_changes
                    connection.execute(
                        "INSERT OR IGNORE INTO sources(location_id, kind, url) VALUES (?, ?, ?)",
                        (location_id, kind, url),
                    )
                    sources += int(connection.total_changes > before)
            locations = connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    return {"rows": imported, "locations": locations, "sources": sources}


def _latest_observation(connection: sqlite3.Connection, location_id: int) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM observations WHERE location_id=? ORDER BY observed_at DESC, id DESC LIMIT 1",
        (location_id,),
    ).fetchone()


def _poll_row(location: sqlite3.Row, sources: list[sqlite3.Row]) -> pd.Series:
    values = {"Cities": location["name"], "Counties": location["county"], "States": location["state"]}
    values["Source"] = " ".join(source["kind"] for source in sources)
    values["171 - Endpoint_Links"] = " ".join(
        source["url"] for source in sources if source["kind"] == "arcgis"
    )
    values["FLUM_Link"] = " ".join(
        source["url"] for source in sources if source["kind"] == "pdf"
    )
    return pd.Series(values)


def poll_database(
    db_path: str | Path = DEFAULT_DB_PATH,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, int]:
    """Poll every imported location and persist observations and update alerts."""
    connection = connect(db_path)
    started = utc_now()
    run_id = connection.execute(
        "INSERT INTO poll_runs(started_at, status) VALUES (?, 'running')", (started,)
    ).lastrowid
    counts = {"checked": 0, "updated": 0, "review": 0}
    try:
        locations = connection.execute("SELECT * FROM locations ORDER BY name").fetchall()
        total = len(locations)
        for index, location in enumerate(locations, start=1):
            if progress:
                progress(
                    index - 1,
                    total,
                    f"Checking {location['name']}, {location['state']} "
                    f"({index} of {total})...",
                )
            sources = connection.execute(
                "SELECT * FROM sources WHERE location_id=? ORDER BY kind, id",
                (location["id"],),
            ).fetchall()
            observed_at = utc_now()
            date, source, url, _, problems = find_date(_poll_row(location, sources))
            previous = _latest_observation(connection, location["id"])
            previous_date = previous["observed_date"] if previous else None
            issue = "; ".join(problems) if problems else None
            if date is None:
                status = "review"
                counts["review"] += 1
                observed_date = None
            elif previous_date is None:
                status = "baseline"
                observed_date = date.isoformat()
            elif date == datetime.fromisoformat(previous_date).date():
                status = "unchanged"
                observed_date = date.isoformat()
            elif date > datetime.fromisoformat(previous_date).date():
                status = "updated"
                observed_date = date.isoformat()
                counts["updated"] += 1
            else:
                status = "changed"
                observed_date = date.isoformat()
                issue = "Observed date is earlier than the previous value"
                counts["review"] += 1
            observation_id = connection.execute(
                """
                INSERT INTO observations
                    (location_id, poll_run_id, observed_date, source, url, status, issue, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (location["id"], run_id, observed_date, source, url, status, issue, observed_at),
            ).lastrowid
            if status == "updated":
                connection.execute(
                    """
                    INSERT INTO alert_events(location_id, observation_id, event_type, message, created_at)
                    VALUES (?, ?, 'updated', ?, ?)
                    """,
                    (location["id"], observation_id,
                     f"Attention! {location['name']} updated its zoning source date to {observed_date}.",
                     observed_at),
                )
            elif status in ("review", "changed"):
                connection.execute(
                    """
                    INSERT INTO alert_events(location_id, observation_id, event_type, message, created_at)
                    VALUES (?, ?, 'issue', ?, ?)
                    """,
                    (location["id"], observation_id,
                     f"{location['name']}: {issue or 'source requires review'}.",
                     observed_at),
                )
            counts["checked"] += 1
            if progress:
                progress(
                    index,
                    total,
                    f"Checked {location['name']}, {location['state']} "
                    f"({index} of {total})",
                )
        connection.execute(
            """
            UPDATE poll_runs SET completed_at=?, status='complete',
                checked_count=?, updated_count=?, review_count=? WHERE id=?
            """,
            (utc_now(), counts["checked"], counts["updated"], counts["review"], run_id),
        )
        connection.commit()
    except (OSError, sqlite3.Error, ValueError):
        connection.execute("UPDATE poll_runs SET completed_at=?, status='failed' WHERE id=?",
                           (utc_now(), run_id))
        connection.commit()
        raise
    finally:
        connection.close()
    return counts


def list_alerts(
    db_path: str | Path = DEFAULT_DB_PATH,
    limit: int = 50,
) -> pd.DataFrame:
    """Return unacknowledged validation alerts for the dashboard."""
    columns = ["created_at", "city", "county", "state", "type", "message"]
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT a.created_at, l.name AS city, l.county, l.state,
                   a.event_type AS type, a.message
            FROM alert_events a JOIN locations l ON l.id=a.location_id
            WHERE a.acknowledged_at IS NULL
            ORDER BY a.created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return pd.DataFrame([dict(row) for row in rows], columns=columns)
    finally:
        connection.close()


def list_locations(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    """Return the read-only city catalog shown on the dashboard."""
    columns = ["location_id", "city", "county", "state", "url"]
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT l.id AS location_id, l.name AS city, l.county, l.state,
                   COALESCE(GROUP_CONCAT(s.url, ' | '), '') AS url
            FROM locations l
            LEFT JOIN sources s ON s.location_id=l.id
            GROUP BY l.id
            ORDER BY l.name, l.state, l.county
            """
        ).fetchall()
        return pd.DataFrame([dict(row) for row in rows], columns=columns)
    finally:
        connection.close()


def get_location_details(
    location_id: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, object] | None:
    """Return imported metadata and validation history for one location."""
    connection = connect(db_path)
    try:
        location = connection.execute(
            "SELECT * FROM locations WHERE id=?", (location_id,)
        ).fetchone()
        if location is None:
            return None
        sources = connection.execute(
            "SELECT kind, url FROM sources WHERE location_id=? ORDER BY kind, id",
            (location_id,),
        ).fetchall()
        observations = connection.execute(
            """
            SELECT observed_at, observed_date, status, issue, source, url
            FROM observations
            WHERE location_id=?
            ORDER BY observed_at DESC, id DESC
            """,
            (location_id,),
        ).fetchall()
        return {
            "location": dict(location),
            "metadata": json.loads(location["raw_metadata"]),
            "sources": pd.DataFrame([dict(row) for row in sources]),
            "observations": pd.DataFrame([dict(row) for row in observations]),
        }
    finally:
        connection.close()


def load_arcgis_geojson(
    url: str,
    max_layers: int = 20,
) -> tuple[dict[str, object], list[str], list[str]]:
    """Load ArcGIS feature layers as one GeoJSON FeatureCollection."""
    service_url = url.split("?", 1)[0].rstrip("/")
    if re.search(r"/\d+$", service_url):
        layer_urls = [service_url]
    else:
        service = get_json_fallback(service_url)
        layer_urls = [
            f"{service_url}/{layer['id']}"
            for layer in (service.get("layers") or [])[:max_layers]
            if layer.get("id") is not None
        ]
    if not layer_urls:
        raise ValueError(f"No feature layers were found at {service_url}")

    features: list[dict[str, object]] = []
    loaded_layers: list[str] = []
    errors: list[str] = []
    for layer_url in layer_urls:
        try:
            response = session.get(
                f"{layer_url}/query",
                params={
                    "where": "1=1",
                    "outFields": "*",
                    "outSR": "4326",
                    "returnGeometry": "true",
                    "f": "geojson",
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("ArcGIS response was not a JSON object")
            if payload.get("error"):
                raise ValueError(payload["error"].get("message", "ArcGIS query failed"))
            layer_features = payload.get("features")
            if not isinstance(layer_features, list):
                raise ValueError("ArcGIS response did not contain GeoJSON features")
            features.extend(feature for feature in layer_features if isinstance(feature, dict))
            loaded_layers.append(layer_url)
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{layer_url}: {type(exc).__name__}: {exc}")

    if not loaded_layers:
        raise ValueError("Unable to load any ArcGIS layers. " + " | ".join(errors))
    return (
        {"type": "FeatureCollection", "features": features},
        loaded_layers,
        errors,
    )


def count_open_alerts(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """Return the number of unacknowledged validation issues."""
    connection = connect(db_path)
    try:
        return connection.execute(
            """
            SELECT COUNT(*)
            FROM alert_events
            WHERE acknowledged_at IS NULL
              AND event_type != 'updated'
            """
        ).fetchone()[0]
    finally:
        connection.close()


def list_latest_poll_results(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """Return update and review observations from the latest completed poll."""
    columns = [
        "city", "county", "state", "status", "observed_date",
        "source", "url", "issue", "observed_at",
    ]
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT l.name AS city, l.county, l.state, o.status, o.observed_date,
                   o.source, o.url, o.issue, o.observed_at
            FROM observations o
            JOIN locations l ON l.id=o.location_id
            WHERE o.poll_run_id = (
                SELECT id FROM poll_runs
                WHERE status='complete'
                ORDER BY id DESC LIMIT 1
            )
            AND o.status IN ('updated', 'review', 'changed')
            ORDER BY CASE o.status WHEN 'updated' THEN 0 ELSE 1 END, l.name
            """,
        ).fetchall()
        return pd.DataFrame([dict(row) for row in rows], columns=columns)
    finally:
        connection.close()
