"use strict";

import {
  artifactUrls,
  buildRenderRequest,
  canBeginPoll,
  canCancel,
  canCompact,
  createPollState,
  hasValidArtifact,
  isActiveStatus,
  isTerminalStatus,
  nextPollingDelay,
  normalizeProgress,
  retryRequest,
  safeJobId,
  selectRecoveryJob,
} from "/ui_state.js";

const $ = (selector) => document.querySelector(selector);
const form = $("#render-form");
const submit = $("#submit-button");
const storageKey = "tella.selectedJobId";
const knownStatuses = new Set(["queued", "running", "failed", "cancelled", "succeeded"]);
const terminalFocus = new Set();

let readiness = null;
let jobs = [];
let selectedJob = null;
let submitting = false;
let pollState = null;
let pollTimer = null;
let pollGeneration = 0;

const options = {
  language: [["en", "English"], ["vi", "Vietnamese"], ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"], ["pt", "Portuguese"], ["it", "Italian"]],
  theme: ["cinematic", "parable", "playful", "mindfulness", "minimalist_emotional", "minimalist_symbolic_reel", "life_insight_symbolic", "practical_life_steps"],
};

for (const [id, values] of Object.entries(options)) {
  const select = $("#" + id);
  for (const value of values) {
    const [key, label] = Array.isArray(value) ? value : [value, value.replaceAll("_", " ")];
    select.add(new Option(label, key));
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `Request failed (${response.status})`);
    error.code = payload?.error?.code || "REQUEST_FAILED";
    throw error;
  }
  return payload;
}

function showError(message, {focus = true} = {}) {
  const element = $("#app-error");
  element.textContent = message;
  element.hidden = false;
  if (focus) element.focus();
}

function clearError() {
  const element = $("#app-error");
  element.textContent = "";
  element.hidden = true;
}

function updateSubmitState() {
  const active = isActiveStatus(selectedJob?.status);
  submit.disabled = submitting || !readiness?.ready || active;
  submit.textContent = submitting ? "Submitting…" : "Start production render";
  $("#submit-help").textContent = active
    ? "A selected job is active. History remains available; running renders cannot be forcibly cancelled."
    : "Submission is available when local readiness checks pass. Active renders cannot currently be forcibly cancelled.";
}

function renderHealth(data) {
  readiness = data;
  const pill = $("#health-pill");
  pill.textContent = data.ready ? "System ready" : "Setup required";
  pill.className = "health-pill " + (data.ready ? "ready" : "blocked");
  const storage = data.storage;
  $("#storage-summary").textContent = storage
    ? `${formatStorageBytes(storage.output_root_bytes)} used by web jobs · ${formatStorageBytes(storage.free_disk_bytes)} free · ${formatStorageBytes(storage.minimum_free_bytes)} minimum free.`
    : "Storage policy is unavailable.";
  const list = $("#health-checks");
  list.replaceChildren();
  for (const [name, check] of Object.entries(data.checks ?? {})) {
    const item = document.createElement("li");
    item.className = check.ready ? "ok" : "bad";
    const heading = document.createElement("strong");
    heading.textContent = `${check.ready ? "Ready" : "Unavailable"}: ${name.replaceAll("_", " ")}`;
    const detail = document.createElement("span");
    detail.textContent = check.ready ? "Local check passed." : String(check.guidance ?? "Configuration required.");
    item.append(heading, detail);
    list.append(item);
  }
  updateSubmitState();
}

function renderHealthUnavailable(message) {
  readiness = null;
  const pill = $("#health-pill");
  pill.textContent = "Readiness unavailable";
  pill.className = "health-pill blocked";
  $("#storage-summary").textContent = "Storage policy is unavailable.";
  const item = document.createElement("li");
  item.className = "bad";
  item.textContent = message;
  $("#health-checks").replaceChildren(item);
  updateSubmitState();
}

async function loadReadiness({focusOnFailure = false} = {}) {
  try {
    const data = await api("/api/health?media_source=ai_image");
    renderHealth(data);
    if (!data.ready && focusOnFailure) $("#readiness-heading").focus();
    return data.ready;
  } catch (error) {
    renderHealthUnavailable(error.message);
    if (focusOnFailure) $("#readiness-heading").focus();
    return false;
  }
}

function safeRememberedJobId() {
  try {
    return safeJobId(localStorage.getItem(storageKey));
  } catch {
    return null;
  }
}

