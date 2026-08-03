# Tella

**An AI video content maker — turn any topic into a finished video, fully on your own machine.**

Drop in a topic (or your own `.txt` script). Tella plans the video scene
by scene, narrates it, sources or generates the visuals, and renders a
finished MP4. Built for **any subject, any channel, any purpose** —
educational explainers, motivational shorts, brand promos, news recaps,
language-learning clips, listicles, fables for kids… whatever you need
to ship today. Runs on a free-tier AI stack (no subscriptions), and
every file stays on your hard drive.

```
You type:        "5 study habits backed by neuroscience"
Tella gives you: video.mp4   (1080×1920 or 1920×1080, narrated, ready to post)
```

## Why Tella

One command, one topic, one finished AI-narrated video — no manual
editing, no subscription stack, no cloud upload. What Tella brings:

- **Channel-scoped auto-topics.** Point Tella at a channel folder with a
  `niche_guide` + `seed_examples` and it brainstorms a fresh topic on
  brand, dedup'd against everything you've already shipped via embedding
  similarity. Useful for daily-publishing channels that don't want to
  hand-write briefs forever.
- **Multi-character consistency.** In AI-image mode every recurring
  character *and* the setting are locked across scenes — a two-character
  fable keeps both characters consistent, an explainer keeps the same
  host on screen all the way through.
- **8 native TTS languages** — English, Vietnamese, Japanese, Korean,
  Chinese, German, French, Spanish; auto-detected when you drop a script.
- **Bring-your-own-script mode.** Drop a `.txt` and Tella narrates your
  exact words (no rewrite, just scene-split + visuals + timing).
- **Pace adapts to the content** — children's tales narrate slowly and
  warmly, explainers narrate at a clear steady clip.
- **100% on your machine, free-tier first.** Free Gemini + Edge TTS +
  Cloudflare Workers AI / Pexels + ffmpeg; nothing uploaded to a third
  party, no monthly bill.

### What you can make

A non-exhaustive sample — all out of the same pipeline:

| Use case | Topic example | Tip |
|---|---|---|
| Educational explainer | `how compound interest actually works` | `--theme cinematic`, detailed mode |
| Motivational short | `the discipline of showing up early` | short mode |
| News / topic recap | `what's behind the global chip shortage` | stock_video media |
| Brand / product promo | `5 reasons our gel polish kit beats salon visits` | channel scope + 9:16 |
| Listicle | `7 underrated cities in Vietnam` | stock_photo media |
| Fable for kids | `the tortoise and the hare` | `--theme playful`, cartoon style |
| Language-learning clip | `most common French phrases for travel` | French narration, 9:16 |
| Mindfulness reflection | `letting go without giving up` | `--theme mindfulness` |

