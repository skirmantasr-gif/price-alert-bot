#!/usr/bin/env python3
"""
Stock price alert bot -> Telegram.

Watches the stocks in the WATCHLIST secret (see watchlist.py) and sends a Telegram message every
time the price moves the stock's threshold (default 2%) up or down from the price of the
last alert. After each alert the reference price resets to the current price,
so you get one message per step in either direction. Also: trading halts, unusual volume,
watchdog + daily status, catalyst calendar (see extras.py) and news (see news.py).

Standard library only - no pip install needed.

Env vars:
  TELEGRAM_BOT_TOKEN   (required) token from @BotFather
  TELEGRAM_CHAT_ID     (required) your chat id (see README)
  WATCHLIST            JSON with stocks, per-stock thresholds and news settings (see watchlist.py)
  TICKERS              comma list, only used when WATCHLIST is not set
  THRESHOLD_PCT        default 2 (per-stock values in WATCHLIST override it)
  HEARTBEAT_ROLE       "github" or "phone": enables the watchdog (each side checks the other)
  POLL_SECONDS         default 60
  STATE_FILE           default ./state.json
  NEWS                 "true" (default) = also send news headlines + SEC filings (see news.py)
  NEWS_SECONDS         how often to check news, default 300
  SEC_CONTACT          contact email the SEC requires for EDGAR access; filings are skipped if unset
  THIRDPARTY_API, THIRDPARTY_SITE, THIRDPARTY_COOKIE_NAME, THIRDPARTY_COOKIE
                       optional third-party chat source (set only on the phone). When set, every new
                       message and reply in your group chats there is forwarded too.
  THIRDPARTY_CONNECT_SID optional second session cookie, if your account has one.
  CONTRACTS            "true" (default) = also scan 15 markets for contract wins by any small/mid cap
                       (see contracts.py; needs ANTHROPIC_API_KEY). CONTRACTS_CHAT_ID = optional separate channel.
"""

import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    import alpaca
except ImportError:  # e.g. an older phone copy without alpaca.py
    class alpaca:  # noqa: N801
        ON = False
        recent = staticmethod(lambda: [])
        selftest = staticmethod(lambda: "OFF (alpaca.py missing)")
import buyout
try:
    import earnings
except ImportError:  # phone copy without earnings.py
    earnings = None
try:
    import insiders
except ImportError:  # phone copy without insiders.py
    insiders = None
try:
    import weekly
except ImportError:  # phone copy without weekly.py
    weekly = None
try:
    import movers
except ImportError:  # phone copy without movers.py
    movers = None
try:
    import mood
except ImportError:  # phone copy without mood.py
    mood = None
import contracts
import extras
import insights
try:
    import learn
except ImportError:  # phone copy without learn.py
    learn = None
import watchlist
from news import NewsWatcher
from news import sec_cik as news_sec_cik

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CONTRACTS_CHAT_ID = os.environ.get("CONTRACTS_CHAT_ID", "").strip() or CHAT_ID
BUYOUT_CHAT_ID = os.environ.get("BUYOUT_CHAT_ID", "").strip()  # buyout watch is off without its own channel
PAPER_CHAT_ID = os.environ.get("PAPER_CHAT_ID", "").strip()    # 🧪 practice portfolio channel (else: main, report only)
CONTRACT_SCAN_SECONDS = int(os.environ.get("CONTRACT_SCAN_SECONDS", "60"))  # one market per scan
TICKERS = watchlist.TICKERS
THRESHOLDS = watchlist.THRESHOLDS  # ticker -> fraction, e.g. 0.02
# First minutes after the 9:30 ET open swing back and forth (2026-11-09: one stock -2.3% then +2.5% within 3 min):
# hold ±step alerts that long, unless the move is already OPEN_BIG_X times the step.
OPEN_HOLD = int(os.environ.get("OPEN_HOLD_MINUTES", "15")) * 60
OPEN_BIG_X = 3
HEARTBEAT_ROLE = os.environ.get("HEARTBEAT_ROLE", "").strip().lower()
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME_SECONDS", "0"))  # 0 = run forever; set on GitHub Actions
STARTUP_MESSAGE = os.environ.get("STARTUP_MESSAGE", "true").lower() == "true"
ERROR_ALERT_AFTER = 15 * 60  # tell Telegram if a ticker can't be fetched for 15 min

