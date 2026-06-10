import threading
import logging
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config
from database import get_db, SessionLocal
from models import UserSettings
from services.twilio_calls import place_outbound_call

logger = logging.getLogger(__name__)

# Auth-protected endpoints (mounted under /api/calls with require_auth dependency in main.py)
router = APIRouter()

# Public endpoints — Twilio posts back here; registered separately in main.py without auth
webhook_router = APIRouter()


class OutboundCallRequest(BaseModel):
    to: str | None = None  # overrides settings.phone_number when provided


@router.post("/outbound")
def trigger_outbound_call(body: OutboundCallRequest, db: Session = Depends(get_db)):
    settings = db.get(UserSettings, 1)
    to_number = (body.to or "").strip() or (settings.phone_number if settings else "")

    if not to_number:
        raise HTTPException(400, "Provide a 'to' number or save one via PATCH /api/settings")
    if not to_number.startswith("+") or len(to_number) < 8:
        raise HTTPException(400, "Phone number must be in E.164 format, e.g. +971503297329")

    result = place_outbound_call(to_number, db)
    return result


@router.get("/status")
def call_status():
    return {
        "twilio_configured": config.twilio_configured(),
        "webhook_base_url": config.twilio_webhook_base_url(),
        "voice": config.twilio_ar_voice(),
        "scheduled_time": config.twilio_call_time(),
    }


# ── Public webhook — called by Twilio, not by the user's browser ─────────────

def _twiml_response(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")


def _snooze_call(to_number: str, delay_seconds: int = 3600):
    """Fire a follow-up call after delay_seconds in a daemon thread."""
    import time

    def _run():
        time.sleep(delay_seconds)
        db = SessionLocal()
        try:
            place_outbound_call(to_number, db)
        except Exception as exc:
            logger.error("Snooze call failed for %s: %s", to_number, exc)
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True, name=f"snooze-{to_number}").start()


@webhook_router.get("/audio/{audio_id}")
def serve_audio(audio_id: str):
    from services.twilio_calls import get_audio
    data = get_audio(audio_id)
    if data is None:
        raise HTTPException(404, "Audio not found or expired")
    return Response(content=data, media_type="audio/mpeg")


@webhook_router.post("/gather")
def twilio_gather(
    Digits: str | None = Form(default=None),
    To: str | None = Form(default=None),
):
    """
    Twilio POSTs here after the student presses a key.
    Digits="1" → confirm study session
    Digits="2" → snooze one hour
    """
    voice = config.twilio_ar_voice()
    say = f'<Say language="ar-AE" voice="{voice}">'

    if Digits == "1":
        msg = "ممتاز! بالتوفيق في دراستك اليوم. إلى اللقاء!"
    elif Digits == "2":
        msg = "حسناً، سأذكرك بعد ساعة. إلى اللقاء!"
        if To:
            _snooze_call(To, delay_seconds=3600)
    else:
        msg = "لم أفهم اختيارك. إلى اللقاء!"

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response>{say}{msg}</Say></Response>"
    )
    return _twiml_response(xml)
