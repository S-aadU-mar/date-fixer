"""
Update New_Date in a zoning CSV using dates found via ArcGIS metadata.

Run:  python update_dates.py
Reads input.csv, writes output.csv, needs_review.csv and changes_log.csv.
"""
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import requests

CSV_IN = r"data\Incorrect Dates.csv"
CSV_OUT = r"data\Corrected Dates.csv"
REVIEW_OUT = r"data\Review.csv"
LOG_OUT = "changes_log.csv"

VIEWER_COL = "TML: 183 | NMF: 0"
ENDPOINT_COL = "171 - Endpoint_Links"
STATUS_COL = "U: 146 | NU: 37 | I: 0"
DATE_COL = "New_Date"
DATE_FMT = "%d-%m-%Y"  # your CSV uses DD-MM-YYYY

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


def explain(e):
    """Short human-readable reason for the review file."""
    if isinstance(e, (ArcGISError, NotJSON)):
        return str(e)
    if isinstance(e, requests.exceptions.SSLError):
        return "SSL error (server does not speak HTTPS on this port, http also failed)"
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return "connection timed out (blocked, firewalled, or server down)"
    if isinstance(e, requests.exceptions.HTTPError):
        return f"HTTP {e.response.status_code}"
    return f"{type(e).__name__}: {str(e)[:120]}"


# ---------- HTTP helpers ----------
def get_json(url):
    r = session.get(url, params={"f": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        j = r.json()
    except ValueError:
        raise NotJSON("response is a web page, not a REST endpoint")
    if isinstance(j, dict) and "error" in j:
        raise ArcGISError(j["error"])
    return j


def get_json_fallback(url):
    """Try https; if the server doesn't speak TLS on that port, retry over http."""
    try:
        return get_json(url)
    except requests.exceptions.SSLError:
        if url.startswith("https://"):
            return get_json("http://" + url[len("https://"):])
        raise


def from_epoch_ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


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


def date_from_json(j, url, label):
    ei = j.get("editingInfo") or {}
    ms = ei.get("dataLastEditDate") or ei.get("lastEditDate")
    if ms:
        return from_epoch_ms(ms), f"{label}: editingInfo"
    item_id = j.get("serviceItemId")
    if item_id and "arcgis.com" in url:
        d = item_modified("https://www.arcgis.com/sharing/rest", item_id)
        if d:
            return d, f"{label}: portal item modified"
    return None, None


def date_from_endpoint(url):
    url = url.split("?")[0].rstrip("/")  # drop any existing ?f=json etc.
    label = "endpoint"
    try:
        j = get_json_fallback(url)
    except ArcGISError as e:
        # Layer id in the link no longer exists -> ask the parent service instead
        if e.code == 404 and re.search(r"/\d+$", url):
            url = re.sub(r"/\d+$", "", url)
            label = "parent service (layer id in link is stale)"
            j = get_json_fallback(url)
        else:
            raise
    return date_from_json(j, url, label)


def date_from_viewer(url):
    base, item_id = portal_base(url), item_id_from(url)
    if base and item_id:
        d = item_modified(base, item_id)
        if d:
            return d, "viewer: item modified"
    return None, None


def find_date(row):
    """Return (date, source, list_of_problems). Tries every URL in both columns."""
    problems = []
    for col, fn in ((ENDPOINT_COL, date_from_endpoint), (VIEWER_COL, date_from_viewer)):
        for url in split_urls(row.get(col)):
            try:
                d, src = fn(url)
                if d:
                    return d, src, problems
                problems.append(f"{col}: no date available ({url[:90]})")
            except Exception as e:
                problems.append(f"{col}: {explain(e)} ({url[:90]})")
            time.sleep(DELAY)
    return None, None, problems


def parse(v):
    if pd.isna(v) or not str(v).strip():
        return None
    return pd.to_datetime(v, dayfirst=True).date()


def main():
    df = pd.read_csv(CSV_IN, dtype=str, encoding="utf-8-sig")
    changes, review = [], []
    try:
        for idx, row in df.iterrows():
            label = f"{row.iloc[1]}, {row.iloc[3]}"
            if SKIP_NO_UPDATION and row.get(STATUS_COL) == "No Updation":
                continue

            api_date, src, problems = find_date(row)
            if api_date is None:
                reason = "; ".join(problems) or "no URL in row"
                review.append({"csv_line": idx + 2, "city": label, "reason": reason})
                print(f"[review] line {idx + 2} {label}: {reason}")
                continue

            if parse(row[DATE_COL]) != api_date:
                new = api_date.strftime(DATE_FMT)
                changes.append({"csv_line": idx + 2, "city": label,
                                "old": row[DATE_COL], "new": new, "source": src})
                df.at[idx, DATE_COL] = new
                print(f"[update] line {idx + 2} {label}: {row[DATE_COL]} -> {new} ({src})")
            time.sleep(DELAY)
    finally:  # save progress even if you Ctrl+C
        df.to_csv(CSV_OUT, index=False, encoding="utf-8-sig")
        pd.DataFrame(review).to_csv(REVIEW_OUT, index=False)
        pd.DataFrame(changes).to_csv(LOG_OUT, index=False)
        print(f"\nUpdated {len(changes)} rows, {len(review)} need manual review")


if __name__ == "__main__":
    main()
