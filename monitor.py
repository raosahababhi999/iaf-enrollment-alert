import os
import re
import json
import hashlib
import difflib
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

# PSL is checked first and remains the primary page for Intake 01/2027.
PAGES = [
    # Airmen / Medical Assistant PSL pages
    "https://iafrecruitment.edcil.co.in/airmen/psl.html",
    "https://iafrecruitment.edcil.co.in/psl.html",
    "https://iafrecruitment.edcil.co.in/airmen/",
    # Agniveervayu recruitment section on the same official EdCIL portal
    "https://iafrecruitment.edcil.co.in/agniveervayu/",
]

RECRUITMENT_TERMS = re.compile(
    r"psl|agniveer(?:vayu)?|agnipath|enrol(?:ment|lment)|selection|merit|result|intake|"
    r"admit|medical|provisional|final|01[/_-]?2027|2027|candidate|list",
    re.I,
)
ASSET_EXTENSIONS = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".webm",
)

def relevant_link(url, label=""):
    path = urlparse(url).path.lower()
    if path.endswith(ASSET_EXTENSIONS):
        return False
    return path.endswith(".pdf") or bool(RECRUITMENT_TERMS.search(url + " " + label))

def filtered_source_lines(raw_html):
    """Keep meaningful page structure/content while excluding visual assets and scripts."""
    source_soup = BeautifulSoup(raw_html, "html.parser")
    for tag in source_soup(["script", "style", "noscript", "img", "picture", "source", "svg"]):
        tag.decompose()
    for tag in source_soup.find_all("link"):
        rel = " ".join(tag.get("rel", [])).lower()
        href = str(tag.get("href", "")).lower()
        if "stylesheet" in rel or href.endswith(ASSET_EXTENSIONS):
            tag.decompose()
    for tag in source_soup.find_all(True):
        for attr in ("src", "srcset", "poster"):
            value = tag.get(attr)
            if isinstance(value, str) and value.lower().split("?")[0].endswith(ASSET_EXTENSIONS):
                del tag[attr]
    return source_soup.prettify().splitlines()[:5000]
STATE = Path("state.json")
TIMEOUT = (15, 45)
MAX_PDF_BYTES = 35 * 1024 * 1024
MAX_LINKS_PER_PAGE = 500
INTAKE_PATTERNS = [
    re.compile(r"\bintake\s*0?1\s*[/\-]\s*2027\b", re.I),
    re.compile(r"\b01\s*[/\-]\s*2027\b", re.I),
]

def now():
    return datetime.now(timezone.utc).isoformat()

def http_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "text/html,application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    })
    return session

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
            raw_html = response.text
            raw_html_lines = filtered_source_lines(raw_html)
            raw_html_hash = hashlib.sha256("\n".join(raw_html_lines).encode("utf-8")).hexdigest()
            raw_html_truncated = len(raw_html_lines) >= 5000
            soup = BeautifulSoup(raw_html, "html.parser")
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
                "source_sha256": raw_html_hash,
                "source_lines": raw_html_lines,
                "source_truncated": raw_html_truncated,
                "source_filter_version": 2,
                "checked_at": now(),
            }

            count = 0
            # Only collect recruitment-related links and PDFs; ignore visual/site assets.
            candidates = []
            for anchor in soup.find_all("a", href=True):
                candidates.append((anchor.get("href", "").strip(), anchor.get_text(" ", strip=True)))
            # Also catch literal PDF paths embedded in inline HTML/JavaScript source.
            for match in re.findall(r"""[A-Za-z0-9_./%-]+\.pdf(?:\?[^\s"'<>]*)?""", raw_html, flags=re.I):
                candidates.append((html.unescape(match), "PDF URL found in page source"))
            for href, anchor_text in candidates:
                absolute = urljoin(response.url, href)
                absolute, _fragment = urldefrag(absolute)
                parsed = urlparse(absolute)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    continue
                label = " ".join(anchor_text.split()) or parsed.path.rsplit("/", 1)[-1] or "Linked page"
                if not relevant_link(absolute, label):
                    continue
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

    # Do not abort here: the HTML pages may be blocked while the public API
    # endpoints are still reachable. Let main() test every source independently.
    return pages, links, errors


API_ENDPOINTS = {
    "News ticker (getNewsJson)": "https://iafrecruitment.edcil.co.in/airmen/getNewsJson",
    "News panel (getJson)": "https://iafrecruitment.edcil.co.in/airmen/getJson",
}

