import os
import re
import json
import hashlib
import html
import io
import sys
import smtplib
from pathlib import Path
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

PAGES = [
    "https://iafrecruitment.edcil.co.in/airmen/",
    "https://iafrecruitment.edcil.co.in/airmen/psl.html",
]
STATE = Path("state.json")
TIMEOUT = (15, 45)
MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_LINKS_PER_PAGE = 500
INTAKE_PATTERNS = [
    re.compile(r"\bintake\s*0?1\s*[/\-]\s*2027\b", re.I),
    re.compile(r"\b01\s*[/\-]\s*2027\b", re.I),
]

def now():
    return datetime.now(timezone.utc).isoformat()

class AppsScriptRelaySession:
    """Fetch official IAF resources through a deployed Google Apps Script web app."""
    def __init__(self):
        self.endpoint = os.environ.get("APPS_SCRIPT_URL", "").strip()
        self.token = os.environ.get("APPS_SCRIPT_TOKEN", "").strip()
        if not self.endpoint or not self.token:
            raise RuntimeError("Missing APPS_SCRIPT_URL or APPS_SCRIPT_TOKEN GitHub Actions secret.")

    def get(self, url, timeout=TIMEOUT):
        payload = {"token": self.token, "url": url}
        relay = requests.post(self.endpoint, json=payload, timeout=120)
        relay.raise_for_status()
        try:
            result = relay.json()
        except Exception as exc:
            raise RuntimeError("Apps Script returned a non-JSON response; check Web App deployment access settings.") from exc
        if not result.get("ok"):
            status = result.get("status", "unknown")
            message = result.get("error", "Unknown Apps Script relay error")
            raise RuntimeError(f"Apps Script relay failed (HTTP {status}): {message}")
        try:
            content = __import__("base64").b64decode(result.get("bodyBase64", ""), validate=True)
        except Exception as exc:
            raise RuntimeError("Apps Script response contained invalid Base64 content.") from exc
        response = requests.Response()
        response.status_code = int(result.get("status", 200))
        response.headers.update(result.get("headers", {}))
        response._content = content
        response.url = result.get("finalUrl") or url
        response.encoding = requests.utils.get_encoding_from_headers(response.headers)
        response.raise_for_status()
        return response


def http_session():
    return AppsScriptRelaySession()

def recipients():
    raw = os.environ.get("ALERT_EMAILS", "")
    values = re.split(r"[,;\s]+", raw.strip())
    result = list(dict.fromkeys(x.strip() for x in values if x.strip()))
    if not result:
        raise RuntimeError("ALERT_EMAILS is empty. Add one or more recipient email addresses.")
    return result

def send_email(subject, body):
    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    targets = recipients()
    if not sender or not password:
        raise RuntimeError("Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD GitHub Actions secret.")

    sent, failed = [], []
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(sender, password)
        for target in targets:
            message = EmailMessage()
            message["From"] = sender
            message["To"] = target
            message["Subject"] = subject
            message.set_content(body)
            try:
                smtp.send_message(message, to_addrs=[target])
                sent.append(target)
                print("Email sent:", target)
            except Exception as exc:
                failed.append((target, str(exc)))
                print("Email failed for", target, ":", repr(exc))

    print("Email delivery:", len(sent), "sent;", len(failed), "failed;", "recipients:", len(targets))
    if not sent:
        raise RuntimeError("Email delivery failed for every recipient: " + "; ".join(f"{a}: {b}" for a, b in failed))
    return sent, failed

def is_pdf_url(url):
    return urlparse(url).path.lower().endswith(".pdf")

def inspect_pdf(session, url):
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.content
    if len(data) > MAX_PDF_BYTES:
        raise RuntimeError("PDF exceeds 35 MB safety limit")
    if not data.startswith(b"%PDF") and "pdf" not in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("Link did not return a PDF")
    extracted = ""
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        extracted = "\n".join((page.extract_text() or "") for page in reader.pages[:30])
    except Exception as exc:
        print("PDF text extraction unavailable:", url, repr(exc))
    haystack = (url + " " + extracted).lower()
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "intake_01_2027": any(pattern.search(haystack) for pattern in INTAKE_PATTERNS),
        "text_extractable": bool(extracted.strip()),
    }

