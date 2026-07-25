"""Sends the rendered digest via SMTP, or falls back to writing a local
file if SMTP credentials aren't configured -- lets the pipeline be tested
end-to-end without real email credentials."""

import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

FALLBACK_PATH = Path(__file__).resolve().parent / "latest.html"
DEFAULT_TO = "tylerherman19@gmail.com"


def send_digest(html: str) -> str:
    """Returns a short status string describing what happened."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    from_email = os.environ.get("DIGEST_FROM_EMAIL") or smtp_user
    to_email = os.environ.get("DIGEST_TO_EMAIL") or DEFAULT_TO

    if not (smtp_host and smtp_port and smtp_user and smtp_pass and from_email):
        FALLBACK_PATH.write_text(html, encoding="utf-8")
        return f"SMTP not configured -- wrote digest to {FALLBACK_PATH} instead of emailing {to_email}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Candidate Research Monitor -- {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, int(smtp_port), timeout=30) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(from_email, [to_email], msg.as_string())

    return f"Digest emailed to {to_email}"