def fetch_api_snapshots(session):
    """Call the public endpoints as the site's own JavaScript does; never treat HTML as JSON."""
    snapshots, api_links, warnings = {}, {}, []
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://iafrecruitment.edcil.co.in/airmen/",
    }
    for label, url in API_ENDPOINTS.items():
        try:
            response = session.get(url, headers=headers, timeout=TIMEOUT)
            body = response.content
            content_type = response.headers.get("Content-Type", "").lower()
            raw_hash = hashlib.sha256(body).hexdigest()
            snapshot = {
                "label": label,
                "url": url,
                "status": response.status_code,
                "content_type": content_type,
                "bytes": len(body),
                "raw_sha256": raw_hash,
                "checked_at": now(),
                "is_json": False,
                "record_count": 0,
            }
            response.raise_for_status()

            parsed = None
            try:
                parsed = response.json()
            except (ValueError, json.JSONDecodeError):
                parsed = None

            if parsed is not None and isinstance(parsed, (list, dict)):
                normalized = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                snapshot["data_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                snapshot["is_json"] = True
                records = parsed if isinstance(parsed, list) else [parsed]
                snapshot["record_count"] = len(records)
                snapshot["preview"] = normalized[:1200]

                # The site's JavaScript reads Data, url, startDate, endDate and navin.
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    item_url = record.get("url")
                    if isinstance(item_url, str) and item_url.strip():
                        absolute = urljoin(url, item_url.strip())
                        absolute, _fragment = urldefrag(absolute)
                        parsed_url = urlparse(absolute)
                        if parsed_url.scheme in ("http", "https") and parsed_url.netloc:
                            label_text = html.unescape(str(record.get("Data") or "")).strip()
                            label_text = re.sub(r"\s+", " ", BeautifulSoup(label_text, "html.parser").get_text(" ", strip=True))[:250]
                            if not relevant_link(absolute, label_text):
                                continue
                            api_links[absolute] = {
                                "url": absolute,
                                "label": re.sub(r"\s+", " ", BeautifulSoup(label_text, "html.parser").get_text(" ", strip=True))[:250]
                                         or parsed_url.path.rsplit("/", 1)[-1]
                                         or "API linked item",
                                "source_page": url,
                                "kind": "PDF" if is_pdf_url(absolute) else "API link",
                            }
            else:
                # Save diagnostics, but do not call an HTML fallback page a valid API payload.
                preview = response.text[:300].replace("\r", " ").replace("\n", " ")
                snapshot["preview"] = preview
                warnings.append(
                    f"{label}: expected JSON but received {content_type or 'unknown Content-Type'} "
                    f"(HTTP {response.status_code}, {len(body)} bytes)"
                )
            snapshots[url] = snapshot
            print(
                "API check:", label,
                "status=", snapshot["status"],
                "content_type=", content_type,
                "json=", snapshot["is_json"],
                "records=", snapshot["record_count"],
            )
        except Exception as exc:
            warnings.append(f"{label} ({url}): {exc}")
            snapshots[url] = {
                "label": label, "url": url, "error": str(exc), "checked_at": now()
            }
            print("API request failed:", label, repr(exc))
    return snapshots, api_links, warnings

def build_report(events, page_changes, warning_errors=None, api_changes=None, html_changes=None):
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
    if html_changes:
        lines.extend(["RAW HTML SOURCE CODE CHANGES", "=" * 32])
        for change in html_changes:
            lines.append("Page: " + change["url"])
            lines.append("Old SHA-256: " + change["old_sha256"])
            lines.append("New SHA-256: " + change["new_sha256"])
            lines.extend(change["diff"][:160])
            if change.get("truncated"):
                lines.append("[Diff limited; source snapshot was truncated.]")
            lines.append("")
    if api_changes:
        lines.extend(["PUBLIC API DATA CHANGES", "=" * 32])
        for change in api_changes:
            lines.extend([
                "Endpoint: " + change["label"],
                "URL: " + change["url"],
                "Response: " + str(change.get("content_type", "unknown")),
                "JSON records: " + str(change.get("record_count", 0)),
                "Data SHA-256: " + str(change.get("data_sha256", change.get("raw_sha256", "unavailable"))),
                "Preview: " + str(change.get("preview", ""))[:900],
                "",
            ])
    if warning_errors:
        lines.extend(["CHECK WARNINGS", "=" * 32])
        lines.extend("- " + error[:700] for error in warning_errors[:10])
        lines.append("A failed page/PDF check is not proof that no recruitment update exists.")
    lines.extend([
        "",
        "Official IAF pages:",
        *PAGES,
        "",
        "This alert is generated automatically by the GitHub-hosted IAF Enrolment Alert monitor.",
    ])
    return "\n".join(lines)

def main():
    session = http_session()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    old_pages = state.get("pages", {})
    old_links = state.get("links", {})
    old_pdfs = state.get("pdfs", {})
    old_apis = state.get("apis", {})

    pages, links, page_errors = discover(session)
    api_snapshots, api_links, api_errors = fetch_api_snapshots(session)
    links.update(api_links)
    current_pdfs, pdf_errors = {}, []

    # Download every linked PDF so a replacement file at the same URL is detected too.
    for url, item in links.items():
        if not is_pdf_url(url):
            continue
        try:
            current_pdfs[url] = inspect_pdf(session, url)
        except Exception as exc:
            pdf_errors.append(f"{url}: {exc}")

    # Older Telegram state has documents/initialized but no pages/links. Treat this
    # first Gmail run as a clean baseline to avoid emailing every pre-existing link.
    baseline = not state.get("pages") and not state.get("links")
    events, page_changes, api_changes, html_changes = [], [], [], []

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

        for api_url, current_api in api_snapshots.items():
            previous_api = old_apis.get(api_url, {})
            # Only compare valid JSON payloads as meaningful API data. HTML/error bodies
            # are diagnostic warnings, not recruitment updates.
            if current_api.get("is_json"):
                old_hash = previous_api.get("data_sha256")
                new_hash = current_api.get("data_sha256")
                # Alert when an endpoint first starts returning JSON after HTML/errors,
                # as well as when an established JSON payload changes.
                became_json = bool(previous_api) and not previous_api.get("is_json")
                first_json_observation = not previous_api
                if became_json or first_json_observation or (old_hash and new_hash and old_hash != new_hash):
                    api_changes.append(current_api)

        for page_url, current in pages.items():
            previous = old_pages.get(page_url, {})
            if previous.get("sha256") and previous["sha256"] != current["sha256"]:
                old_lines = set(previous.get("lines", []))
                added = [line for line in current["lines"] if line not in old_lines]
                page_changes.append({"url": page_url, "added_lines": added})
            old_hash = previous.get("source_sha256")
            new_hash = current.get("source_sha256")
            if previous.get("source_filter_version") == 2 and old_hash and new_hash and old_hash != new_hash:
                diff = list(difflib.unified_diff(
                    previous.get("source_lines", []), current.get("source_lines", []),
                    fromfile="previous.html", tofile="current.html", lineterm=""
                ))
                html_changes.append({
                    "url": page_url, "old_sha256": old_hash, "new_sha256": new_hash,
                    "diff": diff[:160],
                    "truncated": bool(previous.get("source_truncated") or current.get("source_truncated") or len(diff) > 160),
                })

    warning_errors = page_errors + api_errors + pdf_errors
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
        "apis": {**old_apis, **api_snapshots},
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
    elif events or page_changes or api_changes or html_changes:
        subject = (
            f"IAF PSL Alert | {len(events)} recruitment item(s), "
            f"{len(page_changes)} page text change(s), "
            f"{len(html_changes)} meaningful HTML change(s), "
            f"{len(api_changes)} API data change(s)"
        )
        # Routine API/asset warnings are kept in state/logs, not sent as noisy emails.
        body = build_report(events, page_changes, None, api_changes, html_changes)
        send_email(subject, body)

    print(
        "Pages:", len(pages),
        "Links:", len(links),
        "PDFs checked:", len(current_pdfs),
        "API endpoints checked:", len(api_snapshots),
        "Valid JSON APIs:", sum(1 for item in api_snapshots.values() if item.get("is_json")),
        "API data changes:", len(api_changes),
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
                    "The GitHub-hosted IAF monitor encountered an error.\n\n"
                    + error_text
                    + "\n\nCheck the GitHub Actions run logs. A website access error is not proof that no update exists.\n"
                    + "\n".join(PAGES),
                )
                failure_state["fatal_error_signature"] = signature
                failure_state["fatal_error_last_sent_at"] = now()
                STATE.write_text(json.dumps(failure_state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            else:
                print("Same fatal error already emailed; suppressing duplicate notification.")
        except Exception as mail_exc:
            print("Could not send/deduplicate failure email:", repr(mail_exc), file=sys.stderr)
        raise
