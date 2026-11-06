"""Shared fake market data for the tests."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def ny(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=NY).timestamp())


def session(day, prices, start=(9, 30)):
    """5-minute regular-session bars for one New York day: prices = list of (open, close)."""
    y, mo, d = day
    t0 = ny(y, mo, d, *start)
    return [(t0 + i * 300, o, c, f"{y:04d}-{mo:02d}-{d:02d}") for i, (o, c) in enumerate(prices)]


def flat(day, price, n=78):
    return session(day, [(price, price)] * n)


def data_of(bars_by_ticker, dollar_vol=5e7, live=False):
    d = {t: {"bars": b, "open_end": None, "live": live, "cur": "USD", "dollar_vol": dollar_vol}
         for t, b in bars_by_ticker.items()}
    return lambda tk: d.get(tk)


def rss(items):
    """Google-News-style RSS: items = [(title, pubDate RFC822)]."""
    body = "".join(f"<item><title>{t}</title><link>https://example.com/{i}</link><pubDate>{d}</pubDate>"
                   f"<description></description></item>" for i, (t, d) in enumerate(items))
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>".encode()