Tella doesn't lock you into "story videos" — pick any of the
[content styles](#themes-advanced) and the same pipeline adapts.

## Live demo

There's a hosted quick demo at **https://app.khuetran.com/tella** if you just
want to see what Tella does without installing anything.

⚠️ The demo runs on a small shared web VPS, so **rendering there is slow** — it's
only a quick preview of the features. Running this repo **on your own machine is
much faster** (your own CPU + your own free API keys, no shared queue). For real
use, clone and run locally.

### At a glance

- **Two ways in**: type a **topic** (Tella writes the script) or drop a **`.txt`
  file** (Tella narrates *your* script word-for-word)
- **9:16** vertical (TikTok / Reels / Shorts) or **16:9** horizontal (YouTube)
- Visuals from **AI image**, **stock photo**, or **stock video**. AI image
  offers two styles: **cinematic** (realistic/filmic) or **cartoon**
  (kid-friendly illustration)
- **Optional channel branding** — small channel name (and a circular
  avatar) on every scene. Save channels under `channels/` and pick one in
  the wizard, type a fresh name, or leave it off for a clean unbranded video
- **Length**: topic mode is short (~60–120s) or detailed (~4–6 min); a
  dropped `.txt` runs as long as it needs, split into scenes automatically
- Free narration via Edge TTS (no key needed); optional Google Chirp 3 HD upgrade

---

## Quick start

### 1. Requirements

| Tool | Why | Install |
|---|---|---|
| Python 3.12+ | Runs the pipeline | [python.org](https://www.python.org/downloads/) — check **"Add Python to PATH"** |
| ffmpeg | Audio + video composition, Ken Burns, transitions | Win: `winget install Gyan.FFmpeg` · Mac: `brew install ffmpeg` · Linux: `apt install ffmpeg` |

### 2. Setup (one-time)

```
Windows:    SETUP.bat
Mac/Linux:  ./SETUP.sh
```

SETUP creates a virtual env under `.venv/` and installs dependencies. Then copy
`.env.example` to `.env` and fill in your keys (see [Keys](#keys) below).

The only **required** key is `GEMINI_API_KEY` (free, generous quota) —
grab one at <https://aistudio.google.com/apikey> in about 30 seconds.

### 3. Run

```
Windows:    RUN.bat
Mac/Linux:  ./RUN.sh
```

With no arguments you get the guided wizard:

```
============================================================
  Tella - turn a topic (or your own story) into a video
============================================================

Step 1 - Your story
  Type a TOPIC for Tella to write about, e.g.
     the tortoise and the hare
  OR drop a .txt file here (your own finished story) and press Enter.
Topic or file: the tortoise and the hare

Step 2 - Narration language
  * 1) Tieng Viet   2) English   3) Japanese  ...
Choose [1]: 2

Step 3 - Aspect ratio
  * 1) Vertical short  (TikTok / Reels / YouTube Shorts)
    2) Horizontal      (YouTube / landscape)
Choose [1]:

Step 4 - Where do the visuals come from?
  * 1) AI image  - characters stay consistent across scenes
    2) Stock photo - real Pexels photographs, fast
    3) Stock video - real Pexels video clips, most motion
Choose [1]:

Step 5 - AI image style   (only if you picked AI image)
  * 1) Cinematic - realistic, filmic
    2) Cartoon   - colorful illustration, kid-friendly
Choose [1]:

Step 6 - How long?        (short ~90s / detailed 4-6 min)
Step 7 - Narrator voice   (male / female)

------------------------------------------------------------
  Ready to render:
    Topic     : the tortoise and the hare
    Language  : English
    Aspect    : 9:16
    Visuals   : AI image
    Length    : Short
    Voice     : Male voice
------------------------------------------------------------
Start? [Y/n]:
```

The finished MP4 lands in `out/<timestamp>_<slug>/video.mp4`.

### Bring your own story (drop a `.txt`)

At Step 1, instead of a topic, drag a `.txt` file into the window (or paste its
path) and press Enter. Tella then:

- narrates **your exact words** — it doesn't rewrite the story, only splits it
  into scenes and adds visuals + timing;
- **cleans the text for narration** first — ellipses (`...`), repeated `!!!`,
  smart quotes, stray dashes and markdown are normalised so the voice doesn't
  stumble on them;
- **auto-detects the language** and picks a matching voice;
- runs **as long as the story needs** — there's no fixed length; it splits into
  as many scenes as the pacing calls for, so no single scene drones on.

---

## CLI (for power users / automation)

Pass any flag to skip the wizard:

```bash
python -m tella \
  --topic "the lighthouse keeper who learned to rest" \
  --lang en \
  --aspect 9:16 \
  --media ai_image \
  --duration short \
  --theme cinematic \
  --out ./out
```

| Flag | Choices | Default |
|---|---|---|
| `--topic` | any text (required) | — |
| `--lang` | `vi en ja ko zh de fr es` (required) | — |
| `--aspect` | `9:16` `16:9` | `9:16` |
| `--media` | `ai_image` `stock_photo` `stock_video` | `ai_image` |
| `--duration` | `short` `detailed` | `short` |
| `--theme` | `cinematic` `parable` `playful` `mindfulness` | `cinematic` |
| `--gender` | `male` `female` | theme default |
| `--out` | output dir | `./out` (or `$TELLA_OUTPUT_DIR`) |

---

## How visuals work

**AI image** (`ai_image`) — the planner first writes a **cast** of subject
briefs and a **setting brief**. For a fable that might be a tortoise and a
hare in a sun-drenched forest; for a money explainer it might be a young
professional and an envelope of bills in a cosy apartment; for a brand promo
it can be the product itself, locked across every scene. Each scene declares
which cast members appear in it, and those identities + the setting are
prepended to that scene's image prompt — so Cloudflare Workers AI (FLUX)
renders the **same subjects in the same world across all scenes**. A
multi-character video keeps *every* character consistent, and the cast is
drawn faithfully (an animal stays an animal — it is never swapped for a
human stand-in; a product stays the product). This is what keeps a video
visually coherent instead of looking like a pile of unrelated pictures.

**Stock photo / video** (`stock_photo`, `stock_video`) — pulls real Pexels
media per scene. Character locking isn't possible with random stock, so Tella
keeps coherence with consistent color grading, transitions, and overlay style
instead. Fast, realistic, and free.

---

## Channels (branding + optional scope)

To brand your videos, save a channel once and pick it in the wizard. Create a
folder under `channels/`:

```
channels/
  my-brand/
    channel.json     ->  {"name": "My Brand"}
    avatar.png        ->  optional square logo/face (shown as a small circle)
```

The wizard lists saved channels alongside "type a new name" and "no channel".
Only the **name** is shown on screen (no handle/slug). An example channel ships
under `channels/example/`.

### Scoped channels — let Tella pick the topic for you

If you give a channel a `niche_guide` + `seed_examples` + `defaults`, the
wizard unlocks an **Auto AI topic** option for that channel. Gemini brainstorms
a fresh topic in your channel's voice and dedups it against every previous
topic via embedding cosine similarity — so even on episode 200 you won't get a
rephrased rerun. Used topics are written to `channels/<slug>/history.jsonl`
after the render succeeds (failed renders don't burn a topic).

A worked example ships under `channels/cosmos-fiction/` — a speculative-cosmology
Vietnamese channel. The full schema:

```json
{
  "name": "My Brand",
  "niche_guide": "one-paragraph description of what this channel is about",
  "seed_examples": ["example topic 1", "example topic 2"],
  "defaults": {
    "lang": "vi",                 // vi/en/ja/ko/zh/de/fr/es
    "media": "ai_image",          // ai_image / stock_photo / stock_video
    "style": "cinematic",         // cinematic / cartoon (ai_image only)
    "voice_gender": "male",       // male / female
    "duration": "short",          // short / detailed
    "aspect": "9:16"              // 9:16 / 16:9
  }
}
```

When a scoped channel is picked, the wizard hides the per-step questions
covered by `defaults` and only asks: AI auto-topic, type your own, or drop a
`.txt`. Channels without `niche_guide` keep the old behavior — just a name
and avatar, the full 7-step wizard runs.

## Themes (advanced)

Themes are content-style presets — they shape narrator tone, vocabulary, and
imagery so the same topic can ship as a clear-eyed explainer, a meditative
parable, or a kids' cartoon. The wizard always uses **cinematic** (the most
versatile look — good for explainers, promos, news, motivational, listicles).
To pick a different style, pass `--theme` on the CLI:

| Theme | Tone | Imagery | Good for |
|---|---|---|---|
| `cinematic` (default) | Clear, vivid presenter | Photorealistic, film grain, teal-orange grade | Explainers, promos, listicles, news recaps, motivational |
| `parable` | Meditative third-person fable | Watercolor, Studio-Ghibli-inspired | Allegories, life lessons, slow takes |
| `mindfulness` | Calm dharma-talk reflection | Recurring monk character, warm watercolor | Meditation prompts, reflection, wellness |
| `playful` | Upbeat children's-book read-aloud | Vibrant cartoon, bold colors | Fables for kids, edutainment, vocab clips |

---

## Keys

See `.env.example`. Only `GEMINI_API_KEY` is required; everything else
unlocks an upgrade in its provider chain.

| Key | Unlocks | Free? |
|---|---|---|
| `GEMINI_API_KEY` | **Required** — story planning + translation | ✅ Free tier |
| `CF_ACCOUNTS` (or `CF_ACCOUNT_ID`+`CF_AI_TOKEN`) | AI image mode (Cloudflare Workers AI / FLUX) | ✅ 10k images/day/account |
| `PEXELS_API_KEY` | Stock photo + stock video modes | ✅ 200 req/hr, 20k/mo |
| `GOOGLE_TTS_API_KEY` | Optional legacy/CLI Google TTS adapter | Pay-as-you-go (tiny) |

Narration falls back to **Edge TTS**, which needs no key at all — so the
minimum to render a complete video is just `GEMINI_API_KEY` + one image
source (`PEXELS_API_KEY` is the easiest).

**Cloudflare multi-account rotation:** `CF_ACCOUNTS` takes a
semicolon-separated list of `account_id:api_token` pairs. Each free account
gives ~2000 FLUX images/day, so a few accounts let you render all day for $0.

---

## Output

```
out/
└── 20260627_174751_the_lighthouse_keeper/
    ├── video.mp4          ← the finished video
    ├── plan.json          ← scene-by-scene plan (story, prompts, timing)
    ├── assets/            ← per-scene images + narration mp3s
    └── _render/           ← intermediate scene clips (safe to delete)
```

Delete a job's `assets/` and `_render/` once you have the `video.mp4` to
reclaim disk.

---

## Local production web UI

The web UI invokes the same `tella.cli.run_pipeline` entry point as the CLI.
It uses a single persisted worker because provider selection is process-wide,
and it marks a job complete only after `ffprobe` confirms that the MP4 contains
positive-duration audio and video streams.

```powershell
Copy-Item .env.example .env
# Set GEMINI_API_KEY and one media provider.
uv sync --extra dev
uv run tella-web
```

Open `http://127.0.0.1:8787`. On macOS/Linux, use `cp .env.example .env`.
Jobs and their crash-safe `job.json` records are stored under
`TELLA_WEB_OUTPUT_DIR` (default `out/web_jobs`).

The production web profile uses backend-owned Gemini narration:

- `GEMINI_API_KEY`, `GEMINI_API_KEYS`, or `GOOGLE_API_KEY` supplies the existing
  Gemini planner and narration credential.
- The browser cannot select a provider, key, URL, model, or raw voice.
- The server selects the registered
  `gemini_callirrhoe_vi_gentle_emotional` profile and applies strict provider
  mode. Gemini errors fail the job and never fall back to Edge TTS.
- The existing Google Cloud TTS adapter remains available to CLI callers, but
  it is not part of the production web profile. Service-account authentication
  is not implemented by that adapter.

`GET /api/health` reports local credential/configuration presence for Gemini
and the default AI-image route, `ffmpeg`, `ffprobe`, and output-directory
writability without returning secret values. The production web UI is
still-image-only: it always submits `media_source="ai_image"`; stock modes
remain available to the CLI but are not exposed or accepted by the web API.
`POST /api/jobs` applies readiness before queueing and returns `503
PRODUCTION_NOT_READY` without creating a job when a prerequisite is missing.
These local checks do not contact providers or guarantee later provider,
balance, quota, or network availability. In particular, Pollinations readiness
confirms local configuration and authority compatibility, not live Pollen
balance.

The parent `JobManager` owns the queue, persistence, and artifact publication.
Each active render runs `tella.cli.run_pipeline(...)` in a dedicated child
process with a server-built environment that neutralizes inherited settings
which could select another narrator, reuse/skip assets, allow local or
placeholder fallbacks, or alter the web profile. `TELLA_WEB_MAX_WORKERS` accepts
only `1` or `2` and defaults to `1`; the browser cannot set it. Browser-visible
logs and errors redact credentials, authorization tokens, secret query
parameters, provider response bodies, and private absolute paths.

Invalid or oversized HTTP bodies are rejected with structured errors; the
connection closes when unread bytes could otherwise remain on HTTP/1.1.
Shutdown rejects new work, safely cancels queued jobs, waits for active work,
and explicitly reports an incomplete timeout rather than claiming the worker
stopped. A failed or interrupted job can be inspected in the UI; only a
validated successful MP4 is available through preview and download routes.

The production web profile uses Cloudflare as its primary still-image provider.
When `POLLINATIONS_API_KEY` is configured, Pollinations is an optional fallback
for backend-authorized `PUBLIC_SAFE` scenes after Cloudflare rate-limit, quota,
availability, or timeout failures. `PRIVATE` scenes remain Cloudflare-only and
`LOCAL_ONLY` scenes never call an external provider. Missing or stale authority
fails closed. Both 9:16 and 16:9 are validated without silent cropping. Pexels,
local/reused assets, placeholder sprites, and AI-video remain disabled in the
web profile; legacy CLI behavior is unchanged.

Every generated still in the web profile receives the server-owned
`practical_pull_back` motion. It starts slightly enlarged and moves gently
toward the fitted frame. A scene commonly lasts about 4.5–7 seconds and a short
video may land around 32–38 seconds, but neither range is enforced: continuous
narration and existing composition rules determine timing, and FFprobe reports
the authoritative final duration. The browser exposes neither provider nor
motion controls.

The UI loads persisted job history on startup, recovers the remembered or
newest relevant job, supports queued-job cancellation, and retries failed or
cancelled jobs as new IDs. Adaptive polling is based only on server state and
stops at terminal states; it never simulates progress. Browser storage holds
only the selected job ID. Provider selection, privacy classification, keys, and
provider URLs remain server-owned.

For a credential-gated real-render test:

```powershell
$env:TELLA_WEB_REAL_E2E = "1"
uv run --extra dev python -m pytest tests/test_web_production.py -k real_web_render_e2e -q
```

If readiness reports setup errors, check that the environment is loaded in the
server process, both FFmpeg tools are on `PATH`, and `TELLA_WEB_OUTPUT_DIR` is
writable.

Real-production acceptance has been completed with a newly generated MP4 whose
audio/video streams, preview, download, restart persistence, and serialized
queue behavior were validated end to end.

---

## Cost

A typical short uses Gemini planning and narration plus the configured visual
provider and FFmpeg. Provider pricing and quota depend on the configured
accounts. The web profile does not silently switch narration to Edge.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python not found` during SETUP | Reinstall Python with "Add Python to PATH" checked |
| `ffmpeg not found` | Win: `winget install Gyan.FFmpeg` · Mac: `brew install ffmpeg` · Linux: `apt install ffmpeg` |
| `GEMINI_API_KEY missing` | Copy `.env.example` to `.env` and paste a free key from aistudio.google.com/apikey |
| AI images look generic / placeholder | Set `CF_ACCOUNTS` (or `CF_ACCOUNT_ID`+`CF_AI_TOKEN`) for Cloudflare FLUX |
| `WSServerHandshakeError 403` from Edge TTS | ISP/region block — set `GOOGLE_TTS_API_KEY` to use Google TTS instead |
| Vietnamese diacritics garbled in the terminal | RUN.bat/RUN.sh already set `PYTHONUTF8=1`; run through those wrappers |

---

## Author

Made by **Khue Tran** — [khuetran.com](https://khuetran.com).
Live demo: [app.khuetran.com/tella](https://app.khuetran.com/tella).

## License

MIT — see [LICENSE](LICENSE).
