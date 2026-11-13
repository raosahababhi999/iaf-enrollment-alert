/**
 * IAF Enrolment Alert - Google Apps Script fetch relay
 * Deploy as Web App: Execute as Me; Who has access: Anyone.
 * Set Script Property IAF_RELAY_TOKEN to a long random secret and add
 * the same value to GitHub Actions secret APPS_SCRIPT_TOKEN.
 * Only the official IAF recruitment host is accepted. Responses over 5 MB
 * are rejected to keep the relay response manageable.
 */
const ALLOWED_HOST = "iafrecruitment.edcil.co.in";
const MAX_BYTES = 5 * 1024 * 1024;

function doPost(e) {
  try {
    const input = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    const expected = PropertiesService.getScriptProperties().getProperty("IAF_RELAY_TOKEN");
    if (!expected || !input.token || input.token !== expected) {
      return jsonOutput({ok: false, status: 401, error: "Unauthorized. Check the configured relay token."});
    }
    const target = String(input.url || "");
    const match = target.match(/^https:\/\/([^\/?#]+)(?:[\/?#]|$)/i);
    if (!match || match[1].toLowerCase() !== ALLOWED_HOST) {
      return jsonOutput({ok: false, status: 403, error: "Only HTTPS URLs on the official IAF recruitment host are allowed."});
    }
    const response = UrlFetchApp.fetch(target, {
      method: "get",
      followRedirects: true,
      muteHttpExceptions: true,
      validateHttpsCertificates: true,
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
        "Accept": "text/html,application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
      }
    });
    const status = response.getResponseCode();
    const bytes = response.getContent();
    const headers = response.getHeaders();
    if (bytes.length > MAX_BYTES) {
      return jsonOutput({ok: false, status: 413, error: "Response exceeds the 5 MB relay limit; the link is recorded but its content could not be fingerprinted."});
    }
    const safeHeaders = {};
    Object.keys(headers).forEach(function(key) {
      const lower = key.toLowerCase();
      if (lower === "content-type" || lower === "content-length" || lower === "last-modified" || lower === "etag") {
        safeHeaders[key] = String(headers[key]);
      }
    });
    return jsonOutput({
      ok: status >= 200 && status < 300,
      status: status,
      headers: safeHeaders,
      finalUrl: target,
      bodyBase64: Utilities.base64Encode(bytes),
      error: status >= 200 && status < 300 ? "" : "The IAF server returned HTTP " + status + "."
    });
  } catch (err) {
    return jsonOutput({ok: false, status: 500, error: String(err && err.message ? err.message : err)});
  }
}
function jsonOutput(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
