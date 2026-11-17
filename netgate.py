"""Back off from sites that block the bot (2026-11-17).

On the server the bot always uses the same IP address. When Google News answers HTTP 429 ("too many
requests") or FDA.gov answers HTTP 403, retrying every few minutes only keeps the block going and fills
the log. All requests to those hosts go through urlopen() here:

  - a block (429/403) pauses that host for 30 min (FDA 1 h), doubling each time it is still blocked,
    up to 6 h (FDA 12 h); while paused, no request is sent and Blocked is raised at once
  - one WARNING line per block / retry (not one per ticker), so the daily "Problems yesterday" count means something
  - Telegram notice at most once per host per day, and "works again" after a notified block;
    FDA is quiet (it blocks cloud servers for good; only the daily check line shows it)
  - the pause survives restarts (saved in the bot state under "_net")
  - after "works again" the last pause is remembered for MEMORY s: a new block soon after starts at double
    that pause instead of 30 min again (2026-11-17: Google worked ~30 min, blocked 30 min, over and over)
"""
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

log = logging.getLogger("bot")

# host: (name, first pause s, max pause s, Telegram notices?)
HOSTS = {
    "news.google.com": ("Google News", 1800, 6 * 3600, True),
    "www.fda.gov": ("FDA.gov", 3600, 12 * 3600, False),
}
BLOCK_CODES = (403, 429)
NOTICE_AFTER = 3600   # tell Telegram only when a block lasts 1h+ (a single short 429 is not news)
MEMORY = 6 * 3600     # how long the last pause length is remembered after recovery

_lock = threading.Lock()
_st = {}              # host -> {"until", "delay", "since", "notified", "day"}
_loaded = False
_out = []             # pending Telegram messages
_mem = {}             # host -> (last pause s, recovered at) for blocks soon after a recovery


class Blocked(Exception):
    """Host is blocking us (or paused after a block). .code = last HTTP code."""
    def __init__(self, host, code):
        super().__init__(f"{HOSTS[host][0]} blocked (HTTP {code})")
        self.host, self.code = host, code


def is_blocked(e):
    return isinstance(e, Blocked)


def _host(url):
    return urlsplit(url).hostname or ""


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def urlopen(req, timeout=20):
    """Drop-in for urllib.request.urlopen(req, timeout=…) for any URL; gates only the HOSTS."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    host = _host(url)
    if host not in HOSTS:
        return urllib.request.urlopen(req, timeout=timeout)
    now = time.time()
    with _lock:
        s = _st.get(host)
        if s and now < s.get("until", 0):
            raise Blocked(host, s.get("code", 429))
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in BLOCK_CODES:
            _blocked(host, e.code, now)
            raise Blocked(host, e.code) from None
        raise
    _ok(host)
    return r


def _blocked(host, code, now):
    name, first, cap, _ = HOSTS[host]
    with _lock:
        s = _st.setdefault(host, {})
        if s.get("until"):
            s["delay"] = min(cap, s["delay"] * 2)
        else:
            last, at = _mem.pop(host, (0, 0))
            s["delay"] = min(cap, last * 2) if last and now - at < MEMORY else first
        s["until"] = now + s["delay"]
        s["code"] = code
        s.setdefault("since", now)
        delay = s["delay"]
    log.warning("%s blocked the bot (HTTP %d): pausing it for %d min", name, code, delay // 60)


def _ok(host):
    with _lock:
        s = _st.pop(host, None)
        if s:
            _mem[host] = (s.get("delay", 0), time.time())
    if s:
        log.info("%s works again", HOSTS[host][0])
        if s.get("notified"):
            _out.append(f"✅ {HOSTS[host][0]} works again — its news searches are back on.")


def notices():
    """Telegram messages due now (called by the news loop, which has send()). Once per host per day."""
    now, today, msgs = time.time(), _today(), []
    with _lock:
        msgs, _out[:] = list(_out), []
        for host, s in _st.items():
            name, _, cap, loud = HOSTS[host]
            if not loud or s.get("day") == today or now - s.get("since", now) < NOTICE_AFTER:
                continue
            s["day"], s["notified"] = today, True
            mins = max(1, int((s["until"] - now) // 60))
            msgs.append(f"⚠️ {name} is blocking the bot (HTTP {s.get('code')}, too many requests). Its searches "
                        f"are paused; next try in ~{mins} min (waits grow up to {cap // 3600} h). "
                        f"Other news sources keep working. Max one such message per day.")
    return msgs


def status_line():
    """Short line for the daily check; '' when nothing is blocked."""
    now = time.time()
    with _lock:
        parts = [f"{HOSTS[h][0]} (HTTP {s.get('code')}, {(now - s.get('since', now)) / 3600:.0f}h)"
                 for h, s in _st.items() if h in HOSTS]
    return ("🚧 Blocked sources, paused with back-off: " + ", ".join(parts)) if parts else ""


def attach(state):
    """Load the pauses once after a restart, then keep them in state["_net"] (saved with the bot state)."""
    global _loaded
    with _lock:
        if not _loaded:
            _loaded = True
            for h, s in (state.get("_net") or {}).items():
                if h in HOSTS and isinstance(s, dict):
                    _st[h] = {k: v for k, v in s.items() if k in ("until", "delay", "since", "notified", "day", "code")}
        snap = {h: dict(s) for h, s in _st.items()}
    changed = state.get("_net") != snap
    if snap:
        state["_net"] = snap
    else:
        state.pop("_net", None)
    return changed


def _reset():   # tests
    global _loaded
    with _lock:
        _st.clear()
        _out.clear()
        _mem.clear()
        _loaded = False