def discover(session):
    pages, links, errors = {}, {}, []
    successful_pages = 0

    for page_url in PAGES:
        try:
            response = session.get(page_url, timeout=TIMEOUT)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type and not response.text.lstrip().startswith("<"):
                raise RuntimeError("Response was not HTML")

            successful_pages += 1
            soup = BeautifulSoup(response.text, "html.parser")
            for unwanted in soup(["script", "style", "noscript"]):
                unwanted.decompose()

            page_title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else page_url
            text_lines = [
                " ".join(line.split())
                for line in soup.get_text("\n").splitlines()
                if line.strip()
            ]
            text_lines = list(dict.fromkeys(text_lines))[:1500]
            text_hash = hashlib.sha256("\n".join(text_lines).encode("utf-8")).hexdigest()
            pages[page_url] = {
                "title": page_title,
                "sha256": text_hash,
                "lines": text_lines,
                "checked_at": now(),
            }

            count = 0
            for anchor in soup.find_all("a", href=True):
                href = anchor.get("href", "").strip()
                absolute = urljoin(response.url, href)
                absolute, _fragment = urldefrag(absolute)
                parsed = urlparse(absolute)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    continue
                label = " ".join(anchor.get_text(" ", strip=True).split()) or parsed.path.rsplit("/", 1)[-1] or "Linked page"
                links[absolute] = {
                    "url": absolute,
                    "label": label[:250],
                    "source_page": page_url,
                    "kind": "PDF" if is_pdf_url(absolute) else "Link",
                }
                count += 1
                if count >= MAX_LINKS_PER_PAGE:
                    break
        except Exception as exc:
            errors.append(f"{page_url}: {exc}")

    if successful_pages == 0:
        raise RuntimeError("All official pages failed. " + " | ".join(errors))
    return pages, links, errors

def build_report(events, page_changes, warning_errors=None):
    lines = [
        "IAF ENROLMENT ALERT",
        "Intake focus: 01/2027",
        "Checked at (UTC): " + now(),
        "",
    ]
    if events:
        lines.append("NEW OR CHANGED WEBSITE ITEMS")
        lines.append("=" * 32)
        for index, event in enumerate(events, 1):
            lines.extend([
                f"{index}. {event['status']}: {event['label']}",
                "Type: " + event["kind"],
                "URL: " + event["url"],
                "Found on: " + event["source_page"],
            ])
            if event.get("bytes") is not None:
                lines.append("PDF size: " + str(event["bytes"]) + " bytes")
                lines.append("Intake 01/2027 text: " + ("MATCHED" if event.get("intake_01_2027") else "not confirmed"))
                if not event.get("text_extractable"):
                    lines.append("Note: PDF text could not be extracted; open the PDF to verify.")
            lines.append("")
    if page_changes:
        lines.extend(["WEBSITE PAGE TEXT CHANGES", "=" * 32])
        for change in page_changes:
            lines.append("Page: " + change["url"])
            for added in change["added_lines"][:12]:
                lines.append("+ " + added[:300])
            if not change["added_lines"]:
                lines.append("The page text fingerprint changed (layout or content may have changed).")
            lines.append("")
    if warning_errors:
        lines.extend(["CHECK WARNINGS", "=" * 32])
        lines.extend("- " + error[:700] for error in warning_errors[:10])
        lines.append("A failed page/PDF check is not proof that no recruitment update exists.")
    lines.extend([
        "",
        "Official IAF pages:",
        *PAGES,
        "",
        "This alert is generated automatically by GitHub Actions using a Google Apps Script fetch relay.",
    ])
    return "\n".join(lines)