function rememberJob(jobId) {
  const safe = safeJobId(jobId);
  if (!safe) return;
  try {
    localStorage.setItem(storageKey, safe);
  } catch {
    // Browser storage is optional; no job content is persisted here.
  }
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Unknown time" : date.toLocaleString();
}

function renderHistory(state = "ready") {
  const stateElement = $("#history-state");
  const list = $("#job-history");
  list.replaceChildren();
  if (state === "loading") {
    stateElement.textContent = "Loading history…";
    return;
  }
  if (state === "error") {
    stateElement.textContent = "History could not be loaded. Use Refresh to try again.";
    return;
  }
  if (jobs.length === 0) {
    stateElement.textContent = "No persisted jobs yet.";
    return;
  }
  stateElement.textContent = `${jobs.length} persisted ${jobs.length === 1 ? "job" : "jobs"}.`;
  for (const job of jobs) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-job" + (job.job_id === selectedJob?.job_id ? " selected" : "");
    button.setAttribute("aria-current", job.job_id === selectedJob?.job_id ? "true" : "false");
    const id = document.createElement("span");
    id.className = "history-id";
    id.textContent = job.job_id;
    const meta = document.createElement("span");
    meta.className = "history-meta";
    meta.textContent = `${job.status} · ${formatDate(job.created_at)}`;
    button.append(id, meta);
    button.addEventListener("click", () => selectJob(job));
    item.append(button);
    list.append(item);
  }
}

function replaceHistoryJob(job) {
  const index = jobs.findIndex((item) => item.job_id === job.job_id);
  if (index >= 0) jobs[index] = job;
  else jobs.unshift(job);
  jobs.sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)));
  renderHistory();
}

function stopPolling() {
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
  pollState = null;
  pollGeneration += 1;
}

function schedulePoll(delay) {
  const generation = pollGeneration;
  pollTimer = setTimeout(() => pollSelected(generation), delay);
}

function startPolling(job) {
  stopPolling();
  if (!isActiveStatus(job.status)) return;
  pollState = createPollState(job.job_id);
  schedulePoll(pollState.delay);
}

