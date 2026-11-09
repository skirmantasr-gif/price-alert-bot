# First setup on GitHub Actions (works with your computer off)

1. Create a **public** repository on https://github.com (public = free unlimited Actions minutes; all private
   data stays in Secrets and the encrypted `state.enc`).
2. **Add file → Upload files**: all `.py` files, `requirements-ai.txt`, `requirements-pdf.txt` and the
   `tests/` folder. Then add `.github/workflows/price-alerts.yml` and `.github/workflows/tests.yml`
   (Add file → Create new file, paste the contents). Commit to `main`.
3. **Settings → Secrets and variables → Actions → New repository secret**: add at least `PRICEHINIO_ALERT`
   (bot token from @BotFather), `TELEGRAM_CHAT_ID`, `STATE_KEY` (`openssl rand -base64 32`; keep it — if it
   is lost the bot starts fresh once) and `WATCHLIST` (JSON, format in `watchlist.py`). Optional secrets are
   listed in `README.md`.
4. **Settings → Actions → General → Workflow permissions** → **Read and write permissions** → Save.
5. **Actions** tab → enable workflows if asked → **Price alerts** → **Run workflow**. A "✅ Price alert bot
   started" message arrives in Telegram within a minute.

It then runs nonstop: each run watches for ~5h45m and the hourly schedule keeps the next run queued.

- Change stocks or alert steps: edit the `WATCHLIST` secret (takes effect at the next run).
- Stop: Actions → Price alerts → ⋯ → Disable workflow.
