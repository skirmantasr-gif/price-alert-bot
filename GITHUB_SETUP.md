# Run 24/7 on GitHub Actions (works with your Mac off)

1. Sign up at https://github.com and create a new **Public** repository named `price-alert-bot`
   (public = free unlimited Actions minutes; your Telegram token stays secret in step 3).
2. On the repo page: **Add file → Upload files** → drag in `bot.py`.
   Then **Add file → Create new file**, name it `.github/workflows/price-alerts.yml`,
   and paste the contents of that file from this folder. Commit.
3. **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `PRICEHINIO_ALERT` (the bot token): from @BotFather
   - `TELEGRAM_CHAT_ID`: your chat ID
   - `STATE_KEY` (recommended): a long random value (`openssl rand -base64 32`) that encrypts the saved
     state in `state.enc`. Without it the bot token is used. Keep it: if it's lost or changed, the bot
     starts with fresh state once (the old bot token is still accepted for reading).
4. **Settings → Actions → General → Workflow permissions** → choose **Read and write permissions** → Save.
5. **Actions** tab → enable workflows if asked → **Price alerts** → **Run workflow**. You should get a "✅ Price alert bot started" message in Telegram within a minute.

Done. It now runs nonstop: each run watches for ~5h45m, and the hourly schedule keeps the next run queued.
On the first run each stock's current price is the starting point; alerts come at every ±2% from there.

To change tickers or threshold: edit `TICKERS` / `THRESHOLD_PCT` in the workflow file on GitHub.
To stop: Actions → Price alerts → ⋯ → Disable workflow.
