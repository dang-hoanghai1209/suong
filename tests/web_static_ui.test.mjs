import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

import {
  artifactUrls,
  buildRenderRequest,
  canBeginPoll,
  canCancel,
  createPollState,
  hasValidArtifact,
  isTerminalStatus,
  nextPollingDelay,
  normalizeProgress,
  retryRequest,
  safeJobId,
  selectRecoveryJob,
} from "../tella/web_static/ui_state.js";

const safeRequest = {
  schema_version: 1,
  input_mode: "script",
  content: "Exact narration.",
  language: "en",
  aspect_ratio: "9:16",
  media_source: "ai_image",
  theme: "cinematic",
  duration_mode: "short",
  no_music: true,
};

const job = (overrides = {}) => ({
  job_id: "web-0123456789abcdef01234567",
  status: "failed",
  phase: "failed",
  progress: 42,
  created_at: "2026-08-03T01:00:00Z",
  logs: [],
  request: safeRequest,
  ...overrides,
});

const validArtifact = {
  status: "succeeded",
  progress: 100,
  output_available: true,
  duration_seconds: 3.2,
  video_streams: 1,
  audio_streams: 1,
  preview_url: "/api/jobs/web-0123456789abcdef01234567/preview",
  download_url: "/api/jobs/web-0123456789abcdef01234567/download",
};

test("initial empty result obeys the hidden attribute despite result layout CSS", () => {
  const html = readFileSync(new URL("../tella/web_static/index.html", import.meta.url), "utf8");
  const css = readFileSync(new URL("../tella/web_static/styles.css", import.meta.url), "utf8");
  assert.match(html, /<div id="result" class="result" hidden>/);
  assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/);
  assert.equal(hasValidArtifact(null), false);
});

test("success controls require succeeded status and validated artifact metadata", () => {
  for (const status of ["queued", "running", "failed", "cancelled", "unknown"]) {
    assert.equal(hasValidArtifact(job({...validArtifact, status})), false, status);
  }
  for (const invalid of [
    {output_available: false},
    {duration_seconds: null},
    {video_streams: 0},
    {audio_streams: 0},
    {preview_url: null},
    {download_url: null},
  ]) {
    assert.equal(hasValidArtifact(job({...validArtifact, ...invalid})), false);
  }
  assert.equal(hasValidArtifact(job(validArtifact)), true);
});

test("request construction is image-only and excludes authority fields", () => {
  const payload = buildRenderRequest({
    ...safeRequest,
    media_source: "stock_video",
    provider: "forbidden",
    api_key: "forbidden",
    model: "forbidden",
    voice: "forbidden",
    upstream_url: "forbidden",
  });
  assert.deepEqual(payload, safeRequest);
  for (const key of ["provider", "api_key", "model", "voice", "upstream_url"])
    assert.equal(Object.hasOwn(payload, key), false);
});

test("Vietnamese exact-script and checked no-music serialize without default replacement", () => {
  const payload = buildRenderRequest({
    ...safeRequest,
    input_mode: "script",
    language: "vi",
    no_music: true,
  });
  assert.deepEqual(payload, {
    ...safeRequest,
    input_mode: "script",
    language: "vi",
    no_music: true,
  });
  assert.deepEqual(retryRequest(job({request: payload})), payload);
});

test("retry copies only the safe request into a new submission payload", () => {
  const source = job({request: {...safeRequest, api_key: "forbidden", provider: "edge"}});
  const payload = retryRequest(source);
  assert.deepEqual(payload, safeRequest);
  assert.notEqual(payload, source.request);
  assert.equal(retryRequest(job({status: "running"})), null);
});

test("terminal and cancellation status policies are exact", () => {
  for (const status of ["failed", "cancelled", "succeeded"])
    assert.equal(isTerminalStatus(status), true);
  for (const status of ["queued", "running", "unknown"])
    assert.equal(isTerminalStatus(status), false);
  assert.equal(canCancel(job({status: "queued"})), true);
  assert.equal(canCancel(job({status: "running"})), false);
});

test("polling delay resets on change and grows to its cap", () => {
  const current = job({status: "running", phase: "planning", progress: 20});
  assert.equal(nextPollingDelay(null, current, 4000), 1000);
  assert.equal(nextPollingDelay(current, {...current, progress: 21}, 4000), 1000);
  assert.equal(nextPollingDelay({...current, logs: ["old"]}, {...current, logs: ["new"]}, 4000), 1000);
  assert.equal(nextPollingDelay(current, {...current}, 1000), 1600);
  assert.equal(nextPollingDelay(current, {...current}, 4000), 5000);
});

test("recovery prefers remembered, newest active, then newest completed", () => {
  const oldFailed = job({job_id: "web-111111111111111111111111", created_at: "2026-01-01"});
  const active = job({
    job_id: "web-222222222222222222222222",
    status: "running",
    created_at: "2026-01-02",
  });
  const latest = job({job_id: "web-333333333333333333333333", created_at: "2026-01-03"});
  assert.equal(selectRecoveryJob([oldFailed, active, latest], oldFailed.job_id), oldFailed);
  assert.equal(selectRecoveryJob([oldFailed, active, latest], "web-aaaaaaaaaaaaaaaaaaaaaaaa"), active);
  assert.equal(selectRecoveryJob([oldFailed, latest], "missing"), latest);
  assert.equal(selectRecoveryJob([], oldFailed.job_id), null);
});

test("progress is bounded and 100 requires validated artifact metadata", () => {
  assert.equal(normalizeProgress(job({progress: -9})), 0);
  assert.equal(normalizeProgress(job({progress: 150})), 99);
  assert.equal(normalizeProgress(job({progress: "bad"})), 0);
  assert.equal(normalizeProgress(job({status: "succeeded", progress: 100})), 99);
  const complete = job({
    status: "succeeded",
    progress: 100,
    output_available: true,
    duration_seconds: 3.2,
    video_streams: 1,
    audio_streams: 1,
    preview_url: "/api/jobs/web-0123456789abcdef01234567/preview",
    download_url: "/api/jobs/web-0123456789abcdef01234567/download",
  });
  assert.equal(hasValidArtifact(complete), true);
  assert.equal(normalizeProgress(complete), 100);
  assert.equal(hasValidArtifact({...complete, audio_streams: 0}), false);
});

test("artifact routes are constructed only from portable job IDs", () => {
  assert.deepEqual(artifactUrls(job()), {
    preview: "/api/jobs/web-0123456789abcdef01234567/preview",
    download: "/api/jobs/web-0123456789abcdef01234567/download",
  });
  assert.equal(artifactUrls(job({job_id: "../../private"})), null);
  assert.equal(safeJobId(job().job_id), job().job_id);
  assert.equal(safeJobId("narration text"), null);
});

test("poll state prevents overlapping and stale-job requests", () => {
  const state = createPollState(job().job_id);
  assert.equal(canBeginPoll(state, job().job_id), true);
  state.inFlight = true;
  assert.equal(canBeginPoll(state, job().job_id), false);
  state.inFlight = false;
  assert.equal(canBeginPoll(state, "web-999999999999999999999999"), false);
});
