# Tella local web production contract

## Data flow

```text
Browser
  -> POST /api/jobs (validated WebRenderRequest)
  -> persisted parent JobManager queue
  -> one isolated child process per active tella.cli.run_pipeline(...)
  -> existing planner/media/TTS/composition/subtitle/render pipeline
  -> {job_id}/video.mp4
  -> ffprobe audio + video verification
  -> GET /api/jobs/{job_id}/preview or /download
```

`run_pipeline` remains the sole production render entry point. The web layer
does not invoke dry-run, placeholder, preview-only, or test render paths.

## HTTP API

- `GET /api/health` — local readiness for Gemini planning and narration, the
  Cloudflare AI-image route, FFmpeg, FFprobe, and the output directory. The web
  contract accepts only `media_source=ai_image`.
- `POST /api/jobs` — validate the request, apply the same request-specific
  readiness checks, then enqueue one production render. A local readiness
  failure returns `503 PRODUCTION_NOT_READY` and creates no job.
- `GET /api/jobs/{job_id}` — persisted state, phase, progress, logs, safe
  request projection, error, and validated artifact metadata.
- `GET /api/jobs` — newest-first persisted history used for refresh recovery.
- `POST /api/jobs/{job_id}/cancel` — cancel a queued job only.
- `GET /api/jobs/{job_id}/preview` — registered MP4 with range support.
- `GET /api/jobs/{job_id}/download` — registered MP4 with
  `Content-Disposition: attachment`.

The default bind address is `127.0.0.1:8787`.

## Input mapping

| Web field | `run_pipeline` argument |
|---|---|
| topic content | `topic` |
| exact script content | `user_script` |
| language | `target_lang` |
| theme | `theme` |
| visual source | fixed `media_source="ai_image"` |
| duration | `duration_mode` |
| aspect | `aspect_ratio` |
| narrator | backend-owned strict Gemini profile |
| pace | `voice_pace_name` |
| gender | `voice_gender` |

## Progress phases

Progress advances only from emitted pipeline events:

1. `step 1/6` — input translation or canonical-script handling (`10%`)
2. `step 2/6` — story planning (`25%`)
3. `step 3/6` — visual acquisition (`40%`)
4. `step 4/6` — Gemini narration synthesis (`55%`)
5. `step 5/6` — timing composition (`70%`)
6. `step 6/6` — FFmpeg render (`85%`)
7. MP4 validation (`95%`)
8. persisted success only after FFprobe confirms audio and video (`100%`)

## Required server environment

- `GEMINI_API_KEY`, `GEMINI_API_KEYS`, or `GOOGLE_API_KEY`
- Cloudflare credentials for the primary image route
- optional `POLLINATIONS_API_KEY` for the safe image fallback
- optional `TELLA_WEB_MAX_WORKERS=1|2`, default `1`
- `ffmpeg` and `ffprobe` on `PATH`
- writable `TELLA_WEB_OUTPUT_DIR`, default `out/web_jobs`

The web adapter binds each job to the existing registered
`gemini_callirrhoe_vi_gentle_emotional` profile and sets
`TELLA_TTS_PROVIDER=gemini` plus `TELLA_STRICT_TTS_PROVIDER=1`. Provider, model,
voice, style, URL, and credentials are server-owned. Missing or failed Gemini
TTS is a terminal job error; Edge fallback is forbidden. The existing Google
adapter remains available outside the web profile and does not support service
accounts.

Cloudflare is the primary still-image provider. Pollinations is an optional
fallback only for scenes carrying a current backend-minted `PUBLIC_SAFE`
authority and only after rate-limit, quota, provider-availability, or timeout
failures. `PRIVATE` remains Cloudflare-only; `LOCAL_ONLY` prohibits external
providers. Missing, malformed, or stale authority fails before provider work.
Both 9:16 and 16:9 responses are decoded and dimension-checked without silent
cropping. Pexels, local/reused assets, placeholder sprites, and AI-video remain
disabled by the web environment. Legacy CLI behavior remains unchanged.
Renderer zoom-out alignment remains server-owned: every generated still uses
the validated `practical_pull_back` profile. Typical 4.5–7 second scene and
32–38 second short-video ranges are advisory only. Continuous narration and
existing composition determine timing, while FFprobe supplies the authoritative
final duration. No provider, privacy, motion, or raw FFmpeg control is accepted
from the browser.

Readiness is a local configuration gate. It does not call providers and does
not guarantee that an external provider, balance, quota, or network will remain
available when a queued job executes. Pollinations readiness validates local
configuration, geometry, and sensitivity policy only; it does not query live
Pollen balance.

## Web execution isolation

The parent owns the queue, persisted public state, and final artifact
publication. Every active job runs in a dedicated child process which calls
`tella.cli.run_pipeline(...)` directly with a validated request and a
server-built environment. Gemini narration, image generation, reuse, local
fallback, placeholder behavior, visual QC, and branding policy cannot leak
between children or be overridden by browser input. `TELLA_WEB_MAX_WORKERS`
accepts only `1` or `2` and defaults to `1`; invalid values fail readiness.
Child logs and terminal results cross a bounded JSON-line IPC channel. A crash,
malformed event, non-zero exit, or missing/invalid artifact fails closed before
publication.

The request parser rejects missing, invalid, negative, empty, and oversized
body lengths. Connections are explicitly closed when an invalid or oversized
declaration could leave attacker-controlled bytes unread. Valid requests keep
normal HTTP/1.1 behavior.

Shutdown stops new submissions and persists queued jobs as cancelled. It first
allows bounded graceful completion, then terminates the exact owned child
process trees if the deadline expires. Completion is reported only after no
owned worker process remains.

## Persistence and security

Each job is stored under `out/web_jobs/{job_id}/job.json`. Only the final MP4
registered by a succeeded job may be served. Resolved containment, job-ID
validation, MIME, and artifact existence are checked before serving. Server
credentials and raw environment values are never returned. Browser-facing
logs and errors pass through one bounded sanitizer before persistence and
again when records are loaded. It removes configured credential values,
authorization/token forms, secret query parameters, provider response bodies,
and private absolute paths while preserving stable error codes and actionable
messages.

## Browser state and recovery

The UI exposes topic/exact-script input, supported language, aspect ratio,
theme, duration, and music controls. It does not expose stock, AI-video,
provider, credential, URL, model, privacy, or raw-voice controls. Gemini
narration, Cloudflare primary image generation, and eligible Pollinations
fallback remain server-managed.

On bootstrap the browser loads readiness and `GET /api/jobs`. It remembers at
most one validated `web-...` job ID in local storage, never narration, logs,
credentials, or provider metadata. Recovery selects the remembered job when it
still exists, otherwise the newest active job, otherwise the newest completed
job. Refresh does not create work.

Polling starts near one second, backs off to five seconds while state is
unchanged, resets on real server changes, avoids overlapping requests, and
stops for terminal or unknown states. Three consecutive transport failures
pause automatic updates and expose manual refresh. Retry is limited to the
safe stored request of failed/cancelled jobs and always creates a new job.
Only queued jobs expose cancellation; running renders are not forcibly
terminated.

The UI treats `100%` and artifact controls as valid only when a succeeded job
contains positive duration, audio/video stream counts, and canonical job-ID
preview/download routes. Preview errors are visible and do not weaken the
download authority boundary.
