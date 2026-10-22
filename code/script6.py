"""
Update New_Date in a zoning CSV using dates found via ArcGIS metadata.

Run:  python update_dates.py
Reads the input CSV, writes the corrected CSV and ONE report CSV that lists
every change and every row needing review.
"""
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import get_close_matches
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests

CSV_IN = r"data\Test Data.csv"
CSV_OUT = r"data\script6\Corrected Dates.csv"
REPORT_OUT = r"data\script6\Report.csv"  # the single changes + review report
REPORT_COLS = ["csv_line", "city", "old_date", "new_date", "type",
               "source", "url", "review_comment"]

ZONE_COL = "Zone_Code_FN"  # comma-separated field names that must exist in the endpoint

VIEWER_COL = "TML: 183 | NMF: 0"
ENDPOINT_COL = "171 - Endpoint_Links"
STATUS_COL = "U: 146 | NU: 37 | I: 0"
DATE_COL = "New_Date"
DATE_FMT = "%d-%m-%Y"  # your CSV uses DD-MM-YYYY
DATE_TZ = "UTC"        # zone used to turn timestamps into dates, e.g. "America/New_York"
                       # (non-UTC zones on Windows need: pip install tzdata)

USE_METADATA_CREATION_DATE = False  # CreaDate = when metadata was created, not updated
SKIP_NO_UPDATION = False  # True = leave rows marked "No Updation" untouched
DELAY = 0.3               # seconds between requests
TIMEOUT = 20

session = requests.Session()
session.headers["User-Agent"] = "date-checker/1.1"


# ---------- errors ----------
class ArcGISError(Exception):
    def __init__(self, err):
        err = err if isinstance(err, dict) else {"message": str(err)}
        self.code = err.get("code")
        self.msg = err.get("message", "")
        super().__init__(f"ArcGIS error {self.code}: {self.msg}")


class NotJSON(Exception):
    pass


class CreationDateOnly(Exception):
    pass


LOCAL_NET_HINTS = ("getaddrinfo failed", "NameResolutionError", "WinError 10013",
                   "Temporary failure in name resolution")
LOCAL_NET_TAG = "local network problem"
MAX_NET_STREAK = 5  # stop after this many consecutive rows that failed this way


def is_local_net_error(e):
    """DNS failure / blocked socket: a problem on your machine or VPN, not the server."""
    return (isinstance(e, requests.exceptions.ConnectionError)
            and not isinstance(e, (requests.exceptions.SSLError,
                                   requests.exceptions.ConnectTimeout))
            and any(h in str(e) for h in LOCAL_NET_HINTS))


def explain(e):
    """Short human-readable reason for the review file."""
    if is_local_net_error(e):
        return f"{LOCAL_NET_TAG} (VPN dropped, DNS down or firewall) - rerun this row"
    if isinstance(e, (ArcGISError, NotJSON, CreationDateOnly)):
        return str(e)
    if isinstance(e, requests.exceptions.SSLError):
        return "SSL error (server does not speak HTTPS on this port, http also failed)"
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return "connection timed out (blocked, firewalled, or server down)"
    if isinstance(e, requests.exceptions.HTTPError):
        return f"HTTP {e.response.status_code}"
    return f"{type(e).__name__}: {str(e)[:120]}"