def main():
    session = http_session()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    old_pages = state.get("pages", {})
    old_links = state.get("links", {})
    old_pdfs = state.get("pdfs", {})

    pages, links, page_errors = discover(session)
    current_pdfs, pdf_errors = {}, []

    # Download every linked PDF so a replacement file at the same URL is detected too.
    for url, item in links.items():
        if not is_pdf_url(url):
            continue
        if urlparse(url).hostname != "iafrecruitment.edcil.co.in":
            pdf_errors.append(f"{url}: external PDF host is listed but not fetched by the restricted Apps Script relay")
            continue
        try:
            current_pdfs[url] = inspect_pdf(session, url)
        except Exception as exc:
            pdf_errors.append(f"{url}: {exc}")

    # Older Telegram state has documents/initialized but no pages/links. Treat this
    # first Gmail run as a clean baseline to avoid emailing every pre-existing link.
    baseline = not state.get("pages") and not state.get("links")
    events, page_changes = [], []

    if not baseline:
        for url, item in links.items():
            previous = old_links.get(url)
            pdf_info = current_pdfs.get(url, {})
            if previous is None:
                events.append({
                    **item,
                    "status": "NEW LINK FOUND",
                    **pdf_info,
                })
            elif is_pdf_url(url):
                old_pdf = old_pdfs.get(url, {})
                new_pdf = current_pdfs.get(url, {})
                if old_pdf.get("sha256") and new_pdf.get("sha256") and old_pdf["sha256"] != new_pdf["sha256"]:
                    events.append({
                        **item,
                        "status": "PDF FILE CONTENT CHANGED",
                        **new_pdf,
                    })

        for page_url, current in pages.items():
            previous = old_pages.get(page_url, {})
            if previous.get("sha256") and previous["sha256"] != current["sha256"]:
                old_lines = set(previous.get("lines", []))
                added = [line for line in current["lines"] if line not in old_lines]
                page_changes.append({"url": page_url, "added_lines": added})

    warning_errors = page_errors + pdf_errors
    previous_warning = state.get("warning_signature")
    warning_signature = hashlib.sha256("\n".join(warning_errors).encode("utf-8")).hexdigest() if warning_errors else None
    warning_changed = bool(warning_errors and warning_signature != previous_warning)

    # Persist a complete successful snapshot. Keep old known items if a source page
    # failed this run, so temporary 403/5xx responses do not erase the baseline.
    merged_pages = dict(old_pages)
    merged_pages.update(pages)
    merged_links = dict(old_links)
    merged_links.update(links)
    merged_pdfs = dict(old_pdfs)
    merged_pdfs.update(current_pdfs)

    state.update({
        "initialized": True,
        "pages": merged_pages,
        "links": merged_links,
        "pdfs": merged_pdfs,
        "last_checked_at": now(),
        "last_warning": warning_errors or None,
        "warning_signature": warning_signature,
    })
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if baseline:
        subject = "IAF Enrolment Alert | Email monitor activated"
        body = (
            "Your IAF Enrolment Alert email monitor has completed its first baseline scan.\n\n"
            f"Official pages checked successfully: {len(pages)}\n"
            f"Existing links recorded: {len(links)}\n"
            f"Linked PDFs checked: {len(current_pdfs)}\n\n"
            "This first run records existing website items and does not label them as new. "
            "Future newly published links, new PDFs, changed PDF files and detected page-text changes will trigger email alerts.\n\n"
            + (build_report([], [], warning_errors) if warning_errors else "No check warnings were reported in this scan.")
        )
        send_email(subject, body)
    elif events or page_changes or warning_changed:
        subject = f"IAF Enrolment Alert | {len(events)} new/changed link(s), {len(page_changes)} page change(s)"
        body = build_report(events, page_changes, warning_errors if warning_changed else None)
        send_email(subject, body)

    print(
        "Pages:", len(pages),
        "Links:", len(links),
        "PDFs checked:", len(current_pdfs),
        "New/changed links:", len(events),
        "Page text changes:", len(page_changes),
        "Warnings:", len(warning_errors),
        "Baseline:", baseline,
    )

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("MONITOR FAILED:", repr(exc), file=sys.stderr)
        # Deduplicate identical fatal errors: never send the same 403 email
        # every 10 minutes. A changed error will trigger a new alert.
        try:
            failure_state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
            error_text = repr(exc)
            signature = hashlib.sha256(error_text.encode("utf-8")).hexdigest()
            previous_signature = failure_state.get("fatal_error_signature")
            if signature != previous_signature:
                send_email(
                    "IAF Enrolment Alert | Monitor error",
                    "The GitHub Actions IAF monitor (Google Apps Script relay) encountered an error.\n\n"
                    + error_text
                    + "\n\nCheck the GitHub Actions run logs. A website access error is not proof that no update exists.\n"
                    + "\n".join(PAGES),
                )
                failure_state["fatal_error_signature"] = signature
                failure_state["fatal_error_last_sent_at"] = now()
                STATE.write_text(json.dumps(failure_state, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
            else:
                print("Same fatal error already emailed; suppressing duplicate notification.")
        except Exception as mail_exc:
            print("Could not send/deduplicate failure email:", repr(mail_exc), file=sys.stderr)
        raise
