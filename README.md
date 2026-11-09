# Price alert bot

A personal stock-watching bot that sends Telegram messages. It runs 24/7 for free on GitHub Actions
(public repo); the watchlist, chat IDs and all keys live in GitHub **Secrets**, never in this code.

> Everything here is information and simulation, **not financial advice**. Alerts can be wrong or late.

## What it sends

| Feature | What it does | Module | Channel |
|---|---|---|---|
| 📈📉 Price moves | Alert at every ±N% move from the last alert (pre-market, regular, after-hours). First 15 min after the open: only big moves | `bot.py` | main |
| 📰 News & filings | Company news (Yahoo, Google News, Benzinga via Alpaca), SEC filings, FDA, trading halts, unusual volume; AI rates each item UP / DOWN + confidence; old, repeated, off-topic and minor items are quiet | `news.py`, `impact.py`, `extras.py` | main |
| 🧭 Hourly outlook | Each market hour: a short view per stock, then a check of the last hour's calls | `insights.py` | main |
| 🏛️ Contract wins | Contract / tender wins by small and mid caps: press wires, 15 markets, ASX + HKEX announcements (PDF), USAspending.gov federal awards | `contracts.py` | contracts |
| 🏆 Winners & losers | Pre-market movers and daily top movers of all US stocks + a "mover diary" that measures how many big moves were foreseeable | `movers.py` | contracts |
| 🎯 Buyout watch | Daily screen for takeover-type companies + live takeover news / SEC merger filings for the candidates | `buyout.py` | buyout |
| 🎓 Learning review | Saves every UP/DOWN call with its price, checks the outcome later, posts hit rates every 2 days and feeds the lessons back to the AI | `learn.py` | main |
| 🧪 Practice portfolios | Pretend €100 per portfolio trading the bot's own calls at real market prices (spread, fees, stops, profit lock), vs the S&P 500, with 📉 worst drop | `paper.py` | practice |
| 🌡️ Market mood | Risk-on / neutral / defensive from 20 days of QQQ, QQQ vs XLP, IWM, XBI. Shown in the daily check + practice statements. The 🌡️ practice portfolio skips buys while it is defensive, to *measure* whether that helps | `mood.py` | main, practice |
| ☀️ Daily check | Morning status: GitHub bot + phone forwarder alive, yesterday's counts, market mood, earnings / FDA dates for the next 14 days | `extras.py` | main |

A second copy runs on an old Android phone (Termux) to forward third-party chat posts; GitHub and the phone
watch each other (heartbeat) and warn if the other goes quiet. Phone files are not in this repo.

## Settings (GitHub → Settings → Secrets and variables → Actions)

| Secret | Needed | What |
|---|---|---|
| `PRICEHINIO_ALERT` | yes | Telegram bot token (@BotFather) |
| `TELEGRAM_CHAT_ID` | yes | main channel |
| `STATE_KEY` | yes | random key that encrypts the saved state (`openssl rand -base64 32`). Lost/changed = fresh start once |
| `WATCHLIST` | yes | stocks + settings as JSON (format in `watchlist.py`) |
| `SEC_CONTACT` | for SEC | contact email the SEC requires in requests; SEC filings are off without it |
| `ANTHROPIC_API_KEY` | optional | AI ratings; without it simple keyword rules are used |
| `ALPACA_KEY`, `ALPACA_SECRET` | optional | Alpaca *paper* keys for Benzinga real-time news |
| `CONTRACTS_CHAT_ID` | optional | channel for 🏛️ contracts and 🏆 movers |
| `BUYOUT`, `BUYOUT_CHAT_ID` | optional | buyout universe JSON (format in `buyout.py`) + its channel; buyout watch is off without the channel |
| `PAPER_CHAT_ID` | optional | channel for 🧪 practice trades and hourly statements |

Other settings (intervals, report times, AI model) are plain environment values in
`.github/workflows/price-alerts.yml`. The workflow also needs **Settings → Actions → General → Workflow
permissions → Read and write** (to save the encrypted state).

## How it runs

- `price-alerts.yml` starts every hour; each run watches for ~5h45m, at most one runs + one waits, so there is
  always a successor. The state (reference prices, sent items, practice portfolios, learning data) is saved as
  `state.enc` (AES-256, key = `STATE_KEY`) after every run.
- `tests.yml` runs the automatic tests (`tests/`, no internet, no secrets) on every code upload. A failed test
  never stops the running bot; GitHub emails the owner.
- Updating: upload the changed `.py` files (and their tests) to `main`. The running bot keeps its old code;
  the next run (within ~6 hours, or cancel the running one) uses the new code.
- Local test run: `python -m unittest discover -s tests -t .`

## Safety

- **No secrets in code or logs.** Keys come only from Secrets; log lines print error *types*, never URLs,
  tokens or tickers (Actions logs of a public repo are public).
- **Encrypted state.** Watched stocks and history are not readable in the public repo.
- **Locked dependencies.** Only two optional extras are installed, as exact versions with SHA-256 hashes
  (`requirements-ai.txt`, `requirements-pdf.txt`), wheels only; GitHub Actions are pinned to commit SHAs.
  Re-check them for known vulnerabilities before changing (e.g. on https://osv.dev).
- **Untrusted text.** News, filings and PDFs are third-party text: the AI prompts treat them as data and never
  as instructions; messages are HTML-escaped.
- **PDF sandbox.** Exchange PDFs are size-limited and read in a separate process with a 20-second and memory
  limit, so a broken or hostile PDF cannot freeze or crash the bot.
- **Least privilege.** Only the final "save state" step gets the repo-write token; checkout keeps no credentials;
  the tests workflow is read-only.

## Files

`bot.py` main loop · `watchlist.py` settings · `news.py` news/SEC/FDA · `impact.py` AI ratings ·
`extras.py` halts, volume, calendar, heartbeat, daily check · `insights.py` hourly outlook · `contracts.py`
contract wins · `movers.py` winners & losers · `buyout.py` buyout watch · `learn.py` learning review ·
`paper.py` practice portfolios · `mood.py` market mood · `alpaca.py` Benzinga news · `GITHUB_SETUP.md` first setup
