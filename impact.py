"""
Likely price impact of a news item or SEC filing: ("up" | "down" | "neutral", confidence, reason).

- News headlines: Claude (if ANTHROPIC_API_KEY is set), otherwise keyword rules.
- SEC filings / insider trades: rules (the filing type and numbers say enough).

These are automated guesses from a headline, not advice.
"""

import json
import logging
import os
import re

log = logging.getLogger("bot")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5").strip() or "claude-opus-5-5"
FALLBACK_OK = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")

# ---------------------------------------------------------------- keyword rules
POS = re.compile(
    r"\b(approv(al|ed|es)|wins? (fda|ema)|clear(s|ed|ance) by (the )?fda|"
    r"beats?|tops? (estimates|expectations)|raises? (its |full[- ]year )?(guidance|outlook|forecast)|"
    r"upgrade[sd]?|price target (raised|hike)|raises? (price )?target|"
    r"to be acquired|agrees? to be acquired|buyout|takeover (bid|offer)|acquisition (offer|proposal)|"
    r"met (its |the )?(primary )?endpoint|positive (topline|top-line|data|results)|statistically significant|"
    r"record (revenue|quarter|sales|profit)|buyback|repurchase|"
    r"awarded|wins? (a |an )?(\$[\d.]+\w* )?contract|breakthrough therapy|fast track|priority review|orphan drug|"
    r"partnership|collaboration|licens(e|ing) (agreement|deal)|regains? (nasdaq )?compliance|insider buy\w*)\b", re.I)
NEG = re.compile(
    r"\b(complete response letter|crl|rejects?|rejected|rejection|refus(e|al) to file|clinical hold|warning letter|"
    r"fail(s|ed)? to|did not meet|miss(es|ed)? (its |the )?(primary )?endpoint|miss(es|ed)? (estimates|expectations)|"
    r"(cuts?|lowers?|slashes|withdraws?) (its |full[- ]year )?(guidance|outlook|forecast)|downgrade[sd]?|price target (cut|lowered)|"
    r"(public|registered direct|underwritten|secondary) offering|prices? .{0,40}offering|private placement|dilut\w*|"
    r"delist\w*|deficiency|bid price (notice|requirement|rule)|going concern|"
    r"bankrupt\w*|chapter 11|default|recall\w*|halt(s|ed)?|"
    r"investigation|subpoena|probe|fraud|short (report|seller)|"
    r"resigns?|steps down|departure|terminat\w+|layoffs?|restructuring|discontinu\w+|insider sell\w*)\b", re.I)
SPAM = re.compile(r"(class action|shareholder alert|investors? (who|with) (lost|losses)|law firm|"
                  r"rosen|pomerantz|bragar|levi & korsinsky|deadline alert|encourages .* investors)", re.I)


CONTRACT_MIN = float(os.environ.get("CONTRACT_MIN_USD", "20000000"))


def rule_news(title, contract=None):
    if contract and contract >= CONTRACT_MIN and not SPAM.search(title):
        return "up", "medium", f"contract win worth {money(contract)}"
    if SPAM.search(title):
        return "neutral", "low", "law-firm solicitation, usually no new information"
    p, n = POS.search(title), NEG.search(title)
    if p and not n:
        return "up", "low", f"keyword: {p.group(0).lower()}"
    if n and not p:
        return "down", "low", f"keyword: {n.group(0).lower()}"
    if p and n:
        return "neutral", "low", "mixed signals"
    return "neutral", "low", ""


def money(v):
    v = abs(v or 0)
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.0f}"


def rule_sec(it):
    form = it["form"].upper()
    ins = it.get("insider")
    if ins and ins.get("type") == "4":
        buy = sum(t["value"] for t in ins["tx"] if t["code"] == "P")
        sell = sum(t["value"] for t in ins["tx"] if t["code"] == "S")
        if buy and buy >= sell:
            conf = "medium" if buy >= 100_000 else "low"
            who = "CEO" if "CEO" in ins["role"].upper() or "CHIEF EXECUTIVE" in ins["role"].upper() else "insider"
            return "up", conf, f"{who} bought {money(buy)} on the open market"
        if sell:
            if ins.get("plan"):
                return "neutral", "low", f"pre-planned (10b5-1) sale of {money(sell)}"
            if sell >= 1_000_000:
                return "down", "low", f"insider sold {money(sell)}"
            return "neutral", "low", f"small insider sale ({money(sell)})"
        return "neutral", "low", "routine grant / option exercise / tax withholding"
    if ins and ins.get("type") == "144":
        v = ins.get("value") or 0
        return ("down" if v >= 1_000_000 else "neutral"), "low", f"insider plans to sell {money(v)}"
    if ins and ins.get("type") == "3":
        return "neutral", "low", "new insider registered"
    if re.match(r"^(424B[1-8]|S-1|S-3|F-1|F-3|S-3ASR|FWP)", form):
        return "down", "low", "possible offering (dilution if it's shares)"
    if re.match(r"^(SC 13D|SCHEDULE 13D)", form):
        return "up", "low", "investor took a 5%+ active stake"
    if re.match(r"^(SC TO-T)", form):
        return "up", "medium", "tender offer for the shares"
    if re.match(r"^(25-NSE|15-12)", form):
        return "down", "medium", "delisting / deregistration"
    items = set(it.get("items") or [])
    if items & {"1.03"}:
        return "down", "high", "bankruptcy filing"
    if items & {"3.01", "4.02"}:
        return "down", "medium", "delisting notice" if "3.01" in items else "financials can't be relied on"
    if items & {"2.05"}:
        return "down", "low", "restructuring / layoffs"
    if items & {"5.01", "2.01"}:
        return "neutral", "low", "control change / deal completed — check the filing"
    if items & {"2.02"}:
        return "neutral", "low", "earnings released — see the numbers"
    return "neutral", "low", ""


