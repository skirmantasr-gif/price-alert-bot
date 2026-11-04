#!/usr/bin/env python3
"""
Stock price alert bot -> Telegram.

Watches tickers (default: 14 tickers, see TICKERS) and sends a Telegram message every
time the price moves THRESHOLD_PCT (default 2%) up or down from the price of the
last alert. After each alert the reference price resets to the current price,
so you get one message per 2% step in either direction.

Standard library only - no pip install needed.

Env vars:
  TELEGRAM_BOT_TOKEN   (required) token from @BotFather
  TELEGRAM_CHAT_ID     (required) your chat id (see README)
  TICKERS              comma list, default "GLXY,CHRS,CAPR,CRTO,KLAR,ZIM,ORCL,BZUN,DXYZ,MIST,IMRX,VRRM,SLS,VNDA"
  THRESHOLD_PCT        default 2
  POLL_SECONDS         default 60
  STATE_FILE           default ./state.json
  NEWS                 "true" (default) = also send news headlines + SEC filings (see news.py)
  NEWS_SECONDS         how often to check news, default 300
  SEC_CONTACT          contact email the SEC requires for EDGAR access; filings are skipped if unset
  SUBSTACK_COOKIE      optional: value of your substack.sid cookie. When set, every new
                       message and reply in your Substack group chats is forwarded too.
  SUBSTACK_CONNECT_SID optional: value of the connect.sid cookie, if your account has one.
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

from news import NewsWatcher

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TICKERS = [t.strip().upper() for t in os.environ.get("TICKERS", "GLXY,CHRS,CAPR,CRTO,KLAR,ZIM,ORCL,BZUN,DXYZ,MIST,IMRX,VRRM,SLS,VNDA").split(",") if t.strip()]
THRESHOLD = float(os.environ.get("THRESHOLD_PCT", "2")) / 100.0
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME_SECONDS", "0"))  # 0 = run forever; set on GitHub Actions
STARTUP_MESSAGE = os.environ.get("STARTUP_MESSAGE", "true").lower() == "true"
ERROR_ALERT_AFTER = 15 * 60  # tell Telegram if a ticker can't be fetched for 15 min

UA = "Mozilla/5.0 (compatible; price-alert-bot/1.0)"
SUBSTACK_COOKIE = os.environ.get("SUBSTACK_COOKIE", "").strip()
if SUBSTACK_COOKIE.startswith("substack.sid="):
    SUBSTACK_COOKIE = SUBSTACK_COOKIE[len("substack.sid="):]
SUBSTACK_CONNECT_SID = os.environ.get("SUBSTACK_CONNECT_SID", "").strip()
if SUBSTACK_CONNECT_SID.startswith("connect.sid="):
    SUBSTACK_CONNECT_SID = SUBSTACK_CONNECT_SID[len("connect.sid="):]
SUBSTACK_API = "https://substack.com/api/v1"
NEWS_ON = os.environ.get("NEWS", "true").lower() == "true"
NEWS_SECONDS = int(os.environ.get("NEWS_SECONDS", "300"))
# Tickers that keep price alerts but get no news / SEC / insider messages
NEWS_EXCLUDE = {t.strip().upper() for t in os.environ.get("NEWS_EXCLUDE", "").split(",") if t.strip()}
NEWS_TICKERS = [t for t in TICKERS if t not in NEWS_EXCLUDE]
TG_LIMIT = 3900  # Telegram max is 4096 chars; leave room for the header

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("bot")


# ---------------------------------------------------------------- HTTP helpers
def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def telegram_call(method, params):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, **params}).encode()
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
                    return telegram_call(method, plain) or "dropped"
                return "dropped" if method == "sendMessage" else False
            time.sleep(wait)
        except Exception as e:
            log.warning("Telegram %s failed (attempt %d): %s", method, attempt + 1, type(e).__name__)
            time.sleep(3 * (attempt + 1))
    return False


def send_telegram(text):
    return telegram_call("sendMessage", {"text": text, "parse_mode": "HTML",
                                         "disable_web_page_preview": "true"})


def send_photo(url):
    return telegram_call("sendPhoto", {"photo": url})


# ---------------------------------------------------------------- price source
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
        price = float(meta["regularMarketPrice"])

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


# ---------------------------------------------------------------- Substack chat
# Logs never contain message text, names or chat ids (the repo and its logs are public),
# and state.json only stores ids/timestamps.
class SubstackAuthError(Exception):
    pass


def substack_get(path):
    req = urllib.request.Request(SUBSTACK_API + path, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept": "application/json",
        "Cookie": f"substack.sid={SUBSTACK_COOKIE}"
                  + (f"; connect.sid={SUBSTACK_CONNECT_SID}" if SUBSTACK_CONNECT_SID else ""),
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 402, 403):
            raise SubstackAuthError(f"HTTP {e.code}; {describe_error(e)}")
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
            parts.append(f"body=SUBSTACK json: {str(msg)[:100]}")
        except Exception:
            t = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
            parts.append("body=" + (f"html title: {' '.join(t.group(1).split())[:80]}" if t else f"text: {' '.join(body.split())[:80]}"))
    return ", ".join(parts)


def substack_channels():
    data = substack_get("/messages/inbox?tab=all")
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
        data = substack_get(f"/community/posts/{thread_id}/comments?{q}")
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


def send_chat_message(header, body, link, pics):
    body = (body or "").strip()
    footer = f'\n\n<a href="{html.escape(link)}">Open in Substack</a>'
    chunks = [html.escape(body[i:i + TG_LIMIT // 2]) for i in range(0, len(body), TG_LIMIT // 2)] or [""]
    for i, chunk in enumerate(chunks):
        text = (header + ("\n\n" + chunk if chunk else "")) if i == 0 else chunk
        if i == len(chunks) - 1:
            text += footer
        if not send_telegram(text):
            return False
    for p in pics:
        send_photo(p)  # best effort; a failed image shouldn't block the queue
    return True


def who(user, role):
    name = html.escape((user or {}).get("name") or "Someone")
    return f"⭐ <b>{name}</b>" if role == "admin" else f"<b>{name}</b>"


def substack_check(state):
    """Forward new threads and replies. Returns True if state changed."""
    sub = state.setdefault("_substack", {})
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    channels = substack_channels()
    events = []  # (created_at, kind, channel_id, thread, comment)
    changed = False

    for cid, cname in channels.items():
        ch = sub.get(cid)
        data = substack_get(f"/chat/channels/{cid}/posts")
        threads = data.get("threads", [])
        if ch is None:  # first time we see this chat: start from now, don't replay history
            sub[cid] = {"last_post": max([t["communityPost"]["created_at"] for t in threads] or [now_iso]),
                        "since": now_iso, "threads": {}}
            changed = True
            log.info("Substack: started watching a chat (%d recent threads)", len(threads))
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
        link = f"https://substack.com/chat/group/{cid}/post/{p['id']}"
        if kind == "thread":
            icon = "📢" if p.get("user_pub_role") == "admin" else "💬"
            header = f"{icon} <b>{cname}</b> · {who(t.get('user'), p.get('user_pub_role'))}"
            ok = send_chat_message(header, p.get("body"), link, images(p))
        else:
            c = r["comment"]
            header = (f"↳ <b>{cname}</b> · {who(r.get('user'), c.get('user_pub_role'))}\n"
                      f"<i>reply to: “{html.escape(snippet(p.get('body')))}”</i>")
            ok = send_chat_message(header, c.get("body"), link, images(c))
        if not ok:
            log.warning("Substack: Telegram send failed, will retry next poll")
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
        log.info("Substack: forwarded %d message(s)", sent)
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
                send_telegram(f"🔌 Phone is charging ({pct}%). Substack forwarding continues.")
            self.warned.clear()
            return
        for lvl in sorted(BATTERY_WARN, reverse=True):
            if pct <= lvl and lvl not in self.warned:
                self.warned.update(l for l in BATTERY_WARN if l >= lvl)
                send_telegram(f"🪫 <b>Phone battery {pct}%</b> — plug it in, or Substack chat "
                              f"forwarding stops when it dies.")
                log.info("Battery warning sent at %d%%", pct)
                break


# ---------------------------------------------------------------- main loop
def fmt_price(p):
    return f"{p:.4f}" if p < 1 else f"{p:.2f}"


def fmt_pct(x):
    return f"{x * 100:+.2f}%"


def check(ticker, state, errors):
    try:
        price, prev_close, session = fetch_price(ticker)
    except Exception as e:
        first = errors.setdefault(ticker, {"since": time.time(), "notified": False})
        log.warning("%s fetch failed: %s", ticker, e)
        if not first["notified"] and time.time() - first["since"] > ERROR_ALERT_AFTER:
            send_telegram(f"⚠️ Can't fetch <b>${ticker}</b> price for 15+ min: {html.escape(str(e))}")
            first["notified"] = True
        return False

    err = errors.pop(ticker, None)
    if err and err["notified"]:
        send_telegram(f"✅ <b>${ticker}</b> price feed recovered: ${fmt_price(price)}")
    s = state.get(ticker)
    if s is None:
        state[ticker] = {"ref": price, "ref_time": datetime.now(timezone.utc).isoformat()}
        log.info("%s baseline set at %.4f", ticker, price)
        return True

    ref = s["ref"]
    change = price / ref - 1
    if abs(change) >= THRESHOLD:
        up = change > 0
        day = f"\nDay: {fmt_pct(price / prev_close - 1)} vs prev close ${fmt_price(prev_close)}" if prev_close else ""
        msg = (f"{'🟢📈' if up else '🔴📉'} <b>${ticker}</b> {'UP' if up else 'DOWN'} {fmt_pct(change)}\n"
               f"Price: <b>${fmt_price(price)}</b> (was ${fmt_price(ref)} at last alert)"
               f"{day}\nSession: {session}")
        if send_telegram(msg):
            state[ticker] = {"ref": price, "ref_time": datetime.now(timezone.utc).isoformat()}
            log.info("ALERT %s %s -> %.4f", ticker, fmt_pct(change), price)
            return True
    else:
        log.debug("%s %.4f (%s from ref)", ticker, price, fmt_pct(change))
    return False


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.")

    state = load_state()
    if not SUBSTACK_COOKIE:
        state.pop("_substack", None)  # Substack runs elsewhere; keep chat ids out of the public state file
    errors = {}
    log.info("Watching %s, threshold %.1f%%, every %ds", TICKERS, THRESHOLD * 100, POLL_SECONDS)

    if STARTUP_MESSAGE:
        lines = []
        for t in TICKERS:
            try:
                p, _, sess = fetch_price(t)
                lines.append(f"${t}: ${fmt_price(p)} ({sess})")
            except Exception as e:
                lines.append(f"${t}: fetch error ({html.escape(str(e))})")
        extra = "\nSubstack chat forwarding: ON" if SUBSTACK_COOKIE else ""
        if NEWS_ON:
            extra += f"\nNews alerts: ON ({NewsWatcher(NEWS_TICKERS, None).sources_label()})"
            if NEWS_EXCLUDE:
                extra += f"\nNo news for: {', '.join(sorted(NEWS_EXCLUDE))}"
            extra += "\n<i>Up/down guesses are automated and can be wrong — not financial advice.</i>"
        send_telegram(f"✅ Price alert bot started. Alerts every ±{THRESHOLD * 100:g}%.{extra}\n" + "\n".join(lines))

    substack_on = bool(SUBSTACK_COOKIE)
    substack_fail_since = None
    if substack_on:
        log.info("Substack chat forwarding enabled")

    news = NewsWatcher(NEWS_TICKERS, send_telegram) if NEWS_ON else None
    last_news = 0

    battery = BatteryWatch()
    started = time.time()
    while True:
        battery.check()
        changed = False
        for t in TICKERS:
            changed |= check(t, state, errors)
        if news and time.time() - last_news >= NEWS_SECONDS:
            last_news = time.time()
            try:
                changed |= news.check(state)
            except Exception as e:
                log.warning("News check failed: %s", type(e).__name__)
        if substack_on:
            try:
                changed |= substack_check(state)
                if substack_fail_since == "notified":
                    send_telegram("✅ Substack chat forwarding recovered.")
                substack_fail_since = None
            except SubstackAuthError as e:
                log.warning("Substack login rejected (%s); forwarding paused until next run", e)
                send_telegram("⚠️ <b>Substack refused the request</b> — chat messages are NOT being forwarded.\n"
                              f"{html.escape(str(e))}")
                substack_on = False
            except Exception as e:
                log.warning("Substack check failed: %s", type(e).__name__)
                if substack_fail_since is None:
                    substack_fail_since = time.time()
                elif substack_fail_since != "notified" and time.time() - substack_fail_since > ERROR_ALERT_AFTER:
                    send_telegram("⚠️ Can't reach Substack chat for 15+ min; will keep trying.")
                    substack_fail_since = "notified"
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
