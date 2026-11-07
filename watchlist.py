"""
Watchlist settings, read from the WATCHLIST environment variable (a GitHub Secret), so the list of stocks
is not visible in the public repo. JSON, for example:

{"threshold_pct": 2, "news_exclude": ["XYZ"], "important_only": [], "timezone": "UTC", "status_time": "08:00",
 "stocks": {"ABC": {"pct": 3, "search": ["\\"ABC Corp\\"", "ABC"], "match": ["ABC Corp", "ABC"], "gov": ["ABC Corp"]}}}

- pct:    alert step for this stock in % (default threshold_pct)
- search: Google News search terms; match: words a headline must contain to count as being about the company
- gov:    company names in US federal contract data (USAspending.gov)

The phone has no WATCHLIST; it gets only GOV_NAMES (JSON {"ABC": ["ABC Corp"]}) for the USAspending.gov check.

Without WATCHLIST the older TICKERS / THRESHOLD_PCT / NEWS_EXCLUDE / NEWS_IMPORTANT_ONLY variables are used.
"""

import json
import os


def _csv(name):
    return [t.strip().upper() for t in os.environ.get(name, "").split(",") if t.strip()]


_raw = os.environ.get("WATCHLIST", "").strip()
try:
    CFG = json.loads(_raw) if _raw else {}
    ERROR = ""
except json.JSONDecodeError as e:
    CFG, ERROR = {}, f"WATCHLIST is not valid JSON (line {e.lineno}, column {e.colno})"

DEFAULT_PCT = float(CFG.get("threshold_pct") or os.environ.get("THRESHOLD_PCT") or 2)
STOCKS = {k.upper(): (v or {}) for k, v in (CFG.get("stocks") or {}).items()}
TICKERS = list(STOCKS) or _csv("TICKERS")
THRESHOLDS = {t: float(STOCKS.get(t, {}).get("pct") or DEFAULT_PCT) / 100.0 for t in TICKERS}
NEWS_EXCLUDE = {t.upper() for t in CFG.get("news_exclude", [])} if CFG else set(_csv("NEWS_EXCLUDE"))
IMPORTANT_ONLY = {t.upper() for t in CFG.get("important_only", [])} if CFG else set(_csv("NEWS_IMPORTANT_ONLY"))
COMPANIES = {t: {"q": s.get("search") or [t], "match": s.get("match") or [t]} for t, s in STOCKS.items()}
GOV_NAMES = {t: s["gov"] for t, s in STOCKS.items() if s.get("gov")}
if not GOV_NAMES and os.environ.get("GOV_NAMES", "").strip():  # phone: only the federal-contract names, no stocks
    try:
        GOV_NAMES = {k.upper(): list(v) for k, v in json.loads(os.environ["GOV_NAMES"]).items() if v}
    except (json.JSONDecodeError, AttributeError, TypeError):
        ERROR = ERROR or "GOV_NAMES is not valid JSON"
TIMEZONE = CFG.get("timezone") or os.environ.get("STATUS_TZ") or "UTC"
STATUS_TIME = CFG.get("status_time") or os.environ.get("STATUS_TIME") or "08:00"