# ---------------------------------------------------------------- Claude
SYSTEM = (
    "You estimate the likely short-term (1-3 trading days) stock price reaction to a news headline "
    "about a specific listed company. You only see the headline, the source and sometimes a short snippet.\n"
    "Guidelines:\n"
    "- direction: up, down or neutral.\n"
    "- neutral for rehashed, opinion, listicle, promotional, 'stock moves X%' recap or law-firm solicitation pieces, "
    "and for news that is not really about this company.\n"
    "- confidence high only when the headline clearly states a material new event (FDA approval or rejection, "
    "trial success or failure, acquisition at a premium, large share offering, guidance change, bankruptcy, delisting).\n"
    "- For small biotechs, regulatory and trial news dominates; for large caps most headlines are neutral.\n"
    "- contract wins: if contract_usd is given, judge it against the company's size; a contract that is large "
    "relative to the company's revenue (common for small caps) is a clear up signal.\n"
    "- reason: at most 12 words, plain English, no hedging boilerplate."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["up", "down", "neutral"]},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "direction", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_client = None
_ai_disabled = False


def ai_enabled():
    return bool(API_KEY) and not _ai_disabled


def _ai(items, companies):
    """items: list of news dicts. Returns {index: (dir, conf, reason)} or raises."""
    global _client
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic(api_key=API_KEY, max_retries=2, timeout=90)
    payload = []
    for i, it in enumerate(items):
        snip = " ".join((it.get("desc") or "").split())[:300]
        if snip and snip.lower().startswith(it["title"].lower()[:40]):
            snip = ""
        payload.append({"id": i, "ticker": it["ticker"], "company": companies.get(it["ticker"], it["ticker"]),
                        "headline": it["title"], "source": ", ".join(it.get("sources") or [it.get("source", "")]),
                        **({"snippet": snip} if snip else {}),
                        **({"contract_usd": int(it["contract"])} if it.get("contract") else {})})
    kwargs = dict(
        model=AI_MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": "Classify each headline:\n" + json.dumps(payload, ensure_ascii=False)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if AI_MODEL in FALLBACK_OK:
        resp = _client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        resp = _client.messages.create(**kwargs)
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"stop_reason={resp.stop_reason}")
    text = next(b.text for b in resp.content if b.type == "text")
    out = {}
    for r in json.loads(text)["items"]:
        if 0 <= r["id"] < len(items):
            out[r["id"]] = (r["direction"], r["confidence"], r["reason"].strip())
    return out


def assign(items, companies, notify):
    """Set it['impact'] = (dir, conf, reason, by) on every item that lacks one."""
    global _ai_disabled
    todo = [it for it in items if "impact" not in it]
    news = [it for it in todo if it["kind"] == "news"]
    for it in todo:
        if it["kind"] == "sec":
            it["impact"] = (*rule_sec(it), "rules")
        elif it["kind"] == "gov":
            it["impact"] = ("up", "low", f"federal contract {money(it['amount'])} (may already be public)", "rules")
    if news and ai_enabled():
        try:
            res = _ai(news, companies)
            for i, it in enumerate(news):
                if i in res:
                    it["impact"] = (*res[i], "ai")
        except Exception as e:
            name = type(e).__name__
            log.warning("AI impact failed (%s); using keyword rules", name)
            if name in ("AuthenticationError", "PermissionDeniedError", "ModuleNotFoundError", "ImportError"):
                _ai_disabled = True
                why = "anthropic package missing" if name in ("ModuleNotFoundError", "ImportError") else "API key rejected"
                notify(f"⚠️ AI impact predictions are off ({why}); using keyword rules instead.")
    for it in news:
        if "impact" not in it:
            it["impact"] = (*rule_news(it["title"], it.get("contract")), "rules")


def label(impact):
    if not impact:
        return ""
    d, conf, reason, by = impact
    tag = " 🤖" if by == "ai" else ""
    if d == "neutral":
        return "⚪ Neutral" + tag  # no explanation needed for neutral
    word = {"high": "Expected to go", "medium": "Likely", "low": "Possibly"}[conf]
    head = f"🟢 {word} UP" if d == "up" else f"🔴 {word} DOWN"
    return f"{head}{' — ' + reason if reason else ''}{tag}"
