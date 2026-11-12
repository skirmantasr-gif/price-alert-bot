"""📅 Weekly review (Sunday, WEEKLY_TIME owner's time zone, main channel): one quiet message that pulls the week
together, so the owner doesn't have to scroll five channels:
  - 🧪 practice portfolios: value, change this week, trades closed this week, vs the S&P
  - 💶 the owner's real-money rule (paper.money_rule)
  - 🎓 the bot's up/down calls made this week that are already checked: right / wrong / flat per kind
  - counts of this week's 📣 public sale signals and 👥 insider clusters
  - a reminder to ask Claude for a full check (/alerts-check)
GitHub only; needs the practice tracker (prices) for the portfolio part."""
import logging
import os
import time
from datetime import timedelta

import extras

log = logging.getLogger("bot")
ON = os.environ.get("WEEKLY_REVIEW", "true").lower() == "true"
WEEKLY_TIME = os.environ.get("WEEKLY_TIME", "10:00")
FLAT = 1.0


def _calls(state, since):
    """{src: [right, wrong, flat, sum of % moves the called way]} for calls made since `since` and already checked."""
    out = {}
    for p in state.get("_learn", {}).get("p", []):
        if p.get("t", 0) < since or "r" not in p:
            continue
        s = p["r"] if p["d"] == "up" else -p["r"]
        g = out.setdefault(p["src"], [0, 0, 0, 0.0])
        g[0 if s > FLAT else 1 if s < -FLAT else 2] += 1
        g[3] += s
    return out


def _count_since(d, day):
    """Values that are dates (YYYY-MM-DD, or lists starting with one) on/after day."""
    n = 0
    for v in (d or {}).values():
        v = v[0] if isinstance(v, list) and v else v
        if isinstance(v, str) and v >= day:
            n += 1
    return n


def message(state, now, data, paper=None, learn_names=None):
    loc = extras.local_now()
    start = (loc - timedelta(days=6)).strftime("%b %d")
    lines = [f"📅 <b>Weekly review</b> · {start} – {loc.strftime('%b %d')}"]
    wk = state.setdefault("_wk", {})
    prev = wk.get("snap", {})
    snap = {}
    st = state.get("_paper")
    if paper and st and st.get("pf"):
        lines.append("<b>🧪 Practice portfolios</b> (pretend €100 each)")
        week_ago = now - 7 * 86400
        for key, (name, _) in paper.PORTFOLIOS.items():
            pf = st["pf"].get(key)
            if not pf:
                continue
            v = paper.value(pf, now, data)
            snap[key] = round(v, 2)
            ch = f" ({v - prev[key]:+.2f} this week)" if key in prev else ""
            wk_closed = [c for c in pf["closed"] if c.get("t", 0) >= week_ago]
            won = sum(1 for c in wk_closed if c["ret"] > 0)
            closed = (f" · {len(wk_closed)} closed this week ({won} won, avg "
                      f"{sum(c['ret'] for c in wk_closed) / len(wk_closed) * 100:+.1f}%)" if wk_closed else
                      " · no trades closed this week")
            lines.append(f"{name}: €{v:.2f}{ch}{closed}")
        bv = paper._bench(st, now, data)
        if bv:
            snap["bench"] = round(bv, 2)
            ch = f" ({bv - prev['bench']:+.2f} this week)" if "bench" in prev else ""
            lines.append(f"📊 S&P 500: €{bv:.2f}{ch} — the number to beat")
        try:
            lines.append(paper.money_rule(st, now, data))
        except Exception as e:
            log.warning("Weekly review: money rule failed (%s)", type(e).__name__)
    calls = _calls(state, now - 7 * 86400)
    if calls:
        lines.append("<b>🎓 This week's up/down calls already checked</b> (right = moved >1% the called way)")
        for src, (r, w, f, s) in sorted(calls.items(), key=lambda kv: -sum(kv[1][:3])):
            n = r + w + f
            lines.append(f"• {(learn_names or {}).get(src, src)}: {r} of {n} right · {w} wrong · {f} flat · "
                         f"avg {s / n:+.1f}% the called way")
    else:
        lines.append("🎓 No up/down calls from this week checked yet.")
    day = (loc - timedelta(days=6)).strftime("%Y-%m-%d")
    sig = _count_since(state.get("_bo", {}).get("sig"), day)
    ins = _count_since(state.get("_insb", {}).get("posted"), day)
    lines.append(f"📣 Public sale signals: {sig} · 👥 insider clusters: {ins} (scored in the 🎓 review)")
    bo = state.get("_bo") or {}
    p = bo.get("perf")
    if p:
        lines.append(f"🎯 Buyout watch ({len(bo.get('cands', {}))} candidates): average {p['avg'] * 100:+.1f}% since "
                     f"added vs their market {p['bench'] * 100:+.1f}% · {p['ahead']} of {p['n']} ahead. Switch-off "
                     "test mid-February: no deal, not ahead of the market → stop it.")
    lines.append("<i>One week is far too short to judge anything: watch the trend over 4–6 weeks. For a full "
                 "health check, ask Claude for /alerts-check. Not advice.</i>")
    wk["snap"] = snap or prev
    return "\n".join(lines)


class WeeklyReview:
    def __init__(self, send, practice=None):
        self.send = send            # send(text, silent)
        self.practice = practice    # paper.Tracker (its .data gives prices) or None

    @staticmethod
    def enabled():
        return ON

    def check(self, state):
        loc = extras.local_now()
        week = loc.strftime("%G-%V")
        wk = state.setdefault("_wk", {})
        if loc.weekday() != 6 or loc.strftime("%H:%M") < WEEKLY_TIME or wk.get("week") == week:
            return False
        wk["week"] = week
        paper, names = None, {}
        try:
            import learn
            paper, names = learn.paper, learn.SRC_NAMES
        except Exception:
            pass
        data = self.practice.data if self.practice else (lambda tk: None)
        try:
            self.send(message(state, time.time(), data, paper if self.practice else None, names), True)
            log.info("Weekly review sent")
        except Exception as e:
            log.warning("Weekly review failed: %s", type(e).__name__)
        return True
