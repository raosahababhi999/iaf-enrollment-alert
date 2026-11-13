# IAF Enrolment Alert

GitHub Actions-based monitor for official Indian Air Force Airmen recruitment pages, focused on Intake 01/2027. Notifications are sent by Gmail SMTP only.

## What it monitors
- Official Airmen landing page and PSL page.
- All HTTP/HTTPS links found in page anchors, not only links containing PSL/Enrolment keywords.
- New links and PDFs, including their visible link title, URL and source page.
- PDF file-content changes at an existing URL using SHA-256.
- Visible page-text changes, with newly appearing text lines included in the alert where possible.
- Partial website/PDF failures are reported as warnings and are never treated as proof that no update exists.

## Notification delivery
The workflow sends one separate email per recipient, so recipients do not see one another's email addresses.

Add these repository Actions secrets:
- \`GMAIL_ADDRESS\`: Gmail account used as the sender.
- \`GMAIL_APP_PASSWORD\`: 16-character Google App Password (not the normal Gmail password).
- \`ALERT_EMAILS\`: comma-, semicolon-, or whitespace-separated recipient addresses (up to 20 recommended).

Gmail SMTP uses \`smtp.gmail.com:587\` with STARTTLS. The sender Gmail account must be eligible for App Passwords and have 2-Step Verification enabled.

## Running
- GitHub Actions runs every 10 minutes (schedule is best-effort and may be delayed).
- Use Actions > IAF Enrolment Website Monitor > Run workflow for a manual run.
- The first successful run sends an activation/baseline email and records existing links. Existing links are not falsely reported as new.
- Later runs email newly discovered links, changed PDF bytes, visible page-text changes, and newly changed check warnings.

## State and limitations
- \`state.json\` stores page fingerprints, link inventory, and PDF hashes. GitHub Actions commits this file after each run.
- Only links present in the rendered HTML anchor elements are discovered; links injected only by JavaScript or hidden behind forms/login may not be visible.
- PDF hashing downloads linked PDFs on each check. Files over 35 MB are skipped and reported.
- Text extraction uses pypdf. Scanned/image-only PDFs may not yield searchable text; the alert still includes the PDF name and direct link.
- If the official site returns HTTP 403/5xx or blocks GitHub-hosted runners, the workflow sends an error/warning email when SMTP is available. A successful website check is required before declaring the scan complete.
- This project uses GitHub Actions and Gmail SMTP. It does not use Supabase, Firebase, Telegram, ntfy, WhatsApp, or Resend.

## Files
- \`monitor.py\`: page/link discovery, PDF hashing, change detection, Gmail notifications.
- \`.github/workflows/monitor.yml\`: scheduled workflow.
- \`state.json\`: automatically maintained monitoring history.