# ---------- HTTP helpers ----------
def get_json(url, params=None):
    r = session.get(url, params={**(params or {}), "f": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        j = r.json()
    except ValueError:
        raise NotJSON("response is a web page, not a REST endpoint")
    if isinstance(j, dict) and "error" in j:
        raise ArcGISError(j["error"])
    return j


def get_json_fallback(url, params=None):
    """Try https; if the server doesn't speak TLS on that port, retry over http."""
    try:
        return get_json(url, params)
    except requests.exceptions.SSLError:
        if url.startswith("https://"):
            return get_json("http://" + url[len("https://"):], params)
        raise


def get_bytes(url):
    """Plain GET (for the XML metadata), with the same https -> http fallback."""
    try:
        r = session.get(url, timeout=TIMEOUT)
    except requests.exceptions.SSLError:
        if not url.startswith("https://"):
            raise
        r = session.get("http://" + url[len("https://"):], timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def from_epoch_ms(ms):
    tz = timezone.utc if DATE_TZ == "UTC" else ZoneInfo(DATE_TZ)
    return datetime.fromtimestamp(ms / 1000, tz=tz).date()


# ---------- URL helpers ----------
def split_urls(cell):
    """A cell may hold several URLs separated by commas/spaces; return each one."""
    if not isinstance(cell, str):
        return []
    parts = re.split(r"(?:[,\s]|%20)+(?=https?://)", cell.strip())
    return [p.strip(" ,") for p in parts if p.strip().lower().startswith("http")]


def portal_base(url):
    """Where to find /sharing/rest for this viewer link."""
    p = urlparse(url)
    if p.netloc.lower().endswith("arcgis.com"):
        return "https://www.arcgis.com/sharing/rest"
    if "/apps/" in p.path:  # Enterprise portal
        return f"{p.scheme}://{p.netloc}{p.path.split('/apps/')[0]}/sharing/rest"
    return None


def item_id_from(url):
    m = (re.search(r"[?&](?:appid|webmap|id)=([0-9a-f]{32})", url, re.I)
         or re.search(r"/experience/([0-9a-f]{32})", url, re.I)
         or re.search(r"/items/([0-9a-f]{32})", url, re.I))
    return m.group(1) if m else None


# ---------- date extraction ----------
def item_modified(base, item_id):
    item = get_json(f"{base}/content/items/{item_id}")
    return from_epoch_ms(item["modified"]) if item.get("modified") else None


EDIT_FIELD_NAMES = {"editdate", "lasteditdate", "lastediteddate", "editeddate"}
QUERY_ERRORS = (ArcGISError, NotJSON, requests.exceptions.HTTPError)


def find_edit_field(j):
    """Name of the date field that records when each feature was edited, if any."""
    name = (j.get("editFieldsInfo") or {}).get("editDateField")
    if name:
        return name
    for f in j.get("fields") or []:
        norm = re.sub(r"[^a-z]", "", str(f.get("name", "")).lower())
        if f.get("type") == "esriFieldTypeDate" and norm in EDIT_FIELD_NAMES:
            return f["name"]
    return None


def _first_value(result):
    feats = result.get("features") or []
    attrs = (feats[0].get("attributes") or {}) if feats else {}
    val = next(iter(attrs.values()), None)
    return val if isinstance(val, (int, float)) else None


def date_from_edit_field(j, url, label):
    """1st choice: newest value in the layer's EditDate field."""
    field = find_edit_field(j)
    if not field:
        return None, None
    attempts = (
        {"where": "1=1", "returnGeometry": "false",
         "outStatistics": json.dumps([{"statisticType": "max", "onStatisticField": field,
                                       "outStatisticFieldName": "max_edit"}])},
        {"where": f"{field} IS NOT NULL", "returnGeometry": "false", "outFields": field,
         "orderByFields": f"{field} DESC", "resultRecordCount": 1},
    )
    for params in attempts:  # statistics first, then "sort and take the top row"
        try:
            ms = _first_value(get_json_fallback(url + "/query", params))
        except QUERY_ERRORS:
            continue
        if ms:
            return from_epoch_ms(ms), f"{label}: newest {field} value"
    return None, None


def date_from_editing_info(j, label):
    """2nd choice: Data Last Edit Date, else Last Edit Date (schema date ignored)."""
    ei = j.get("editingInfo") or {}
    ms = ei.get("dataLastEditDate") or ei.get("lastEditDate")
    if ms:
        return from_epoch_ms(ms), f"{label}: editingInfo"
    return None, None


def date_from_service_item(j, url, label):
    """3rd choice: modified date of the hosted service's portal item."""
    item_id = j.get("serviceItemId")
    if item_id and "arcgis.com" in url:
        d = item_modified("https://www.arcgis.com/sharing/rest", item_id)
        if d:
            return d, f"{label}: portal item modified"
    return None, None


def _ymd(text):
    m = re.match(r"\s*(\d{4})-?(\d{2})-?(\d{2})", text or "")
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3])).date()
    except ValueError:
        return None


