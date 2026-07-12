"""Cloudflare Workers AI FLUX adapter — primary AI image generator.

Pattern copied from ktb-story-teller's ``core/images/cloudflare.py``:
multi-account rotation with 429 fall-through so one account's daily 10k
Neuron quota burning out automatically rolls to the next account.

Endpoint: ``POST /accounts/{acct}/ai/run/@cf/black-forest-labs/flux-1-schnell``
Auth: ``Authorization: Bearer {token}``

Env var formats:
  - ``CF_ACCOUNTS="acct1:tok1;acct2:tok2"`` (preferred — rotation)
  - ``CF_ACCOUNT_ID`` + ``CF_AI_TOKEN`` (single account fallback)

Cost: flux-schnell ~5 Neurons/image, free tier = 10000 Neurons/day per
account → 2000+ images/day per account.

Limitations carried over from story-teller:
  * FLUX trains at 1024×1024 — we generate at the sweet spot and let
    the renderer crop with ``scale + crop`` in ffmpeg.
  * NSFW safety filter occasionally returns a fully-black PNG with 200;
    we detect via mean luminance and treat as a soft failure → caller
    falls through to next provider.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import logging
import os
import random
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import httpx

logger = logging.getLogger("tella.media.ai_image")

DEFAULT_MODEL = "@cf/black-forest-labs/flux-1-schnell"
DEFAULT_STEPS = 4
# Generate smaller than the final canvas — the renderer upscales/crops to
# 1080×1920 anyway, and a smaller image costs fewer Neurons, so a free CF
# account stretches across far more scenes before hitting its daily cap.
DEFAULT_WIDTH = 768
DEFAULT_HEIGHT = 1344

HTTP_TIMEOUT = 60.0
MAX_RETRIES_PER_ACCOUNT = 3
RETRY_BACKOFF_SECONDS = 2.0

# Global request throttle — CF rate-limits bursts, so we space out calls
# across the whole process (all scenes share this), with a little jitter so
# concurrent scenes don't align into a thundering herd.
_MIN_REQUEST_INTERVAL = 0.45
_throttle_lock = asyncio.Lock()
_last_request_at = 0.0
_before_request_hook: contextvars.ContextVar[
    Callable[[], Awaitable[None]] | None
] = contextvars.ContextVar("cloudflare_before_request_hook", default=None)


def _positive_env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("invalid %s=%r; using %d", name, raw, default)
        return default


class CloudflareAIError(RuntimeError):
    """Structured Cloudflare Workers AI failure."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "unknown",
        status_code: int = 0,
        recoverable: bool = True,
        policy_code: int = 0,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code
        self.recoverable = recoverable
        self.policy_code = policy_code


@contextlib.contextmanager
def cloudflare_request_hook(
    hook: Callable[[], Awaitable[None]],
) -> Iterator[None]:
    """Install a task-local callback immediately before each CF HTTP request."""
    token = _before_request_hook.set(hook)
    try:
        yield
    finally:
        _before_request_hook.reset(token)


async def _notify_before_cloudflare_request() -> None:
    hook = _before_request_hook.get()
    if hook is not None:
        await hook()


def _cloudflare_policy_code(status_code: int, body: str) -> int:
    if status_code != 400:
        return 0
    if re.search(r'"code"\s*:\s*3030\b', body or ""):
        return 3030
    return 3030 if "3030" in (body or "") else 0


def classify_cloudflare_error(status_code: int, body: str) -> tuple[str, bool]:
    text = (body or "").lower()
    if (
        "3030" in text
        or "input prompt contains nsfw content" in text
        or "nsfw" in text
    ):
        return "content_policy_blocked", True
    if status_code == 429:
        if (
            "daily free allocation" in text
            or "used up your daily" in text
            or "quota" in text
            or "neurons" in text
        ):
            return "quota_exhausted", False
        return "rate_limited", False
    if status_code in {401, 403}:
        return "auth_error", False
    if status_code in {402, 4020} or "payment" in text or "billing" in text:
        return "payment_required", False
    return "provider_http_error", True


async def _throttle() -> None:
    """Space CF requests at least ``_MIN_REQUEST_INTERVAL`` apart, plus jitter."""
    global _last_request_at
    async with _throttle_lock:
        now = time.monotonic()
        wait = _MIN_REQUEST_INTERVAL - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()
    # Jitter outside the lock so callers fan out instead of firing in lockstep.
    await asyncio.sleep(random.uniform(0.0, 0.25))


def resolve_all_credentials() -> list[tuple[str, str]]:
    """Return every known ``(account_id, api_token)`` pair, shuffled.

    Same env-var convention as story-teller — a deployment can share one
    .env across both tools.
    """
    accounts_csv = (os.environ.get("CF_ACCOUNTS") or "").strip()
    pairs: list[tuple[str, str]] = []
    if accounts_csv:
        for piece in accounts_csv.split(";"):
            piece = piece.strip()
            if ":" not in piece:
                continue
            aid, tok = piece.split(":", 1)
            aid, tok = aid.strip(), tok.strip()
            if aid and tok:
                pairs.append((aid, tok))

    if not pairs:
        account_id = (os.environ.get("CF_ACCOUNT_ID") or "").strip()
        api_token = (os.environ.get("CF_AI_TOKEN") or "").strip()
        if account_id and api_token:
            pairs.append((account_id, api_token))

    if pairs:
        random.shuffle(pairs)
    return pairs


