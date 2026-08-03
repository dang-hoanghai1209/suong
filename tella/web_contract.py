"""Public contracts for the local production web application."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

WEB_API_VERSION = 1
MAX_WEB_INPUT_CHARS = 10_000
MAX_WEB_REQUEST_BYTES = 32 * 1024


class WebContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WebInputMode(StrEnum):
    TOPIC = "topic"
    SCRIPT = "script"


class WebJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WebRenderRequest(WebContract):
    schema_version: Literal[1] = 1
    input_mode: Literal["topic", "script"]
    content: str = Field(min_length=1, max_length=MAX_WEB_INPUT_CHARS)
    language: Literal["vi", "en", "es", "fr", "de", "ja", "ko", "zh", "pt", "it"]
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    media_source: Literal["ai_image"] = "ai_image"
    theme: Literal[
        "parable",
        "cinematic",
        "playful",
        "mindfulness",
        "minimalist_emotional",
        "minimalist_symbolic_reel",
        "life_insight_symbolic",
        "practical_life_steps",
    ] = "cinematic"
    duration_mode: Literal["short", "detailed"] = "short"
    no_music: StrictBool = False

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the integer 1")
        return value

    @model_validator(mode="after")
    def canonical_values(self) -> WebRenderRequest:
        if self.content != self.content.strip():
            raise ValueError("content must not contain leading or trailing whitespace")
        return self


class WebJobError(WebContract):
    code: str
    message: str


class WebJobView(WebContract):
    schema_version: Literal[1] = 1
    job_id: str
    status: WebJobStatus
    phase: str
    progress: int = Field(ge=0, le=100)
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    logs: tuple[str, ...] = ()
    error: WebJobError | None = None
    output_available: StrictBool = False
    preview_url: str | None = None
    download_url: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    video_streams: int = Field(default=0, ge=0)
    audio_streams: int = Field(default=0, ge=0)
    output_bytes: int | None = Field(default=None, gt=0)
    plan_metadata: dict[str, object] | None = None
    tts_metadata: dict[str, object] | None = None
    request: WebRenderRequest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "MAX_WEB_INPUT_CHARS",
    "MAX_WEB_REQUEST_BYTES",
    "WEB_API_VERSION",
    "WebInputMode",
    "WebJobError",
    "WebJobStatus",
    "WebJobView",
    "WebRenderRequest",
    "utc_now",
]
