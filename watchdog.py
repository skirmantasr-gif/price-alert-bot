"""Server watchdog, run by GitHub Actions every 30 minutes (.github/workflows/watchdog.yml).

The bot on the server writes "... OK at <time>" into the Telegram bot's description every 10 minutes
(extras.Heartbeat). If that time is too old, the server (or the bot on it) has stopped: warn in Telegram.
GitHub runs are stateless, so the warning is sent only in fixed time windows: once when the heartbeat is
40-70 min old, then again about every 6 hours while it stays quiet. Stdlib only, no secrets in the log.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import extras   # _parse_ts: same timestamp format the heartbeat writes

FIRST = 40 * 60          # first warning (cron runs can be late; the heartbeat is every 10 min)
WINDOW = 30 * 60         # one cron interval
REPEAT = 6 * 3600        # remind every 6 h while still quiet


def should_alert(age):
    if age is None or age < FIRST:
        return False
    return (age - FIRST) % REPEAT < WINDOW


def message(age):
    if age is None:
        return None
    h, m = int(age // 3600), int(age % 3600 // 60)
    since = f"{h} h {m} min" if h else f"{m} min"
    return (f"🚨 <b>Server bot hasn't checked in for {since}</b> — price, news and practice alerts are probably DOWN.\n"
            "Check: Hetzner console (is alerts-server running?) or ask Claude to look at the server. "
            "Fallback: re-enable the 'Price alerts' workflow in GitHub Actions.")


def api(token, method, params=None):
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=30) as r:
        return json.load(r).get("result")


def main():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    desc = (api(token, "getMyDescription") or {}).get("description", "")
    ts = extras._parse_ts(desc)
    age = None if ts is None else time.time() - ts
    print("heartbeat age:", "unknown" if age is None else f"{int(age // 60)} min")
    if should_alert(age):
        api(token, "sendMessage", {"chat_id": chat, "text": message(age), "parse_mode": "HTML"})
        print("warning sent")


if __name__ == "__main__":
    main()