UA = "Mozilla/5.0 (compatible; price-alert-bot/1.0)"
TP_API = os.environ.get("THIRDPARTY_API", "").strip().rstrip("/")
TP_SITE = os.environ.get("THIRDPARTY_SITE", "").strip().rstrip("/")
TP_COOKIE_NAME = os.environ.get("THIRDPARTY_COOKIE_NAME", "").strip()
TP_COOKIE = os.environ.get("THIRDPARTY_COOKIE", "").strip() if TP_API else ""
if TP_COOKIE_NAME and TP_COOKIE.startswith(TP_COOKIE_NAME + "="):
    TP_COOKIE = TP_COOKIE[len(TP_COOKIE_NAME) + 1:]
TP_CONNECT_SID = os.environ.get("THIRDPARTY_CONNECT_SID", "").strip()
if TP_CONNECT_SID.startswith("connect.sid="):
    TP_CONNECT_SID = TP_CONNECT_SID[len("connect.sid="):]
NEWS_ON = os.environ.get("NEWS", "true").lower() == "true"
NEWS_SECONDS = int(os.environ.get("NEWS_SECONDS", "300"))
# Tickers that keep price alerts but get no news / SEC / insider messages
NEWS_EXCLUDE = watchlist.NEWS_EXCLUDE
NEWS_TICKERS = [t for t in TICKERS if t not in NEWS_EXCLUDE]
TG_LIMIT = 3900  # Telegram max is 4096 chars; leave room for the header

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("bot")
log.addHandler(extras.FAILS)   # counts warnings per day for the ☀️ daily check


# ---------------------------------------------------------------- HTTP helpers
def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def telegram_api(method, params):
    """Bot-level call (no chat). Returns the result or None; never logs details."""
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode())
            return body.get("result") if body.get("ok") else None
    except Exception as e:
        log.warning("Telegram %s failed: %s", method, type(e).__name__)
        return None


