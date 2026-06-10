import logging
import time
import uuid
from sqlalchemy.orm import Session

import config
from models import Topic, StudySession, QuizAttempt, Assessment, DayOff, UserSettings
from scheduler import generate_daily_plan

logger = logging.getLogger(__name__)

# In-memory audio cache: uuid → (mp3_bytes, expiry_timestamp)
_audio_cache: dict[str, tuple[bytes, float]] = {}


def store_audio(audio_bytes: bytes, ttl: int = 600) -> str:
    key = str(uuid.uuid4())
    _audio_cache[key] = (audio_bytes, time.monotonic() + ttl)
    return key


def get_audio(key: str) -> bytes | None:
    entry = _audio_cache.get(key)
    if not entry:
        return None
    data, expiry = entry
    if time.monotonic() > expiry:
        _audio_cache.pop(key, None)
        return None
    return data


def _build_call_text(plan: dict) -> tuple[str, str]:
    """Returns (prompt_text, no_answer_fallback_text)."""
    items = plan.get("items", [])
    total = plan.get("total_minutes", 0)

    if plan.get("is_day_off"):
        main = "مرحباً من StudyPace! اليوم يوم راحة مجدول لك. استمتع بيومك!"
    elif not items:
        main = "مرحباً من StudyPace! لا توجد جلسات دراسية مجدولة اليوم."
    else:
        parts = [
            f"{item['topic_name']} من {item['course_name']}، {item['planned_minutes']} دقيقة"
            for item in items[:4]
        ]
        main = (
            f"مرحباً من StudyPace! جلستك الدراسية اليوم تبلغ {total} دقيقة. "
            f"المواضيع المخططة: {', '.join(parts)}. "
            "اضغط 1 للتأكيد، أو اضغط 2 لتأجيل ساعة."
        )

    return main, "لم يُستلم أي رد. إلى اللقاء!"


def _generate_elevenlabs_audio(text: str) -> bytes | None:
    api_key = config.elevenlabs_api_key()
    voice_id = config.elevenlabs_voice_id()
    if not (api_key and voice_id):
        return None
    try:
        import httpx
        resp = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": api_key, "Accept": "audio/mpeg"},
            json={
                "text": text,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("ElevenLabs generation failed, falling back to Polly: %s", exc)
        return None


def _build_twiml(
    plan: dict,
    gather_action_url: str | None = None,
    audio_url: str | None = None,
) -> str:
    prompt_text, no_answer_text = _build_call_text(plan)
    voice = config.twilio_ar_voice()

    if audio_url:
        speech = f"<Play>{audio_url}</Play>"
        fallback_say = f'<Say language="ar-AE" voice="{voice}">{no_answer_text}</Say>'
    else:
        speech = f'<Say language="ar-AE" voice="{voice}">{prompt_text}</Say>'
        fallback_say = f'<Say language="ar-AE" voice="{voice}">{no_answer_text}</Say>'

    if gather_action_url:
        body = (
            f'<Gather numDigits="1" action="{gather_action_url}" method="POST">'
            f"{speech}"
            "</Gather>"
            f"{fallback_say}"
        )
    else:
        body = speech

    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def place_outbound_call(to_number: str, db: Session) -> dict:
    """
    Place an outbound study-reminder call.
    Uses ElevenLabs audio when configured, otherwise Polly <Say>.
    Falls back to demo mode when TWILIO_* env vars are absent.
    """
    topics = db.query(Topic).all()
    sessions = db.query(StudySession).all()
    attempts = db.query(QuizAttempt).all()
    assessments = db.query(Assessment).all()
    days_off = db.query(DayOff).all()
    settings = db.query(UserSettings).first() or UserSettings()

    plan = generate_daily_plan(topics, sessions, attempts, assessments, days_off, settings)

    webhook_base = config.twilio_webhook_base_url()
    gather_url = f"{webhook_base}/api/calls/gather" if webhook_base else None

    audio_url = None
    if webhook_base:
        prompt_text, _ = _build_call_text(plan)
        audio_bytes = _generate_elevenlabs_audio(prompt_text)
        if audio_bytes:
            key = store_audio(audio_bytes)
            audio_url = f"{webhook_base}/api/calls/audio/{key}"
            logger.info("ElevenLabs audio ready at %s", audio_url)

    twiml = _build_twiml(plan, gather_action_url=gather_url, audio_url=audio_url)

    sid = config.twilio_account_sid()
    token = config.twilio_auth_token()
    from_num = config.twilio_from_number()

    if not (sid and token and from_num):
        logger.info("Twilio credentials not configured — demo mode, no call to %s", to_number)
        return {
            "mode": "demo",
            "call_sid": None,
            "twiml": twiml,
            "to": to_number,
            "audio_url": audio_url,
        }

    try:
        from twilio.rest import Client

        call = Client(sid, token).calls.create(to=to_number, from_=from_num, twiml=twiml)
        logger.info("Outbound call placed %s → %s (SID %s)", from_num, to_number, call.sid)
        return {
            "mode": "live",
            "call_sid": call.sid,
            "twiml": twiml,
            "to": to_number,
            "audio_url": audio_url,
        }
    except Exception as exc:
        logger.error("Twilio call failed for %s: %s", to_number, exc)
        raise
