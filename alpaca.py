"""
⚡ Alpaca news: Benzinga's real-time news feed (incl. company press releases from all the newswires), free with an
Alpaca paper account. Keys: ALPACA_KEY / ALPACA_SECRET (GitHub secrets, paper keys are enough). Off without them.

One shared poll (at most every ALPACA_SECONDS) serves the watchlist news and the contract scanner.
Nothing about the watchlist is sent to Alpaca: the bot asks for the latest news of ALL symbols and filters locally.
"""

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("bot")

KEY = os.environ.get("ALPACA_KEY", "").strip()
SECRET = os.environ.get("ALPACA_SECRET", "").strip()
ON = bool(KEY and SECRET) and os.environ.get("ALPACA", "true").lower() == "true"
URL = "https://data.alpaca.markets/v1beta1/news"
POLL = int(os.environ.get("ALPACA_SECONDS", "45"))
KEEP = 400

_items = {}      # id -> item
_last = 0.0
status = "not checked yet"


def _parse(n):
    try:
        d = datetime.fromisoformat((n.get("created_at") or "").replace("Z", "+00:00"))
    except Exception:
        d = None
    return {"id": n.get("id"), "title": (n.get("headline") or "").strip(), "desc": (n.get("summary") or "").strip(),
            "link": (n.get("url") or "").strip(), "date": d, "symbols": [s.upper() for s in n.get("symbols") or []],
            "source": "Benzinga" if (n.get("source") or "").lower() == "benzinga" else (n.get("source") or "Alpaca")}


def _get(params):
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params), headers={
        "APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET, "Accept": "application/json",
        "User-Agent": "price-alert-bot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def recent():
    """Latest news (all symbols), newest last. Polls Alpaca at most every POLL seconds; raises on failure."""
    global _last, status
    if not ON:
        return []
    if time.monotonic() - _last >= POLL or not _items:
        _last = time.monotonic()
        try:
            j = _get({"limit": 50, "sort": "desc", "include_content": "false"})
        except Exception as e:
            status = f"failed ({getattr(e, 'code', type(e).__name__)})"
            raise
        for n in j.get("news") or []:
            it = _parse(n)
            if it["id"] and it["title"]:
                _items[it["id"]] = it
        for k in sorted(_items, key=lambda k: _items[k]["date"] or datetime.min.replace(tzinfo=timezone.utc))[:-KEEP]:
            del _items[k]
        status = "OK"
    return sorted(_items.values(), key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc))


def selftest():
    """For the startup message: 'ON (…)' / 'FAILED (…)'."""
    if not ON:
        return "OFF (add ALPACA_KEY + ALPACA_SECRET secrets)"
    try:
        items = recent()
    except Exception:
        return f"FAILED — {status}; check the ALPACA_KEY / ALPACA_SECRET secrets"
    newest = items[-1]["date"] if items else None
    age = f", newest {int((time.time() - newest.timestamp()) // 60)} min old" if newest else ""
    return f"ON (Benzinga real-time news, checked every minute; {len(items)} latest headlines read{age})"
