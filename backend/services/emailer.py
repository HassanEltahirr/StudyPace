import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage


def password_reset_url(token: str) -> str:
    base_url = os.getenv("FRONTEND_URL", "https://studypace.fly.dev").rstrip("/")
    return f"{base_url}/reset-password?token={token}"


def send_password_reset_email(to_email: str, token: str) -> bool:
    reset_url = password_reset_url(token)
    subject = "Reset your StudyPace password"
    text = (
        "Use this link to reset your StudyPace password. "
        "It expires in 1 hour.\n\n"
        f"{reset_url}\n\n"
        "If you did not ask for this, you can ignore this email."
    )

    if _send_with_resend(to_email, subject, text):
        return True
    if _send_with_smtp(to_email, subject, text):
        return True

    print(f"Password reset link for {to_email}: {reset_url}")
    return False


def _sender_address() -> str:
    return (
        os.getenv("EMAIL_FROM", "")
        or os.getenv("SMTP_FROM", "")
        or "StudyPace <no-reply@studypace.app>"
    ).strip()


def _send_with_resend(to_email: str, subject: str, text: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        return False

    payload = json.dumps({
        "from": _sender_address(),
        "to": [to_email],
        "subject": subject,
        "text": text,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception as exc:
        print(f"Resend password reset email failed: {exc}")
        return False


def _send_with_smtp(to_email: str, subject: str, text: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return False

    port = int(os.getenv("SMTP_PORT", "587") or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() not in {"0", "false", "no"}

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _sender_address()
    message["To"] = to_email
    message.set_content(text)

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return True
    except Exception as exc:
        print(f"SMTP password reset email failed: {exc}")
        return False