function renderArtifact(job) {
  const result = $("#result");
  const preview = $("#preview");
  const previewError = $("#preview-error");
  result.hidden = true;
  preview.pause();
  preview.removeAttribute("src");
  previewError.hidden = true;
  if (job.status !== "succeeded") return;
  if (!hasValidArtifact(job)) {
    previewError.textContent = "Success was reported without complete validated artifact metadata. Preview and download are unavailable.";
    previewError.hidden = false;
    return;
  }
  const urls = artifactUrls(job);
  preview.src = urls.preview;
  preview.load();
  $("#download").href = urls.download;
  const size = Number(job.output_bytes) > 0 ? ` · ${formatBytes(job.output_bytes)}` : "";
  $("#result-meta").textContent = `${Number(job.duration_seconds).toFixed(1)} seconds · ${job.video_streams} video · ${job.audio_streams} audio${size}`;
  result.hidden = false;
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatStorageBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "unknown";
  if (bytes < 1024) return `${Math.round(bytes)} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function renderSelectedJob(job, {focusTerminal = false} = {}) {
  selectedJob = job;
  const known = knownStatuses.has(job.status);
  const validSuccess = hasValidArtifact(job);
  const progress = normalizeProgress(job);
  $("#job-title").textContent = known ? `Production ${job.job_id}` : "Unknown production state";
  $("#job-status").textContent = known ? job.status : "unknown";
  $("#job-status").className = `status ${known ? job.status : "blocked"}`;
  $("#job-progress").value = progress;
  $("#job-progress").textContent = `${progress}%`;
  $("#progress-value").textContent = `${progress}%`;
  $("#phase").textContent = String(job.phase || "Unknown phase").replaceAll("-", " ");
  $("#logs").textContent = job.logs?.length ? job.logs.slice(-100).join("\n") : "No job logs.";
  $("#refresh-job").disabled = false;
  $("#cancel-job").hidden = !canCancel(job);
  $("#retry-job").hidden = !["failed", "cancelled"].includes(job.status);
  $("#compact-job").hidden = !canCompact(job);

  let summary = `Server state: ${job.status}. Phase: ${job.phase}. Progress: ${progress}%.`;
  if (!known) summary = "The server returned an unknown job state. Automatic polling stopped.";
  else if (job.status === "queued") summary = "Queued behind any active production. This job can still be cancelled.";
  else if (job.status === "running") summary = "Production is running. Progress changes only when the pipeline reports a real phase.";
  else if (job.status === "failed") summary = job.error ? `${job.error.code}: ${job.error.message}` : "Production failed without a public error detail.";
  else if (job.status === "cancelled") summary = job.error?.message || "The queued production was cancelled.";
  else if (!validSuccess) summary = "Artifact validation metadata is incomplete; preview and download are blocked.";
  else summary = "Production completed and the MP4 passed audio/video validation.";
  if (job.storage_compacted_at && isTerminalStatus(job.status)) {
    summary += " Non-authoritative intermediate files were compacted.";
  }
  $("#job-summary").textContent = summary;
  renderArtifact(job);
  updateSubmitState();

  if ((!known || isTerminalStatus(job.status)) && focusTerminal) {
    const announcement = `${job.job_id}:${job.status}:${validSuccess}`;
    if (!terminalFocus.has(announcement)) {
      terminalFocus.add(announcement);
      (validSuccess ? $("#result-heading") : $("#job-title")).focus();
    }
  }
}

function renderEmptyJob() {
  selectedJob = null;
  $("#job-title").textContent = "No production selected";
  $("#job-status").textContent = "Empty";
  $("#job-status").className = "status neutral";
  $("#job-summary").textContent = "Create a video or select one from history.";
  $("#job-progress").value = 0;
  $("#progress-value").textContent = "0%";
  $("#phase").textContent = "Not started";
  $("#logs").textContent = "No job logs.";
  $("#refresh-job").disabled = true;
  $("#cancel-job").hidden = true;
  $("#retry-job").hidden = true;
  $("#compact-job").hidden = true;
  $("#result").hidden = true;
  updateSubmitState();
}

function selectJob(job, {recovering = false} = {}) {
  stopPolling();
  rememberJob(job.job_id);
  renderSelectedJob(job);
  renderHistory();
  if (isActiveStatus(job.status)) startPolling(job);
  if (!recovering) $("#job-title").focus();
}

async function pollSelected(generation) {
  const state = pollState;
  const jobId = selectedJob?.job_id;
  if (generation !== pollGeneration || !canBeginPoll(state, jobId)) return;
  state.inFlight = true;
  const previous = selectedJob;
  try {
    const current = await api(`/api/jobs/${jobId}`);
    if (generation !== pollGeneration || selectedJob?.job_id !== jobId) return;
    state.failures = 0;
    state.delay = nextPollingDelay(previous, current, state.delay);
    replaceHistoryJob(current);
    renderSelectedJob(current, {focusTerminal: true});
    if (!knownStatuses.has(current.status) || isTerminalStatus(current.status)) {
      stopPolling();
      return;
    }
  } catch (error) {
    if (generation !== pollGeneration) return;
    state.failures += 1;
    state.delay = Math.min(5000, Math.max(1000, Math.round(state.delay * 1.6)));
    if (state.failures >= 3) {
      showError("Automatic updates paused after repeated connection failures. Use Refresh selected job to continue.");
      stopPolling();
      return;
    }
  } finally {
    if (pollState === state) state.inFlight = false;
  }
  if (pollState === state) schedulePoll(state.delay);
}

async function loadHistory({bootstrap = false} = {}) {
  renderHistory("loading");
  try {
    const payload = await api("/api/jobs");
    if (!Array.isArray(payload?.jobs)) throw new Error("History response was invalid.");
    jobs = payload.jobs;
    const currentId = selectedJob?.job_id;
    const recovered = selectRecoveryJob(jobs, currentId || safeRememberedJobId());
    if (recovered) selectJob(recovered, {recovering: bootstrap});
    else {
      stopPolling();
      renderEmptyJob();
      renderHistory();
    }
    return true;
  } catch (error) {
    renderHistory("error");
    if (!bootstrap) showError(error.message);
    return false;
  }
}

function currentFormPayload() {
  if (!form.checkValidity()) {
    const invalid = form.querySelector(":invalid");
    invalid?.focus();
    form.reportValidity();
    return null;
  }
  const data = Object.fromEntries(new FormData(form));
  if (String(data.content).trim() !== data.content) {
    showError("Content must not begin or end with whitespace.");
    $("#content").focus();
    return null;
  }
  return buildRenderRequest({
    ...data,
    schema_version: 1,
    no_music: $("#no_music").checked,
  });
}

async function submitPayload(payload) {
  if (submitting || isActiveStatus(selectedJob?.status)) return;
  clearError();
  submitting = true;
  updateSubmitState();
  try {
    if (!(await loadReadiness({focusOnFailure: true}))) {
      showError("Production readiness is unavailable. Resolve the listed checks and retry.");
      return;
    }
    const created = await api("/api/jobs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    replaceHistoryJob(created);
    selectJob(created);
    $("#job-panel").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    showError(error.message);
  } finally {
    submitting = false;
    updateSubmitState();
  }
}

async function refreshSelectedJob() {
  if (!selectedJob || pollState?.inFlight) return;
  clearError();
  try {
    const current = await api(`/api/jobs/${selectedJob.job_id}`);
    replaceHistoryJob(current);
    renderSelectedJob(current, {focusTerminal: true});
    if (isActiveStatus(current.status)) startPolling(current);
    else stopPolling();
  } catch (error) {
    showError(error.message);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const payload = currentFormPayload();
  if (payload) submitPayload(payload);
});

$("#retry-job").addEventListener("click", async () => {
  if (!retryRequest(selectedJob) || submitting) {
    showError("Only failed or cancelled jobs with safe request metadata can be retried.");
    return;
  }
  clearError();
  submitting = true;
  updateSubmitState();
  try {
    const created = await api(`/api/jobs/${selectedJob.job_id}/retry`, {method: "POST"});
    replaceHistoryJob(created);
    selectJob(created);
    $("#job-panel").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    showError(`Retry was not created: ${error.message}`);
  } finally {
    submitting = false;
    updateSubmitState();
  }
});

$("#cancel-job").addEventListener("click", async () => {
  if (!canCancel(selectedJob)) return;
  clearError();
  try {
    const cancelled = await api(`/api/jobs/${selectedJob.job_id}/cancel`, {method: "POST"});
    replaceHistoryJob(cancelled);
    stopPolling();
    renderSelectedJob(cancelled, {focusTerminal: true});
  } catch (error) {
    let detail = error.message;
    try {
      const current = await api(`/api/jobs/${selectedJob.job_id}`);
      replaceHistoryJob(current);
      renderSelectedJob(current);
      if (isActiveStatus(current.status)) startPolling(current);
    } catch (refreshError) {
      detail += ` Latest state could not be loaded: ${refreshError.message}`;
    }
    showError(`Cancellation was not applied: ${detail}`);
  }
});

$("#compact-job").addEventListener("click", async () => {
  if (!canCompact(selectedJob)) return;
  const confirmed = window.confirm(
    "Remove reconstructable intermediate files for this job? The persisted job record and any validated MP4 will be retained."
  );
  if (!confirmed) return;
  clearError();
  const jobId = selectedJob.job_id;
  try {
    await api(`/api/jobs/${jobId}/compact`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: true}),
    });
    const current = await api(`/api/jobs/${jobId}`);
    replaceHistoryJob(current);
    renderSelectedJob(current);
    await loadReadiness();
  } catch (error) {
    showError(`Storage compaction was not applied: ${error.message}`);
  }
});

$("#refresh-readiness").addEventListener("click", () => loadReadiness({focusOnFailure: true}));
$("#refresh-history").addEventListener("click", () => loadHistory());
$("#refresh-job").addEventListener("click", refreshSelectedJob);
$("#preview").addEventListener("error", () => {
  if ($("#result").hidden) return;
  $("#preview-error").textContent = "Preview could not be loaded. The validated download may still be available.";
  $("#preview-error").hidden = false;
});

$("#content").addEventListener("input", (event) => {
  const length = event.target.value.length;
  $("#char-count").textContent = String(length);
  $("#char-remaining").textContent = (10000 - length).toLocaleString();
});

document.querySelectorAll("[name=input_mode]").forEach((radio) => radio.addEventListener("change", () => {
  const script = document.querySelector("[name=input_mode]:checked").value === "script";
  $("#content-label").textContent = script ? "Exact narration script" : "Topic";
  $("#content-help").textContent = script
    ? "The supplied narration is preserved exactly for production planning."
    : "Describe the subject; Tella will plan the narration and scenes.";
  $("#content").placeholder = script
    ? "Paste the exact narration text to produce"
    : "A quiet lesson about choosing courage over certainty";
}));

window.addEventListener("beforeunload", stopPolling);

async function bootstrap() {
  renderEmptyJob();
  await Promise.all([loadReadiness(), loadHistory({bootstrap: true})]);
}

bootstrap();
