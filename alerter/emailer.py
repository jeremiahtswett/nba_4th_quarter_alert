"""Gmail SMTP delivery (spec section 6): smtplib, STARTTLS on port 587."""

import logging
import smtplib
import ssl
import time
from email.message import EmailMessage

from .config import SMTP_HOST, SMTP_PORT, EmailConfig
from .trigger import REASON_OVERTIME, Alert

log = logging.getLogger(__name__)

SEND_RETRIES = 2  # total attempts (retry at least once per spec section 9)
RETRY_DELAY_SECONDS = 10


def build_subject(alert: Alert) -> str:
    score = f"{alert.away_tricode} {alert.away_score} – {alert.home_tricode} {alert.home_score}"
    if alert.reason == REASON_OVERTIME:
        return f"\U0001f6a8 Overtime: {score} ({alert.period_display})"
    return f"\U0001f6a8 Close game: {score}, {alert.clock_display} left in {alert.period_display}"


def build_body(alert: Alert) -> str:
    clock = alert.clock_display or "clock stopped"
    lines = [
        f"{alert.matchup}",
        f"Score: {alert.away_tricode} {alert.away_score} – {alert.home_tricode} {alert.home_score}",
        f"Time:  {clock} in {alert.period_display}",
    ]
    if alert.series_text:
        lines.append(f"Series: {alert.series_text}")
    lines += [
        "",
        "Tune in! This is the only alert you'll get for this game.",
    ]
    return "\n".join(lines)


def send_email(subject: str, body: str, cfg: EmailConfig) -> bool:
    """Send via Gmail SMTP. Returns True on success, False after all retries."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = cfg.recipient
    msg.set_content(body)

    for attempt in range(1, SEND_RETRIES + 1):
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                smtp.starttls(context=context)
                smtp.login(cfg.sender, cfg.app_password)
                smtp.send_message(msg)
            log.info("Email sent: %s", subject)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.error("Email send attempt %d/%d failed: %s", attempt, SEND_RETRIES, exc)
            if attempt < SEND_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    return False


def send_alert(alert: Alert, cfg: EmailConfig, subject_prefix: str = "") -> bool:
    return send_email(subject_prefix + build_subject(alert), build_body(alert), cfg)
