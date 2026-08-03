"use strict";

export const ACTIVE_STATUSES = Object.freeze(["queued", "running"]);
export const TERMINAL_STATUSES = Object.freeze(["failed", "cancelled", "succeeded"]);
export const JOB_ID_PATTERN = /^web-[0-9a-f]{24}$/;

const ALLOWED_FIELDS = Object.freeze([
  "schema_version",
  "input_mode",
  "content",
  "language",
  "aspect_ratio",
  "theme",
  "duration_mode",
  "no_music",
]);

export function buildRenderRequest(values) {
  const payload = {};
  for (const field of ALLOWED_FIELDS) {
    if (Object.hasOwn(values, field)) payload[field] = values[field];
  }
  return {
    schema_version: 1,
    input_mode: payload.input_mode,
    content: payload.content,
    language: payload.language,
    aspect_ratio: payload.aspect_ratio,
    media_source: "ai_image",
    theme: payload.theme,
    duration_mode: payload.duration_mode,
    no_music: payload.no_music === true,
  };
}

export function retryRequest(job) {
  if (!job || !["failed", "cancelled"].includes(job.status) || !job.request) {
    return null;
  }
  return buildRenderRequest(job.request);
}

export function isTerminalStatus(status) {
  return TERMINAL_STATUSES.includes(status);
}

export function isActiveStatus(status) {
  return ACTIVE_STATUSES.includes(status);
}

export function pollingSignature(job) {
  const logs = Array.isArray(job?.logs) ? job.logs : [];
  return [job?.status, job?.phase, job?.progress, logs.length, logs.at(-1) ?? ""].join("|");
}

export function nextPollingDelay(previousJob, currentJob, currentDelay, base = 1000, cap = 5000) {
  if (!previousJob || pollingSignature(previousJob) !== pollingSignature(currentJob)) return base;
  return Math.min(cap, Math.max(base, Math.round(currentDelay * 1.6)));
}

export function createPollState(jobId) {
  return {jobId, inFlight: false, failures: 0, delay: 1000};
}

export function canBeginPoll(state, jobId) {
  return Boolean(state && state.jobId === jobId && !state.inFlight);
}

export function normalizeProgress(job) {
  if (job?.status === "succeeded" && hasValidArtifact(job)) return 100;
  const numeric = Number(job?.progress);
  if (!Number.isFinite(numeric)) return 0;
  return Math.min(99, Math.max(0, Math.round(numeric)));
}

export function artifactUrls(job) {
  if (!safeJobId(job?.job_id)) return null;
  const base = `/api/jobs/${job.job_id}`;
  return {preview: `${base}/preview`, download: `${base}/download`};
}

export function hasValidArtifact(job) {
  const urls = artifactUrls(job);
  return Boolean(
    job?.status === "succeeded" &&
    job.output_available === true &&
    Number(job.duration_seconds) > 0 &&
    Number(job.video_streams) >= 1 &&
    Number(job.audio_streams) >= 1 &&
    urls &&
    job.preview_url === urls.preview &&
    job.download_url === urls.download
  );
}

export function canCancel(job) {
  return job?.status === "queued";
}

export function canCompact(job) {
  return ["succeeded", "failed", "cancelled"].includes(job?.status);
}

export function safeJobId(value) {
  return JOB_ID_PATTERN.test(value ?? "") ? value : null;
}

function newest(jobs) {
  return [...jobs].sort((left, right) =>
    String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""))
  )[0] ?? null;
}

export function selectRecoveryJob(jobs, rememberedJobId) {
  if (!Array.isArray(jobs) || jobs.length === 0) return null;
  if (safeJobId(rememberedJobId)) {
    const remembered = jobs.find((job) => job.job_id === rememberedJobId);
    if (remembered) return remembered;
  }
  return newest(jobs.filter((job) => isActiveStatus(job.status))) ?? newest(jobs);
}