def date_from_metadata(url, label):
    """Read <layer>/metadata (XML). ModDate / reviseDate are update dates;
    CreaDate is only the creation date and is not used unless you enable it."""
    try:
        root = ET.fromstring(get_bytes(url + "/metadata").strip())
    except (requests.exceptions.HTTPError, ET.ParseError):
        return None, None  # no metadata published: normal, not an error
    for tag in ("ModDate", "reviseDate"):
        d = _ymd(root.findtext(f".//{tag}"))
        if d:
            return d, f"{label}: metadata {tag}"
    created = _ymd(root.findtext(".//CreaDate"))
    if created:
        if USE_METADATA_CREATION_DATE:
            return created, f"{label}: metadata CreaDate (creation only)"
        raise CreationDateOnly(f"metadata only has a creation date ({created}), not an update date")
    return None, None


_layer_cache = {}


def layer_info(url):
    """Layer JSON for an endpoint, fetched once per URL (errors are cached too),
    so the date lookup and the Zone_Code_FN check share a single request."""
    url = url.split("?")[0].rstrip("/")
    if url not in _layer_cache:
        try:
            _layer_cache[url] = (get_json_fallback(url), None)
        except Exception as e:
            _layer_cache[url] = (None, e)
        time.sleep(DELAY)
    j, err = _layer_cache[url]
    if err:
        raise err
    return j


def date_from_endpoint(url):
    url = url.split("?")[0].rstrip("/")  # drop any existing ?f=json etc.
    label = "endpoint"
    try:
        j = layer_info(url)
    except ArcGISError as e:
        # Layer id in the link no longer exists -> ask the parent service instead
        if e.code == 404 and re.search(r"/\d+$", url):
            url = re.sub(r"/\d+$", "", url)
            label = "parent service (layer id in link is stale)"
            j = get_json_fallback(url)
        else:
            raise
    for lookup in (lambda: date_from_edit_field(j, url, label),
                   lambda: date_from_editing_info(j, label),
                   lambda: date_from_service_item(j, url, label)):
        d, src = lookup()  # lazy: later (slower) lookups run only if earlier ones find nothing
        if d:
            return d, src
    return date_from_metadata(url, label)  # 4th choice


def date_from_viewer(url):
    base, item_id = portal_base(url), item_id_from(url)
    if base and item_id:
        d = item_modified(base, item_id)
        if d:
            return d, "viewer: item modified"
    return None, None


def find_date(row):
    """Return (date, source, used_url, tried_urls, list_of_problems).
    Tries every URL in both columns. used_url is the link that gave the date."""
    problems, tried = [], []
    for col, fn in ((ENDPOINT_COL, date_from_endpoint), (VIEWER_COL, date_from_viewer)):
        for url in split_urls(row.get(col)):
            tried.append(url)
            try:
                d, src = fn(url)
                if d:
                    return d, src, url, tried, problems
                problems.append(f"{col}: no date available ({url[:90]})")
            except Exception as e:
                problems.append(f"{col}: {explain(e)} ({url[:90]})")
            time.sleep(DELAY)
    return None, None, None, tried, problems


def endpoint_field_names(row):
    """All field names (exact case) exposed by the row's endpoint URL(s)."""
    names, errors = set(), []
    for url in split_urls(row.get(ENDPOINT_COL)):
        try:
            names.update(f["name"] for f in layer_info(url).get("fields") or [])
        except Exception as e:
            errors.append(explain(e))
    return names, errors