def telegram_call(method, params, chat=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode({"chat_id": chat or CHAT_ID, **params}).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                if json.loads(r.read().decode()).get("ok"):
                    return True
        except urllib.error.HTTPError as e:
            wait = 3 * (attempt + 1)
            try:
                body = json.loads(e.read().decode())
                wait = max(wait, int(body.get("parameters", {}).get("retry_after", 0)) + 1)
                reason = body.get("description", "")
            except Exception:
                reason = ""
            log.warning("Telegram %s failed (attempt %d): HTTP %s %s", method, attempt + 1, e.code, reason)
            if e.code == 400:  # bad request won't succeed on retry
                if params.get("parse_mode") == "HTML":  # fall back to plain text once
                    plain = dict(params)
                    plain.pop("parse_mode")
                    plain["text"] = html.unescape(re.sub(r"<[^>]+>", "", params.get("text", "")))
                    return telegram_call(method, plain, chat) or "dropped"
                return "dropped" if method == "sendMessage" else False
            time.sleep(wait)
        except Exception as e:
            log.warning("Telegram %s failed (attempt %d): %s", method, attempt + 1, type(e).__name__)
            time.sleep(3 * (attempt + 1))
    return False


def send_telegram(text, silent=None, chat=None):
    """silent=None: decide from the message type (routine news / filings arrive without sound)."""
    if silent is None:
        silent = extras.is_quiet(text)
    params = {"text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if silent:
        params["disable_notification"] = "true"
    return telegram_call("sendMessage", params, chat)


def send_photo(url):
    return telegram_call("sendPhoto", {"photo": url, "disable_notification": "true"})


# ---------------------------------------------------------------- price source
LAST_CHART = {}  # ticker -> last 1-minute chart (used for the volume check)


def fetch_price(ticker):
    """
    Latest price incl. pre-market / after-hours, from Yahoo Finance chart API.
    Returns (price, previous_close, session_label).
    """
    q = urllib.parse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{q}"
           f"?interval=1m&range=1d&includePrePost=true")
    data = http_get_json(url)
    result = data["chart"]["result"][0]
    LAST_CHART[ticker] = result
    meta = result["meta"]
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")

    price, ts = None, None
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    stamps = result.get("timestamp") or []
    for t, c in zip(reversed(stamps), reversed(closes)):
        if c is not None:
            price, ts = float(c), t
            break
    if price is None:
        # No trade yet in today's chart (e.g. right at 4am ET pre-market open): Yahoo's regularMarketPrice is
        # then yesterday's close, which would trigger a false alert against an after-hours reference.
        price = float(meta["regularMarketPrice"])
        return price, (float(prev_close) if prev_close else None), "stale"

    session = "regular"
    periods = meta.get("currentTradingPeriod") or {}
    if ts is not None:
        pre, reg, post = periods.get("pre", {}), periods.get("regular", {}), periods.get("post", {})
        if pre and pre.get("start", 0) <= ts < pre.get("end", 0):
            session = "pre-market"
        elif post and post.get("start", 0) <= ts < post.get("end", 0):
            session = "after-hours"
        elif reg and not (reg.get("start", 0) <= ts < reg.get("end", 0)):
            session = "closed"
    return price, (float(prev_close) if prev_close else None), session


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- Third-party chat
# Logs never contain message text, names or chat ids (the repo and its logs are public),
# and state.json only stores ids/timestamps.
class ThirdPartyAuthError(Exception):
    pass


def thirdparty_get(path):
    req = urllib.request.Request(TP_API + path, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept": "application/json",
        "Cookie": f"{TP_COOKIE_NAME}={TP_COOKIE}"
                  + (f"; connect.sid={TP_CONNECT_SID}" if TP_CONNECT_SID else ""),
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 402, 403):
            raise ThirdPartyAuthError(f"HTTP {e.code}; {describe_error(e)}")
        raise


def describe_error(e):
    """Short, non-sensitive description of an error response, for the public log."""
    try:
        body = e.read(5000).decode(errors="replace")
    except Exception:
        body = ""
    h = e.headers
    parts = [f"server={h.get('server')}", f"type={(h.get('content-type') or '').split(';')[0]}"]
    if h.get("cf-mitigated"):
        parts.append(f"cf-mitigated={h.get('cf-mitigated')}")
    low = body.lower()
    if any(k in low for k in ("just a moment", "attention required", "cf-chl", "challenge-platform", "cloudflare ray id")):
        parts.append("body=CLOUDFLARE block page")
    else:
        try:
            j = json.loads(body)
            msg = j.get("errors", [{}])[0].get("msg") if isinstance(j.get("errors"), list) else (j.get("error") or j.get("status"))
            parts.append(f"body=API json: {str(msg)[:100]}")
        except Exception:
            t = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
            parts.append("body=" + (f"html title: {' '.join(t.group(1).split())[:80]}" if t else f"text: {' '.join(body.split())[:80]}"))
    return ", ".join(parts)


def thirdparty_channels():
    data = thirdparty_get("/messages/inbox?tab=all")
    out = {}
    for t in data.get("threads", []):
        ch = t.get("channel") or {}
        if t.get("type") == "chat" and ch.get("id"):
            out[ch["id"]] = ch.get("name") or "Chat"
    return out


def comments_since(thread_id, since):
    """Replies in a thread created strictly after `since` (ISO string), oldest first."""
    out, cursor = [], since
    for _ in range(20):
        q = "order=asc&" + (f"after={urllib.parse.quote(cursor)}" if cursor else "initial=true")
        data = thirdparty_get(f"/community/posts/{thread_id}/comments?{q}")
        batch = [r for r in data.get("replies", []) if r["comment"]["created_at"] > (since or "")]
        new = [r for r in batch if r["comment"]["created_at"] > (cursor or "")]
        out.extend(new)
        if not data.get("moreAfter") or not new:
            break
        cursor = new[-1]["comment"]["created_at"]
    seen, uniq = set(), []
    for r in out:
        if r["comment"]["id"] not in seen:
            seen.add(r["comment"]["id"])
            uniq.append(r)
    return uniq


def snippet(text, n=60):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def images(item):
    return [m["url"] for m in (item.get("mediaAttachments") or [])
            if m.get("type") == "image" and m.get("url")]


_ticker_cache = {}


def send_chat_message(header, body, link, pics, loud=True):
    """loud: posts by the chat's author ring; other members' posts arrive silently."""
    body = (body or "").strip()
    prices = ""
    try:
        prices = extras.price_context(body, fetch_price, _ticker_cache)
    except Exception as e:
        log.warning("Price context failed: %s", type(e).__name__)
    footer = (f"\n\n{prices}" if prices else "") + f'\n\n<a href="{html.escape(link)}">Open in Third party</a>'
    chunks = [html.escape(body[i:i + TG_LIMIT // 2]) for i in range(0, len(body), TG_LIMIT // 2)] or [""]
    for i, chunk in enumerate(chunks):
        text = (header + ("\n\n" + chunk if chunk else "")) if i == 0 else chunk
        if i == len(chunks) - 1:
            text += footer
        if not send_telegram(text, silent=not (loud and i == 0)):
            return False
    for p in pics:
        send_photo(p)  # best effort; a failed image shouldn't block the queue
    return True


def who(user, role):
    name = html.escape((user or {}).get("name") or "Someone")
    return f"⭐ <b>{name}</b>" if role == "admin" else f"<b>{name}</b>"


def thirdparty_check(state):
    """Forward new threads and replies. Returns True if state changed."""
    sub = state.setdefault("_thirdparty", {})
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    channels = thirdparty_channels()
    events = []  # (created_at, kind, channel_id, thread, comment)
    changed = False

    for cid, cname in channels.items():
        ch = sub.get(cid)
        data = thirdparty_get(f"/chat/channels/{cid}/posts")
        threads = data.get("threads", [])
        if ch is None:  # first time we see this chat: start from now, don't replay history
            sub[cid] = {"last_post": max([t["communityPost"]["created_at"] for t in threads] or [now_iso]),
                        "since": now_iso, "threads": {}}
            changed = True
            log.info("Third party: started watching a chat (%d recent threads)", len(threads))
            continue
        tracked = ch["threads"]
        for t in sorted(threads, key=lambda t: t["communityPost"]["created_at"]):
            p = t["communityPost"]
            if p.get("status") in ("deleted", "removed"):
                continue
            is_new = p["created_at"] > ch["last_post"]
            if is_new:
                events.append((p["created_at"], "thread", cid, t, None))
            seen = tracked.get(p["id"]) or (p["created_at"] if is_new else ch["since"])
            last_c = p.get("max_comment_created_at") or p.get("most_recent_comment_created_at")
            if last_c and last_c > seen:
                for r in comments_since(p["id"], seen):
                    if r["comment"].get("status") not in ("deleted", "removed"):
                        events.append((r["comment"]["created_at"], "reply", cid, t, r))
        # forget threads that dropped off the recent list
        keep = {t["communityPost"]["id"] for t in threads}
        for tid in list(tracked):
            if tid not in keep:
                del tracked[tid]
                changed = True

    events.sort(key=lambda e: e[0])
    sent = 0
    for created, kind, cid, t, r in events:
        p = t["communityPost"]
        ch = sub[cid]
        cname = html.escape(channels[cid])
        link = f"{TP_SITE}/chat/group/{cid}/post/{p['id']}"
        if kind == "thread":
            icon = "📢" if p.get("user_pub_role") == "admin" else "💬"
            header = f"{icon} <b>{cname}</b> · {who(t.get('user'), p.get('user_pub_role'))}"
            ok = send_chat_message(header, p.get("body"), link, images(p), loud=p.get("user_pub_role") == "admin")
        else:
            c = r["comment"]
            header = (f"↳ <b>{cname}</b> · {who(r.get('user'), c.get('user_pub_role'))}\n"
                      f"<i>reply to: “{html.escape(snippet(p.get('body')))}”</i>")
            ok = send_chat_message(header, c.get("body"), link, images(c), loud=c.get("user_pub_role") == "admin")
        if not ok:
            log.warning("Third party: Telegram send failed, will retry next poll")
            break
        sent += 1
        changed = True
        if kind == "thread":
            ch["last_post"] = max(ch["last_post"], created)
            ch["threads"].setdefault(p["id"], created)
        else:
            ch["threads"][p["id"]] = max(ch["threads"].get(p["id"], ""), created)
        time.sleep(1.1)  # stay under Telegram's per-channel rate limit
    if sent:
        log.info("Third party: forwarded %d message(s)", sent)
    return changed


# ---------------------------------------------------------------- phone battery
# Only active on Android/Termux with the Termux:API app installed; silently off elsewhere (e.g. GitHub).
BATTERY_WARN = [int(x) for x in os.environ.get("BATTERY_WARN", "25,10").split(",") if x.strip()]
BATTERY_SECONDS = 600


def battery_status():
    exe = shutil.which("termux-battery-status")
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=30).stdout
        d = json.loads(out)
        return int(d["percentage"]), d.get("plugged", "UNPLUGGED") != "UNPLUGGED" or d.get("status") == "CHARGING"
    except Exception:
        return None


class BatteryWatch:
    def __init__(self):
        self.on = bool(shutil.which("termux-battery-status"))
        self.last = 0
        self.warned = set()

    def check(self):
        if not self.on or time.time() - self.last < BATTERY_SECONDS:
            return
        self.last = time.time()
        st = battery_status()
        if st is None:
            return
        pct, charging = st
        if charging:
            if self.warned:
                send_telegram(f"🔌 Phone is charging ({pct}%). Third-party chat forwarding continues.")
            self.warned.clear()
            return
        for lvl in sorted(BATTERY_WARN, reverse=True):
            if pct <= lvl and lvl not in self.warned:
                self.warned.update(l for l in BATTERY_WARN if l >= lvl)
                send_telegram(f"🪫 <b>Phone battery {pct}%</b> — plug it in, or third-party chat "
                              f"forwarding stops when it dies.")
                log.info("Battery warning sent at %d%%", pct)
                break


# ---------------------------------------------------------------- main loop
def fmt_price(p):
    return f"{p:.4f}" if p < 1 else f"{p:.2f}"


def fmt_pct(x):
    return f"{x * 100:+.2f}%"


def in_open_hold(ticker, now=None):
    reg = (((LAST_CHART.get(ticker) or {}).get("meta") or {}).get("currentTradingPeriod") or {}).get("regular") or {}
    start = reg.get("start")
    now = now or time.time()
    return bool(start) and start <= now < start + OPEN_HOLD


def check(ticker, state, errors, volume=None):
    try:
        price, prev_close, session = fetch_price(ticker)
    except Exception as e:
        first = errors.setdefault(ticker, {"since": time.time(), "notified": False})
        log.warning("Price fetch failed for a ticker: %s", type(e).__name__)  # no ticker in the public log
        if not first["notified"] and time.time() - first["since"] > ERROR_ALERT_AFTER:
            send_telegram(f"⚠️ Can't fetch <b>${ticker}</b> price for 15+ min: {html.escape(str(e))}")
            first["notified"] = True
        return False

    err = errors.pop(ticker, None)
    if err and err["notified"]:
        send_telegram(f"✅ <b>${ticker}</b> price feed recovered: ${fmt_price(price)}")
    changed = False
    if volume and session != "stale" and ticker in LAST_CHART:
        try:
            changed |= volume.check(ticker, LAST_CHART[ticker], price, prev_close, state)
        except Exception as e:
            log.warning("Volume check failed: %s", type(e).__name__)
    s = state.get(ticker)
    if session == "stale" and s is not None:
        return changed  # no fresh trade yet; wait for a real one
    if s is None:
        state[ticker] = {"ref": price, "ref_time": datetime.now(timezone.utc).isoformat()}
        log.info("Baseline set for a ticker")
        return True

    ref = s["ref"]
    change = price / ref - 1
    step = THRESHOLDS.get(ticker, 0.02)
    if session == "regular" and abs(change) < OPEN_BIG_X * step and in_open_hold(ticker):
        return changed  # opening swings: compare again after the hold (the reference price stays)
    if abs(change) >= step:
        up = change > 0
        day = f"\nDay: {fmt_pct(price / prev_close - 1)} vs prev close ${fmt_price(prev_close)}" if prev_close else ""
        msg = (f"{'🟢📈' if up else '🔴📉'} <b>${ticker}</b> {'UP' if up else 'DOWN'} {fmt_pct(change)}\n"
               f"Price: <b>${fmt_price(price)}</b> (was ${fmt_price(ref)} at last alert)"
               f"{day}\nSession: {session}")
        msg = "\n".join([msg] + extras.related(ticker))
        if send_telegram(msg):
            state[ticker] = {"ref": price, "ref_time": datetime.now(timezone.utc).isoformat()}
            extras.bump(state, "price")
            log.info("Price alert sent")
            return True
    return changed


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.")

    state = load_state()
    if not TP_COOKIE:
        state.pop("_thirdparty", None)  # chat forwarding runs elsewhere; keep chat ids out of the saved state
    errors = {}
    log.info("Watching %d tickers, every %ds", len(TICKERS), POLL_SECONDS)  # tickers stay out of the public log
    if watchlist.ERROR or (HEARTBEAT_ROLE == "github" and not TICKERS):
        send_telegram("⚠️ <b>No stocks to watch</b> — " + html.escape(
            watchlist.ERROR or "the WATCHLIST secret is missing or empty") + ". Price and news alerts are off.")

    if STARTUP_MESSAGE:
        lines = []
        for t in TICKERS:
            step = f" · alerts every ±{THRESHOLDS[t] * 100:g}%"
            try:
                p, _, sess = fetch_price(t)
                lines.append(f"${t}: ${fmt_price(p)} ({sess}){step}")
            except Exception as e:
                lines.append(f"${t}: fetch error ({html.escape(str(e))})")
        extra = "\nThird-party chat forwarding: ON" if TP_COOKIE else ""
        if NEWS_ON:
            extra += f"\nNews alerts: ON ({NewsWatcher(NEWS_TICKERS, None).sources_label()})"
            if NEWS_EXCLUDE:
                extra += f"\nNo news for: {', '.join(sorted(NEWS_EXCLUDE))}"
            extra += "\n<i>Up/down guesses are automated and can be wrong — not financial advice.</i>"
        if TICKERS:
            extra += "\nAlso: trading halts ⏸️, unusual volume 📊, catalyst calendar 📅, daily check ☀️"
        if HEARTBEAT_ROLE != "phone" and insights.HourlyInsights.enabled(TICKERS):
            extra += "\nHourly outlook 🧭: ON (10:00–16:00 New York, market days)"
        if HEARTBEAT_ROLE != "phone" and buyout.ON:
            extra += "\nBuyout watch 🎯: " + (buyout.BuyoutWatch.label() if buyout.BuyoutWatch.enabled() and BUYOUT_CHAT_ID
                                              else "OFF (" + (buyout.ERROR or "BUYOUT or BUYOUT_CHAT_ID secret not set") + ")")
        if learn and HEARTBEAT_ROLE != "phone" and learn.Reviewer.enabled() and TICKERS:
            extra += "\nLearning 🎓: ON (guesses checked against real prices, review every 2 days)"
            if learn.paper and learn.paper.Tracker.enabled():
                extra += ("\nPractice portfolio 🧪: ON (pretend €100 per strategy" +
                          (", hourly trade updates while the market is open + daily report → separate channel)"
                           if PAPER_CHAT_ID else ", daily report here)"))
        if HEARTBEAT_ROLE != "phone":
            extra += "\nReal-time news ⚡: " + alpaca.selftest()
        if earnings and HEARTBEAT_ROLE != "phone" and earnings.ON:
            extra += "\nEarnings patterns 📊: " + (earnings.EarningsWatch.label() if earnings.EarningsWatch.enabled()
                                                   else "OFF (needs SEC_CONTACT)")
        if weekly and HEARTBEAT_ROLE != "phone" and weekly.ON:
            extra += f"\nWeekly review 📅: ON (Sundays {weekly.WEEKLY_TIME} LT)"
        if insiders and HEARTBEAT_ROLE != "phone" and insiders.ON:
            extra += "\nInsider cluster buys 👥: " + (insiders.InsiderWatch.label() if insiders.InsiderWatch.enabled()
                                                     else "OFF (needs SEC_CONTACT)")
        if movers and HEARTBEAT_ROLE != "phone" and movers.MoversWatch.enabled():
            extra += "\nWinners & losers 🏆: " + movers.MoversWatch.label() + (
                " → contracts channel" if CONTRACTS_CHAT_ID != CHAT_ID else "")
        if HEARTBEAT_ROLE != "phone" and contracts.ON:
            extra += ("\nContract scan 🏛️: " + (contracts.ContractScanner.label()
                      + (" → separate channel" if CONTRACTS_CHAT_ID != CHAT_ID else "")
                      if contracts.ContractScanner.enabled() else "OFF (needs ANTHROPIC_API_KEY)"))
        send_telegram(f"✅ Price alert bot started.{extra}\n" + "\n".join(lines))

    thirdparty_on = bool(TP_COOKIE)
    thirdparty_fail_since = None
    if thirdparty_on:
        log.info("Third-party chat forwarding enabled")

    news = NewsWatcher(NEWS_TICKERS, send_telegram) if NEWS_ON else None
    last_news = 0
    scanner = None
    if HEARTBEAT_ROLE != "phone" and contracts.ContractScanner.enabled():
        scanner = contracts.ContractScanner(lambda text, silent: send_telegram(text, silent, CONTRACTS_CHAT_ID),
                                            skip_symbols=TICKERS)
    last_scan = 0

    battery = BatteryWatch()
    halts = extras.HaltWatcher(TICKERS, send_telegram)
    volume = extras.VolumeWatcher(http_get_json, send_telegram)
    bo_watch = (buyout.BuyoutWatch(lambda text, silent: send_telegram(text, silent, BUYOUT_CHAT_ID))
                if HEARTBEAT_ROLE != "phone" and BUYOUT_CHAT_ID and buyout.BuyoutWatch.enabled() else None)
    mv_watch = (movers.MoversWatch(lambda text, silent: send_telegram(text, silent, CONTRACTS_CHAT_ID), TICKERS)
                if movers and HEARTBEAT_ROLE != "phone" and movers.MoversWatch.enabled() else None)
    earn_watch = (earnings.EarningsWatch(send_telegram, TICKERS, news_sec_cik)
                  if earnings and HEARTBEAT_ROLE != "phone" and earnings.EarningsWatch.enabled() else None)
    ins_watch = (insiders.InsiderWatch(send_telegram, lambda text, silent: send_telegram(text, silent, CONTRACTS_CHAT_ID),
                                       TICKERS)
                 if insiders and HEARTBEAT_ROLE != "phone" and insiders.InsiderWatch.enabled() else None)
    outlook = (insights.HourlyInsights(TICKERS, LAST_CHART, volume, send_telegram)
               if HEARTBEAT_ROLE != "phone" and insights.HourlyInsights.enabled(TICKERS) else None)
    heartbeat = extras.Heartbeat(
        HEARTBEAT_ROLE, telegram_api, send_telegram, thirdparty_ok=lambda: thirdparty_on,
        daily_extra=(lambda st: ([mood.line(mood.get(st, http_get_json))] if mood else []) + [extras.fail_line(st)])
        if HEARTBEAT_ROLE != "phone" else None)
    reviewer = None
    if learn and HEARTBEAT_ROLE != "phone" and learn.Reviewer.enabled() and TICKERS:
        learn.CHARTS = LAST_CHART
        learn.Reviewer.attach(state)
        reviewer = learn.Reviewer(http_get_json, send_telegram)
    practice = None
    if reviewer and learn.paper and learn.paper.Tracker.enabled():
        practice = learn.paper.Tracker(http_get_json, lambda text, silent=True: send_telegram(text, silent, PAPER_CHAT_ID or None),
                                       live_trades=bool(PAPER_CHAT_ID))
        practice.attach(state)  # before any alert is sent, so the first calls are traded too
    wk_review = (weekly.WeeklyReview(send_telegram, practice)
                 if weekly and HEARTBEAT_ROLE != "phone" and weekly.WeeklyReview.enabled() else None)
    started = time.time()
    while True:
        battery.check()
        changed = False
        for t in TICKERS:
            changed |= check(t, state, errors, volume)
        checks = [("Halt check", halts.check), ("Watchdog", heartbeat.check)]
        if bo_watch:
            checks.append(("Buyout watch", bo_watch.check))
        if outlook:
            checks.append(("Hourly outlook", outlook.check))
        if mv_watch:
            checks.append(("Winners & losers", mv_watch.check))
        if earn_watch:
            checks.append(("Earnings patterns", earn_watch.check))
        if ins_watch:
            checks.append(("Insider clusters", ins_watch.check))
        if wk_review:
            checks.append(("Weekly review", wk_review.check))
        if reviewer:
            checks.append(("Learning review", reviewer.check))
        if practice:
            checks.append(("Practice portfolio", practice.check))
        for name, fn in checks:
            try:
                changed |= fn(state)
            except Exception as e:
                log.warning("%s failed: %s", name, type(e).__name__)
        if news and time.time() - last_news >= NEWS_SECONDS:
            last_news = time.time()
            try:
                changed |= news.check(state)
            except Exception as e:
                log.warning("News check failed: %s", type(e).__name__)
        elif news and alpaca.ON and HEARTBEAT_ROLE != "phone":  # real-time feed between the full rounds
            try:
                changed |= news.check(state, fast=True)
            except Exception as e:
                log.warning("Fast news check failed: %s", type(e).__name__)
        if scanner and time.time() - last_scan >= CONTRACT_SCAN_SECONDS:
            last_scan = time.time()
            try:
                changed |= scanner.check(state)
            except Exception as e:
                log.warning("Contract scan failed: %s", type(e).__name__)
        if thirdparty_on:
            try:
                changed |= thirdparty_check(state)
                if thirdparty_fail_since == "notified":
                    send_telegram("✅ Third-party chat forwarding recovered.")
                thirdparty_fail_since = None
            except ThirdPartyAuthError as e:
                log.warning("Third party login rejected (%s); forwarding paused until next run", e)
                send_telegram("⚠️ <b>Third party refused the request</b> — chat messages are NOT being forwarded.\n"
                              f"{html.escape(str(e))}")
                thirdparty_on = False
            except Exception as e:
                log.warning("Third party check failed: %s", type(e).__name__)
                if thirdparty_fail_since is None:
                    thirdparty_fail_since = time.time()
                elif thirdparty_fail_since != "notified" and time.time() - thirdparty_fail_since > ERROR_ALERT_AFTER:
                    send_telegram("⚠️ Can't reach the third-party chat for 15+ min; will keep trying.")
                    thirdparty_fail_since = "notified"
        changed |= extras.FAILS.flush_to(state)
        if changed:
            save_state(state)
        if MAX_RUNTIME and time.time() - started + POLL_SECONDS >= MAX_RUNTIME:
            break
        time.sleep(POLL_SECONDS)

    # Heartbeat so the repo gets a commit each run (GitHub pauses schedules on repos idle for 60 days).
    state["_last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    log.info("Max runtime reached, exiting (next scheduled run takes over).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
