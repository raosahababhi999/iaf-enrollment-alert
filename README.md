# IAF Enrolment Alert

Telegram-only monitor for public Indian Air Force Airmen recruitment notices, with focus on Intake 01/2027 PSL and Enrolment PDFs.

## How it works
- GitHub Actions runs `monitor.py` on a scheduled basis (every 10 minutes; GitHub scheduling is best-effort).
- The script checks the official Airmen landing page and PSL page.
- Relevant PDF links are downloaded and compared using SHA-256.
- First successful run saves a baseline and sends a Telegram activation message. Later new PDFs or changed PDF bytes trigger alerts.
- Failed website/PDF checks produce warnings; they are not treated as confirmation that no update exists.

## Official pages
- https://iafrecruitment.edcil.co.in/airmen/
- https://iafrecruitment.edcil.co.in/airmen/psl.html

## Setup
1. In Telegram, open `@BotFather`, send `/newbot`, and create your bot.
2. Open your new bot and press **Start**.
3. Obtain your personal Telegram chat ID. Never share or commit the bot token.
4. In GitHub go to **Settings > Secrets and variables > Actions** and add repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Open **Actions > IAF Enrolment Website Monitor > Run workflow** to test.
6. First successful run saves the baseline; subsequent checks alert on new/changed PDFs.

## Notes
- GitHub Actions cron is best-effort; it may run later than the scheduled time.
- PDF link filtering uses PSL, selection list, enrolment/enrollment, Intake and 2027 terms in link text or URL.
- `pypdf` extracts text when possible. Scanned/image-only PDFs are not OCR-processed in this initial version.
- The alert reports whether Intake 01/2027 was found in extracted text. Always verify the official PDF.
- If the site blocks automated access (for example HTTP 403), a warning is sent instead of claiming no update.
- This repository does not use Firebase, FCM, or an Android app. Telegram is the only notification channel.

## Files
- `monitor.py`: website parsing, PDF fingerprinting, Telegram alerts.
- `.github/workflows/monitor.yml`: scheduled GitHub Actions workflow.
- `state.json`: automatically created history of discovered PDF hashes.