def check_zone_codes(row):
    """Verify every comma-separated name in Zone_Code_FN exists (case-sensitive)
    as a field in the endpoint. Returns (new_cell_or_None, problems).

    new_cell is set only when a missing name has exactly one case-insensitive
    match (a pure capitalisation slip), which is safe to auto-correct.
    Anything else is reported in problems for manual review, never changed."""
    cell = row.get(ZONE_COL)
    if not isinstance(cell, str) or not cell.strip():
        return None, []  # empty: ignore
    values = [v.strip() for v in cell.split(",") if v.strip()]
    fields, errors = endpoint_field_names(row)
    if not fields:
        why = "; ".join(errors) or "no endpoint URL, or endpoint has no fields"
        return None, [f"{ZONE_COL}: could not read endpoint fields ({why})"]

    by_lower = {}
    for f in fields:
        by_lower.setdefault(f.lower(), []).append(f)

    fixes, problems = {}, []
    for v in dict.fromkeys(values):  # unique, keeps order
        if v in fields:
            continue
        candidates = by_lower.get(v.lower(), [])
        if len(candidates) == 1:
            fixes[v] = candidates[0]
        else:
            close = get_close_matches(v, sorted(fields), n=3, cutoff=0.6)
            hint = f" (closest: {', '.join(close)})" if close else ""
            problems.append(f"{ZONE_COL}: '{v}' not found in endpoint fields{hint}")

    new_cell = None
    if fixes:
        parts = []
        for p in cell.split(","):  # rebuild, keeping original spacing
            core = p.strip()
            lead = p[:len(p) - len(p.lstrip())]
            trail = p[len(p.rstrip()):]
            parts.append(lead + fixes.get(core, core) + trail)
        new_cell = ",".join(parts)
    return new_cell, problems


def parse(v):
    if pd.isna(v) or not str(v).strip():
        return None
    return pd.to_datetime(v, dayfirst=True).date()


def main():
    df = pd.read_csv(CSV_IN, dtype=str, encoding="utf-8-sig")
    if ZONE_COL not in df.columns:
        raise SystemExit(f"Column '{ZONE_COL}' not found in {CSV_IN}")
    report = []  # one row per change or review item
    net_streak = 0

    def add(line, city, old, new, kind, source, url, comment):
        report.append({"csv_line": line, "city": city, "old_date": old or "",
                       "new_date": new or "", "type": kind, "source": source or "",
                       "url": url or "", "review_comment": comment or ""})

    try:
        for idx, row in df.iterrows():
            line = idx + 2  # line in the original CSV (header = line 1)
            label = f"{row.iloc[1]}, {row.iloc[3]}"
            old_date = row.get(DATE_COL)
            if SKIP_NO_UPDATION and row.get(STATUS_COL) == "No Updation":
                continue

            # --- Zone_Code_FN check ---
            endpoint_urls = " | ".join(split_urls(row.get(ENDPOINT_COL)))
            new_zone, zone_problems = check_zone_codes(row)
            if new_zone is not None and new_zone != row[ZONE_COL]:
                add(line, label, "", "", "change",
                    f"{ZONE_COL} case fix: {row[ZONE_COL]} -> {new_zone}", endpoint_urls, "")
                print(f"[zone fix] line {line} {label}: {row[ZONE_COL]} -> {new_zone}")
            for p in zone_problems:
                add(line, label, old_date, "", "review", "", endpoint_urls, p)
                print(f"[review] line {line} {label}: {p}")

            # --- date check ---
            api_date, src, used_url, tried, problems = find_date(row)
            if api_date is None:
                reason = "; ".join(problems) or "no URL in row"
                add(line, label, old_date, "", "review", "", " | ".join(tried), reason)
                print(f"[review] line {line} {label}: {reason}")
                net_down = bool(problems) and all(LOCAL_NET_TAG in p for p in problems)
                net_streak = net_streak + 1 if net_down else 0
                if net_streak >= MAX_NET_STREAK:
                    print(f"\nSTOPPING at CSV line {line}: {MAX_NET_STREAK} rows in a row "
                          "failed because your network is down (check the VPN), "
                          "so the rest would be false failures.")
                    break
                continue
            net_streak = 0

            if parse(old_date) != api_date:
                new = api_date.strftime(DATE_FMT)
                add(line, label, old_date, new, "change", src, used_url, "")
                df.at[idx, DATE_COL] = new
                print(f"[update] line {line} {label}: {old_date} -> {new} ({src})")
            time.sleep(DELAY)
    finally:  # save progress even if you Ctrl+C
        df.to_csv(CSV_OUT, index=False, encoding="utf-8-sig")
        pd.DataFrame(report, columns=REPORT_COLS).to_csv(
            REPORT_OUT, index=False, encoding="utf-8-sig")
        n_change = sum(r["type"] == "change" for r in report)
        n_review = sum(r["type"] == "review" for r in report)
        print(f"\n{n_change} changes, {n_review} review items -> {REPORT_OUT}")


if __name__ == "__main__":
    main()
