# IAF Enrolment Alert

GitHub Actions monitors official Indian Air Force Airmen recruitment pages for Intake 01/2027. Google Apps Script acts as a restricted fetch relay; GitHub Actions compares page/link/PDF changes and sends Gmail SMTP notifications.

## Monitoring
- Official Airmen landing page and PSL page.
- New links and PDFs discovered in page anchors.
- PDF byte changes at the same URL (SHA-256), where the PDF is hosted on the official IAF recruitment host and is within the 5 MB relay limit.
- Visible page-text changes and new text lines.
- Partial failures are reported as warnings and are not treated as proof that no update exists.

## Gmail secrets
Keep these existing repository Actions secrets:
- `GMAIL_ADDRESS`: Gmail sender.
- `GMAIL_APP_PASSWORD`: Google App Password, not the normal Gmail password.
- `ALERT_EMAILS`: comma-, semicolon-, or whitespace-separated recipient addresses.

## Deploy the Google Apps Script relay
1. Open [Google Apps Script](https://script.google.com/) and create a standalone project named **IAF Enrolment Alert Relay**.
2. Open this repository's `apps-script/Code.gs`, copy its complete contents, paste into the Apps Script editor and save.
3. In Apps Script **Project Settings > Script Properties**, add:
   - Property: `IAF_RELAY_TOKEN`
   - Value: create a long random secret (at least 32 characters). Do not share it publicly.
4. Select **Deploy > New deployment > Web app**.
5. Set **Execute as: Me** and **Who has access: Anyone**, then deploy and authorize the requested external-request permission.
6. Copy the deployed Web App URL ending in `/exec`.
7. In GitHub repository **Settings > Secrets and variables > Actions**, add:
   - `APPS_SCRIPT_URL` = the deployed `/exec` URL.
   - `APPS_SCRIPT_TOKEN` = exactly the same secret value as `IAF_RELAY_TOKEN`.
8. Keep the existing Gmail secrets. Then open **Actions > IAF Enrolment Website Monitor > Run workflow** for a manual test.

The web app must be accessible to anyone because GitHub's runner is not signed into your Google account. The random token protects the relay endpoint. The script only accepts HTTPS URLs on `iafrecruitment.edcil.co.in`; do not put the token in source code or commit it to the repository.

## Limits and honest status
- Apps Script's relay currently rejects responses larger than 5 MB. External-host PDFs are discovered as links but are not downloaded through this restricted relay.
- Google Apps Script may also receive HTTP 403 from the IAF website. This change routes requests through Google's fetch infrastructure, but it does **not** guarantee the IAF server will allow them.
- A successful test must show HTTP 200 and actual IAF page content in the GitHub Actions log. Until then, monitoring is not confirmed fixed.
- The first successful run sends a baseline/activation email; existing links are recorded, not falsely labelled as new.
- GitHub Actions runs every 10 minutes (schedule is best-effort and can be delayed).

## Files
- `monitor.py`: fetch relay client, discovery, PDF hashing, change detection, Gmail alerts.
- `apps-script/Code.gs`: Google Apps Script Web App relay.
- `.github/workflows/monitor.yml`: scheduled workflow.
- `state.json`: automatically maintained monitoring history.
