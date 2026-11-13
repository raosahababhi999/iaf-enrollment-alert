# IAF Enrolment Alert

GitHub Actions monitors official Indian Air Force Airmen recruitment pages for Intake 01/2027 and sends Gmail SMTP notifications.

## Official pages
- https://iafrecruitment.edcil.co.in/airmen/
- https://iafrecruitment.edcil.co.in/airmen/psl.html

## Monitoring
- New links and PDFs discovered in page anchors.
- PDF byte changes at the same URL (SHA-256).
- Visible page-text changes and newly added text lines.
- Partial failures are reported as warnings and are not treated as proof that no recruitment update exists.

## Gmail secrets
Keep these repository Actions secrets:
- `GMAIL_ADDRESS`: Gmail sender.
- `GMAIL_APP_PASSWORD`: Google App Password, not the normal Gmail password.
- `ALERT_EMAILS`: comma-, semicolon-, or whitespace-separated recipient email addresses.

## Playwright + Chromium access test
A separate manual workflow checks whether the two official pages can be opened by a real Chromium browser on a GitHub-hosted runner.

1. Open **Actions**.
2. Select **Playwright Chromium IAF Access Test**.
3. Tap **Run workflow**.
4. Open the run and inspect the **Chromium test screenshots** artifact and logs.

The test prints each page's HTTP status, final URL, title and visible text sample. It also saves screenshots, including on failures. This is only an access diagnostic; it does not yet replace the production monitor's requests-based fetcher.

## Important limitations
- A real browser does not guarantee access: the IAF server may still block GitHub-hosted runner IP addresses or return an access-denied page.
- The test does not bypass CAPTCHA, access controls, or anti-bot protections.
- A successful result requires HTTP 200 and genuine IAF page content, not merely a browser window or a challenge page.
- Until the Chromium test passes, website monitoring is not confirmed fixed.
- GitHub Actions scheduled runs are best-effort and can be delayed.

## Files
- `monitor.py`: page discovery, PDF hashing, change detection and Gmail alerts.
- `playwright_test.py`: isolated Chromium access diagnostic.
- `.github/workflows/monitor.yml`: scheduled production monitor.
- `.github/workflows/playwright-test.yml`: manual Chromium test.
- `state.json`: automatically maintained monitoring history.
