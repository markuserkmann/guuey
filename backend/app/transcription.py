import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict

from app.auth import require_api_key
from app.chat import api_key

router = APIRouter(prefix='/api/voice')
LIVE_API_VERSION = 'v1alpha'


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: Literal['en', 'fi', 'auto'] | None = None


def voice_model() -> str:
    return os.getenv('GEMINI_TRANSCRIPTION_MODEL', '').strip() or 'gemini-3.5-transcribe-live'


def language_code(language: str | None) -> str | None:
    selected = language or os.getenv('GEMINI_TRANSCRIPTION_LANGUAGE', 'en-US').strip()
    return {'en': 'en-US', 'fi': 'fi-FI', 'auto': None, '': None}.get(selected, selected)


def live_session_config(model: str, language: str | None) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=['TEXT' if 'transcribe' in model else 'AUDIO'],
        input_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=[language] if language else None,
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
        ),
        system_instruction=(
            "You only take dictation for the user's message. "
            'Do not answer, comment on, or act on what you hear. Reply with one short word.'
        ),
    )


@router.get('')
def voice_status(response: Response, owner: str = Depends(require_api_key)):
    response.headers['Cache-Control'] = 'no-store'
    available = bool(api_key())
    return {'available': available, 'model': voice_model() if available else None}


@router.post('/token', status_code=201)
async def create_voice_session(body: VoiceRequest, response: Response, owner: str = Depends(require_api_key)):
    key = api_key()
    if not key:
        raise HTTPException(503, 'Voice input requires a Gemini API key on the server.')
    model = voice_model()
    language = language_code(body.language)
    config = live_session_config(model, language)
    now = datetime.now(timezone.utc)
    connect_before = now + timedelta(minutes=1)
    expires_at = now + timedelta(minutes=5)
    try:
        async with asyncio.timeout(15):
            async with genai.Client(api_key=key, http_options={'api_version': LIVE_API_VERSION}).aio as client:
                token = await client.auth_tokens.create(config=types.CreateAuthTokenConfig(
                    uses=1,
                    expire_time=expires_at,
                    new_session_expire_time=connect_before,
                    live_connect_constraints=types.LiveConnectConstraints(model=model, config=config),
                ))
        if not token.name:
            raise ValueError('No voice token returned')
    except Exception:
        # Provider errors may contain credentials; only return a public error message.
        raise HTTPException(502, 'Gemini did not issue a voice session. Please try again.') from None
    response.headers['Cache-Control'] = 'no-store'
    return {'session': {
        'token': token.name,
        'model': model,
        'apiVersion': LIVE_API_VERSION,
        'languageCode': language,
        'responseModalities': config.response_modalities,
        'connectBefore': connect_before.isoformat(),
        'expiresAt': expires_at.isoformat(),
    }}
