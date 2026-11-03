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
"""

import html
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("bot")


# ---------------------------------------------------------------- HTTP helpers
def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                if json.loads(r.read().decode()).get("ok"):
                    return True
        except Exception as e:
            log.warning("Telegram send failed (attempt %d): %s", attempt + 1, e)
            time.sleep(3 * (attempt + 1))
    return False


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
        send_telegram(f"✅ Price alert bot started. Alerts every ±{THRESHOLD * 100:g}%.\n" + "\n".join(lines))

    started = time.time()
    while True:
        changed = False
        for t in TICKERS:
            changed |= check(t, state, errors)
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
