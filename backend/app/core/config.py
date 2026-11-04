"""Application configuration loaded from environment variables.

All secrets live in the backend process environment (see the repository-root
``.env`` file) and are never exposed to the frontend. Import
:func:`get_settings` to access the cached settings object.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """FIELD backend settings. See ``.env.example`` for documentation."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "sqlite:///./field.db"
    frontend_origin: str = "http://localhost:5173"

    # Meta Model API (Muse Spark reasoning + Voice Transcribe).
    meta_model_api_key: str = ""
    meta_model_base_url: str = ""
    muse_spark_model_id: str = ""
    muse_transcribe_model_id: str = ""

    # Modal / Muse Glimmer (server-side only).
    modal_endpoint_url: str = ""
    modal_api_key: str = ""

    # E2B (server-side only).
    e2b_api_key: str = ""

    # Azure Speech (optional TTS).
    azure_speech_key: str = ""
    azure_speech_region: str = ""

    media_storage_path: Path = Path("./uploads")
    max_upload_size_mb: int = Field(default=100, ge=1, le=2048)

    @field_validator("frontend_origin", "modal_endpoint_url", "meta_model_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/") if value else value

    @property
    def meta_configured(self) -> bool:
        """True when a real Muse Spark call can be attempted."""
        return bool(self.meta_model_api_key and self.muse_spark_model_id)

    @property
    def transcribe_configured(self) -> bool:
        """True when a real transcription call can be attempted."""
        return bool(self.meta_model_api_key and self.muse_transcribe_model_id)

    @property
    def glimmer_configured(self) -> bool:
        """True when a real Glimmer audit call can be attempted."""
        return bool(self.modal_endpoint_url and self.modal_api_key)

    @property
    def e2b_configured(self) -> bool:
        """True when a real E2B sandbox call can be attempted."""
        return bool(self.e2b_api_key)

    @property
    def speech_configured(self) -> bool:
        """True when Azure TTS can be attempted."""
        return bool(self.azure_speech_key and self.azure_speech_region)


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings, validating required values at startup."""
    return Settings()  # type: ignore[call-arg]
