"""Automatic tests. Run: python -m unittest discover -s tests -t .   (from the repo root; no packages needed)

Rules: no internet (any real network call fails the test), only made-up tickers / headlines (public repo),
no secrets needed. Each test file covers one module."""

import os
import socket
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Modules must start "off" for secrets-based features, whatever the machine's environment says.
for k in ("ALPACA_KEY", "ALPACA_SECRET", "ANTHROPIC_API_KEY", "SEC_CONTACT", "TELEGRAM_BOT_TOKEN", "WATCHLIST",
          "PAPER_CHAT_ID", "BUYOUT", "BUYOUT_CHAT_ID", "CONTRACTS_CHAT_ID"):
    os.environ.pop(k, None)
os.environ["OPTIONS"] = "false"   # options context off by default (test_options switches it on where needed)
os.environ["CONTEXT_CHECK"] = "false"   # context check off by default (test_context switches it on)


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*a, **k):
    raise NetworkBlocked("a test tried to use the internet — mock the fetch function instead")


urllib.request.urlopen = _blocked
socket.create_connection = _blocked