async def generate_image(
    prompt: str,
    out_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    steps: int = DEFAULT_STEPS,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    seed: int | None = None,
) -> Path:
    """Generate one image and save to ``out_path``. Returns ``out_path`` on success.

    Raises:
        RuntimeError: when all configured accounts fail. Callers should
            catch and fall through to the next provider (stock photo /
            placeholder).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    creds = resolve_all_credentials()
    if not creds:
        raise RuntimeError(
            "CF AI: no credentials (set CF_ACCOUNTS or CF_ACCOUNT_ID + CF_AI_TOKEN)"
        )
    account_limit = _positive_env_int("TELLA_CF_MAX_ACCOUNTS", len(creds))
    creds = creds[:account_limit]
    attempt_limit = _positive_env_int(
        "TELLA_CF_MAX_RETRIES_PER_ACCOUNT",
        MAX_RETRIES_PER_ACCOUNT,
    )

    payload: dict = {
        "prompt": (prompt or "").strip(),
        "steps": steps,
        "width": width,
        "height": height,
    }
    if seed is not None:
        payload["seed"] = int(seed)

    last_err: Exception | None = None
    for cred_idx, (aid, tok) in enumerate(creds, 1):
        url = f"https://api.cloudflare.com/client/v4/accounts/{aid}/ai/run/{model}"
        headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
        quota_exhausted = False
        for attempt in range(1, attempt_limit + 1):
            try:
                await _throttle()
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    await _notify_before_cloudflare_request()
                    resp = await client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "json" in content_type:
                        data = resp.json()
                        if not data.get("success", True):
                            errors = data.get("errors", [])
                            raise RuntimeError(f"CF AI errors: {errors}")
                        result = data.get("result", {})
                        b64 = result.get("image") or result.get("image_b64")
                        if not b64:
                            raise RuntimeError(
                                f"CF AI 200 JSON missing image: keys={list(result)}"
                            )
                        img_bytes = base64.b64decode(b64)
                        out_path.write_bytes(img_bytes)
                    elif resp.content:
                        out_path.write_bytes(resp.content)
                    else:
                        raise RuntimeError("CF AI 200 with empty body")

                    if _is_blank_or_black(out_path):
                        with contextlib.suppress(OSError):
                            out_path.unlink()
                        raise RuntimeError(
                            "CF AI returned blank/black image (likely NSFW filter)"
                        )

                    logger.info(
                        "cf-ai saved %s (%d KB, account %d/%d, attempt %d)",
                        out_path.name, out_path.stat().st_size // 1024,
                        cred_idx, len(creds), attempt,
                    )
                    return out_path

                error_type, recoverable = classify_cloudflare_error(
                    resp.status_code, resp.text
                )
                last_err = CloudflareAIError(
                    f"CF AI HTTP {resp.status_code} (account {cred_idx}/{len(creds)}): "
                    f"{resp.text[:200]}",
                    error_type=error_type,
                    status_code=resp.status_code,
                    recoverable=recoverable,
                    policy_code=_cloudflare_policy_code(resp.status_code, resp.text),
                )
                logger.warning("cf-ai attempt %d -> %s", attempt, last_err)
                if last_err.policy_code == 3030:
                    raise last_err
                if resp.status_code == 429:
                    quota_exhausted = True
                    break
                if resp.status_code in (401, 403):
                    break  # bad creds, try next account
            except (httpx.HTTPError, httpx.ReadTimeout) as exc:
                last_err = exc
                logger.warning("cf-ai network err attempt %d: %s", attempt, exc)
            if attempt < attempt_limit:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
        if quota_exhausted:
            logger.info("cf-ai account %d quota exhausted, rotating", cred_idx)

    if isinstance(last_err, CloudflareAIError):
        raise CloudflareAIError(
            f"CF AI failed across all {len(creds)} account(s): {last_err}",
            error_type=last_err.error_type,
            status_code=last_err.status_code,
            recoverable=last_err.recoverable,
            policy_code=last_err.policy_code,
        ) from last_err
    raise CloudflareAIError(
        f"CF AI failed across all {len(creds)} account(s): {last_err}",
        error_type="provider_failed",
        recoverable=True,
    )


def _is_blank_or_black(path: Path, *, dark_threshold: int = 16, ratio: float = 0.95) -> bool:
    """``True`` when ``path`` is mostly black / blank — likely NSFW filter."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            img = img.convert("L")
            img.thumbnail((64, 64))
            pixels = list(img.getdata())
            if not pixels:
                return True
            dark = sum(1 for p in pixels if p < dark_threshold)
            mean = sum(pixels) / len(pixels)
            return (dark / len(pixels) >= ratio) or (mean < dark_threshold)
    except (OSError, ValueError):
        try:
            return path.stat().st_size < 2048
        except OSError:
            return True


__all__ = [
    "DEFAULT_MODEL",
    "CloudflareAIError",
    "classify_cloudflare_error",
    "cloudflare_request_hook",
    "generate_image",
    "resolve_all_credentials",
]
