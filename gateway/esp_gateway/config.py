"""Gateway configuration, loaded from environment variables.

Secrets (INTERNAL_API_KEY, GROQ_API_KEY, device tokens) come from the environment
and are never written to disk by the gateway.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Config:
    # WS server bind
    host: str = "0.0.0.0"
    port: int = 8770

    # Butler brain
    butler_url: str = "http://192.168.1.117:8000"
    internal_api_key: str = ""

    # Kokoro TTS
    kokoro_url: str = "http://192.168.1.117:8880"
    default_voice: str = "bf_emma"

    # Groq STT
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"
    stt_language: str = "en"

    # Audio
    default_capture_rate: int = 16000
    default_playback_rate: int = 16000

    # Auth: device_token -> list of allowed user_ids ([] means "any user").
    device_tokens: dict[str, list[str]] = field(default_factory=dict)
    allow_insecure: bool = False  # if True and no tokens configured, accept any token

    @property
    def stt_backend(self) -> str:
        """'groq' when a key is present, else 'null' (text-only / dev)."""
        forced = os.environ.get("STT_BACKEND")
        if forced:
            return forced
        return "groq" if self.groq_api_key else "null"

    def allowed_users(self, device_token: str) -> list[str] | None:
        """Return allowed user_ids for a token, or None if the token is rejected.

        An empty list means "any user_id is allowed" (the token is valid but
        unrestricted). When no tokens are configured and allow_insecure is set,
        every token is accepted with no restriction.
        """
        if not self.device_tokens:
            if self.allow_insecure:
                return []
            return None
        return self.device_tokens.get(device_token)


def load_config() -> Config:
    """Build Config from environment variables."""
    cfg = Config()
    cfg.host = os.environ.get("GATEWAY_HOST", cfg.host)
    cfg.port = int(os.environ.get("GATEWAY_PORT", cfg.port))
    cfg.butler_url = os.environ.get("BUTLER_API_URL", cfg.butler_url).rstrip("/")
    cfg.internal_api_key = os.environ.get("INTERNAL_API_KEY", cfg.internal_api_key)
    cfg.kokoro_url = os.environ.get("KOKORO_URL", cfg.kokoro_url).rstrip("/")
    cfg.default_voice = os.environ.get("KOKORO_VOICE", cfg.default_voice)
    cfg.groq_api_key = os.environ.get("GROQ_API_KEY", cfg.groq_api_key)
    cfg.groq_stt_model = os.environ.get("GROQ_STT_MODEL", cfg.groq_stt_model)
    cfg.default_capture_rate = int(
        os.environ.get("CAPTURE_RATE", cfg.default_capture_rate)
    )
    cfg.default_playback_rate = int(
        os.environ.get("PLAYBACK_RATE", cfg.default_playback_rate)
    )
    cfg.allow_insecure = os.environ.get("GATEWAY_ALLOW_INSECURE", "") in ("1", "true", "True")

    # device tokens: either a JSON map, or a single token + comma-separated users
    tokens_json = os.environ.get("GATEWAY_DEVICE_TOKENS")
    if tokens_json:
        cfg.device_tokens = json.loads(tokens_json)
    else:
        single = os.environ.get("GATEWAY_DEVICE_TOKEN")
        if single:
            users = [u for u in os.environ.get("GATEWAY_ALLOWED_USERS", "").split(",") if u]
            cfg.device_tokens = {single: users}

    if not cfg.device_tokens and not cfg.allow_insecure:
        logger.warning(
            "No GATEWAY_DEVICE_TOKEN(S) configured. Set GATEWAY_ALLOW_INSECURE=1 for "
            "local dev, or configure tokens for production. All connections will be "
            "REJECTED until then."
        )
    return cfg
