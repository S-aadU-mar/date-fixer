from tarfile import DIRTYPE
import re, time
import requests
from requests.models import encode_multipart_formdata
from requests.sessions import session
import pandas as pd
from datetime import date, datetime, timezone
from urllib.parse import urlparse

CSV_IN = " "
CSV_OUT = " "
REVIEW_OUT = " "
VIEWER_COL = "TML: 183 | NMF: 0"
ENDPOINT_COL = "171 - Endpoint_Links"
DATE_COL = "New_Date"
DATE_FMT = "%d-%m-%Y"

session = requests.Session() # for persistent cookies, connection pooling and shared configuration
session.headers["User-Agent"] = "date-checker/1.0" # default header for every request made through this session

def get_json(url, params=None):
    r = session.get(
        url,
        params={**(params or {}), "f":"json"}, # builds query string; if params is None or falsy use empty dict; e.g. https://example.com/api/blablabla&f=json
        timeout=20
    )
    r.raise_for_status() # after response comes back, checks HTTP status code
    j = r.json()
    if "error" in j:
        raise ValueError(j["error"])
    return j

def from_epoch_ms(ms): # converts epoch timestamp in milliseconds to UTC calendar date
    return datetime.fromtimestamp(
        ms/1000,
        tz=timezone.utc
    ).date()

def portal_base(url): # helper for ArcGIS tool; given web-map viewer link it tries to figure out base URL of that portal's REST API
    """Where to find /sharing/rest for this viewer link."""
    p = urlparse(url)
    host = p.netloc.lower()
    if host.endswith("arcgis.com"):
        return "https://www.arcgis.com/sharing/rest"
    if "/apps/" in p.path:
        return f"{p.schema}://{p.netloc}{p.path.split('/apps/')[0]}/sharing/rest" # if recognise link, return string URL
    return None

def item_id_from(url): # given ArcGIS viewer/portal link, extracts 32-char hexadec ArcGIS item ID
    m = (
        re.search(r"[?&](?:appid|webmap|id)=([0-9a-f]{32})", url, re.I)
        or re.search(r"/experience/([0-9a-f]{32})", url, re.I)
        or re.search(r"/items/([0-9a-f]{32})", url, re.I)
    )
    return m.group(1) if m else None

def date_from_endpoint(url): # given ArcGIS service endpoint URL, figure out when data was last edited by querying service
    j = get_json(url.rstrip("/")) #'rstrip' strips any trailing slashes
    ei = j.get("editingInfo") or {}
    ms = ei.get("dataLastEditData") or ei.get("lastEditData") # pull out editing info object and read and store edit timestamp in ms
    if ms:
        return from_epoch_ms(ms), "endpoint.editingInfo"
    # hosted layers link back to their portal item
    item_id = j.get("serviceItemId") # id editingInfo didn't yield a date, fall back to portal item
    if item_id and "arcgis.com" in url:
        item = get_json(f"https://www.arcgis.com/sharing/rest/content/items/{item_id}")
        if item.get("modified"):
            return from_epoch_ms(item["modified"]), "endpoint.serviceItem.modified"
    return None, None

def date_from_viewer(url): # given ArcGIS viewer/app, figure out when portal item last modified
    base, item_id = portal_base(url), item_id_from(url)
    if base and item_id:
        item = get_json(f"{base}/content/items/{item_id}")
        if item.get("modified"):
            return from_epoch_ms(item["modified"]), "viewer.item.modified"
    return None, None

def find_date(row): # function that finds date from a row in a table
    for col, fn in (
        (ENDPOINT_COL, date_from_endpoint), # first by trying the endpoint API
        (VIEWER_COL, date_from_viewer) # then by trying the viewer API
    ):
        url = row.get(col)
        if isinstance(url, str) and url.startswith("http"): # only proceed if is URL value
            try:
                d, src = fn(url)
                if d: # if truthy date object found
                    return d, src
            except Exception as e:
                print(f"    {col}: {type(e).__name__}: {e}")
    return None, None

def parse(v): # cell parser for date column; returns value as a date
    return None if pd.isna(v) or not str(v).strip() else pd.to_datetime(v, dayfirst=True).date()

df = pd.read_csv(CSV_IN, dtype=str, encoding="utf-8") # load in the CSV file and store data in dataframe
changes, review = [], []

for idx, row in df.iterrows(): # iterate over dataframe
    label = f"{row.iloc[1]}, {row.iloc[3]}" # build human-readable label
    api_date, src = find_date(row)
    if api_date is None:
        review.append(
            {
                "row": idx,
                "city": label,
                "reason": "no date found"
            }
        )
        continue
    if parse(row[DATE_COL]) != api_date:
        changes.append(
            (
                idx,
                label,
                row[DATE_COL],
                api_date.strftime(DATE_FMT),
                src
            )
        )
        df.at[idx, DATE_COL] = api_date.strftime(DATE_FMT)
    time.sleep(0.3)
