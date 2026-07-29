import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import type {
  RendererStageAccessV1,
  RendererStageHistoryV1,
} from "../contracts/v1/production";

function safeError(error: unknown) {
  if (error instanceof ProductionContractError) {
    return "The backend returned a malformed renderer-stage response.";
  }
  if (error instanceof ProductionBackendUnavailableError) {
    return "The local renderer backend is unavailable.";
  }
  return "Renderer-stage review ended without a safe response.";
}

const activeStatuses = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);

export function RendererStagePage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [access, setAccess] = useState<RendererStageAccessV1 | null>();
  const [history, setHistory] = useState<RendererStageHistoryV1 | null>(null);
  const [approvalDialog, setApprovalDialog] = useState(false);
  const [startDialog, setStartDialog] = useState(false);
  const [resourceAcknowledged, setResourceAcknowledged] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!repository.getRendererStageAccess) {
      setAccess(null);
      setError("Renderer-stage capability is unavailable.");
      return;
    }
    try {
      const nextAccess = await repository.getRendererStageAccess(runId);
      setAccess(nextAccess);
      setHistory(
        repository.getRendererStageHistory
          ? await repository.getRendererStageHistory(runId)
          : null,
      );
      setError(null);
    } catch (caught) {
      setAccess(null);
      setError(safeError(caught));
    }
  }

  useEffect(() => {
    void load();
  }, [repository, runId]);

  useEffect(() => {
    const job = access?.job;
    if (!job || !activeStatuses.has(job.status)) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [access?.job?.job_id, access?.job?.status, repository, runId]);

  async function approve() {
    const authority = access?.input_authority;
    if (
      !repository.approveRendererStage ||
      !authority ||
      !resourceAcknowledged ||
      !confirmed
    ) return;
    setBusy(true);
    try {
      const result = await repository.approveRendererStage(runId, {
        schema_version: 1,
        execution_package_id: authority.execution_package_id,
        execution_package_revision_id: authority.execution_package_revision_id,
        package_source_authority_sha256:
          authority.package_source_authority_sha256,
        narration_artifact_id: authority.narration_artifact_id,
        narration_artifact_revision_id:
          authority.narration_artifact_revision_id,
        narration_audio_sha256: authority.narration_audio_sha256,
        explicit_local_resource_acknowledgement: true,
        explicit_confirmation: true,
        note: note || null,
      });
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else setMessage("Bounded renderer-stage approval created.");
      await load();
      setApprovalDialog(false);
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
      setConfirmed(false);
      setResourceAcknowledged(false);
    }
  }

  async function createPackage() {
    const approval = access?.approval;
    if (!approval || !repository.createRenderPackage) return;
    setBusy(true);
    try {
      const result = await repository.createRenderPackage(runId, {
        schema_version: 1,
        approval_id: approval.approval_id,
        approval_revision_id: approval.approval_revision_id,
        explicit_confirmation: true,
      });
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else setMessage("Immutable render package created.");
      await load();
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    const renderPackage = access?.render_package;
    if (!renderPackage || !repository.startRenderJob || !confirmed) return;
    setBusy(true);
    try {
      const result = await repository.startRenderJob(runId, {
        schema_version: 1,
        render_package_id: renderPackage.render_package_id,
        render_package_revision_id: renderPackage.render_package_revision_id,
        render_package_sha256: renderPackage.render_package_sha256,
        explicit_confirmation: true,
      });
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else setMessage("One bounded local render job started.");
      await load();
      setStartDialog(false);
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
      setConfirmed(false);
    }
  }

  async function cancel() {
    const job = access?.job;
    if (!job || !repository.cancelRenderJob) return;
    if (!window.confirm("Request cancellation for this exact render job?")) return;
    setBusy(true);
    try {
      const result = await repository.cancelRenderJob(runId, job.job_id, {
        schema_version: 1,
        job_attempt_id: job.job_attempt_id,
        explicit_confirmation: true,
        reason: note || "User requested bounded cancellation.",
      });
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else setMessage("Cancellation requested for the current render job.");
      await load();
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function review(
    operation: "accept-for-qc" | "reject" | "request-rerender",
  ) {
    const artifact = access?.artifact;
    if (!artifact || !repository.reviewRenderArtifact) return;
    if (!window.confirm(`Confirm render artifact action: ${operation}?`)) return;
    setBusy(true);
    try {
      const result = await repository.reviewRenderArtifact(
        runId,
        artifact.artifact_id,
        operation,
        {
          schema_version: 1,
          artifact_revision_id: artifact.artifact_revision_id,
          mp4_sha256: artifact.mp4_sha256,
          explicit_confirmation: true,
          reason: operation === "accept-for-qc" ? null : note || "Render review.",
        },
      );
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else {
        setMessage(
          operation === "accept-for-qc"
            ? "Accepted for separate final-media QC review."
            : "Render artifact review recorded.",
        );
      }
      await load();
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  if (access === undefined) return <h1>Renderer stage</h1>;
  if (access === null) {
    return (
      <div className="not-found">
        <h1>Renderer stage unavailable</h1>
        <p role="alert">{error}</p>
        <Link to={`/production/runs/${runId}/narration-stage`}>
          Back to narration review
        </Link>
      </div>
    );
  }

  const authority = access.input_authority;
  const configuration = access.renderer_configuration;
  const profile = access.output_profile;
  const job = access.job;
  const artifact = access.artifact;
  const videoUrl = artifact
    ? `/api/v1/plan-only/runs/${encodeURIComponent(runId)}/renderer-stage/artifacts/${encodeURIComponent(artifact.artifact_id)}/video`
    : null;

  return (
    <div className="readiness-page">
      <header className="readiness-header">
        <div>
          <p className="eyebrow">Bounded local MP4 execution</p>
          <h1>Renderer stage</h1>
          <p>The review artifact created here is not final media.</p>
        </div>
        <nav aria-label="Renderer-stage navigation">
          <Link to={`/production/runs/${runId}/narration-stage`}>
            Narration artifact
          </Link>
          <Link to={`/production/runs/${runId}`}>Run overview</Link>
        </nav>
      </header>
      <p className="warning-banner">{access.restart_warning}</p>
      <p aria-live="polite">{busy ? "Renderer operation in progress." : message}</p>
      {error && <p role="alert">{error}</p>}
      <div className="readiness-layout">
        <section className="panel">
          <h2>Sealed input authority</h2>
          {authority ? (
            <dl className="authority-grid">
              <dt>Execution package</dt>
              <dd>{authority.execution_package_revision_id}</dd>
              <dt>Package fingerprint</dt>
              <dd>{authority.package_source_authority_sha256}</dd>
              <dt>Narration artifact</dt>
              <dd>{authority.narration_artifact_revision_id}</dd>
              <dt>Audio SHA-256</dt>
              <dd>{authority.narration_audio_sha256}</dd>
              <dt>Timeline</dt>
              <dd>{authority.accepted_timeline_revision_id}</dd>
              <dt>Scenes / accepted visuals</dt>
              <dd>
                {authority.scene_ids.length} / {authority.visual_candidate_ids.length}
              </dd>
              <dt>Measured narration</dt>
              <dd>{authority.measured_narration_duration_ms} ms</dd>
            </dl>
          ) : (
            <p role="alert">{access.blocker_codes.join(", ")}</p>
          )}
        </section>
        <section className="panel">
          <h2>Local renderer configuration</h2>
          <dl className="authority-grid">
            <dt>Renderer configured</dt>
            <dd>{configuration.renderer_configured ? "Yes" : "No"}</dd>
            <dt>Renderer</dt>
            <dd>{configuration.renderer_implementation_id}</dd>
            <dt>FFmpeg / FFprobe</dt>
            <dd>
              {configuration.ffmpeg_available ? "available" : "unavailable"} /{" "}
              {configuration.ffprobe_available ? "available" : "unavailable"}
            </dd>
            <dt>Shell commands</dt><dd>Not allowed</dd>
          </dl>
          {!configuration.renderer_configured && (
            <p role="alert">RENDERER_NOT_CONFIGURED or FFMPEG_NOT_AVAILABLE</p>
          )}
          <button
            disabled={
              !access.renderer_stage_approval_capability ||
              !!access.approval?.current ||
              busy
            }
            onClick={() => setApprovalDialog(true)}
          >
            Review bounded-render approval
          </button>
          <button
            disabled={!access.render_package_creation_capability || !!access.render_package || busy}
            onClick={() => void createPackage()}
          >
            Create render package
          </button>
        </section>
        <section className="panel">
          <h2>Backend-owned output profile</h2>
          <dl className="authority-grid">
            <dt>Profile</dt><dd>{profile.profile_id} v{profile.profile_version}</dd>
            <dt>Container</dt><dd>MP4</dd>
            <dt>Resolution</dt><dd>{profile.width}×{profile.height}</dd>
            <dt>Frame rate</dt><dd>{profile.frame_rate} fps</dd>
            <dt>Video / audio</dt><dd>H.264 / AAC</dd>
            <dt>Pixel format</dt><dd>{profile.pixel_format}</dd>
          </dl>
          {access.render_package && (
            <>
              <p>Render package SHA-256:</p>
              <code>{access.render_package.render_package_sha256}</code>
              <button
                disabled={!access.bounded_render_execution_capability || busy}
                onClick={() => setStartDialog(true)}
              >
                Start one bounded render
              </button>
            </>
          )}
        </section>
        <section className="panel">
          <h2>Render job</h2>
          {job ? (
            <>
              <dl className="authority-grid">
                <dt>Status</dt><dd>{job.status}</dd>
                <dt>Progress</dt><dd>{job.progress.percent}%</dd>
                <dt>Attempt</dt><dd>{job.job_attempt_id}</dd>
                <dt>Failure</dt><dd>{job.failure_code ?? "None"}</dd>
              </dl>
              <button
                disabled={!access.render_job_cancellation_capability || busy}
                onClick={() => void cancel()}
              >
                Request cancellation
              </button>
            </>
          ) : <p>No render job has started.</p>}
        </section>
        <section className="panel">
          <h2>MP4 review artifact</h2>
          {artifact && videoUrl ? (
            <>
              <video controls preload="metadata" src={videoUrl}>
                Your browser does not support MP4 playback.
              </video>
              <dl className="authority-grid">
                <dt>Status</dt><dd>{artifact.status}</dd>
                <dt>MP4 SHA-256</dt><dd>{artifact.mp4_sha256}</dd>
                <dt>Bytes</dt><dd>{artifact.byte_length}</dd>
                <dt>Duration</dt><dd>{artifact.metadata.duration_ms} ms</dd>
                <dt>Dimensions</dt>
                <dd>{artifact.metadata.width}×{artifact.metadata.height}</dd>
                <dt>Streams</dt>
                <dd>{artifact.metadata.video_codec} / {artifact.metadata.audio_codec}</dd>
              </dl>
              <label>
                Bounded review reason
                <textarea
                  maxLength={500}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
              </label>
              <div className="action-row">
                <button
                  disabled={!access.render_artifact_review_capability || busy}
                  onClick={() => void review("accept-for-qc")}
                >
                  Submit for separate final-media QC review
                </button>
                <button disabled={busy} onClick={() => void review("reject")}>
                  Reject
                </button>
                <button
                  disabled={busy}
                  onClick={() => void review("request-rerender")}
                >
                  Request rerender
                </button>
              </div>
            </>
          ) : <p>No MP4 review artifact is available.</p>}
        </section>
        <aside className="panel">
          <h2>Authority locks</h2>
          <ul>
            <li>FULL_RENDER: not globally enabled</li>
            <li>Final media: not granted</li>
            <li>Release/publication: not granted</li>
            <li>Subtitle generation: not granted</li>
            <li>Publishing and upload: unavailable</li>
          </ul>
          <h3>Immutable process-local history</h3>
          <p>
            Approvals {history?.approvals.length ?? 0} · Packages{" "}
            {history?.render_packages.length ?? 0} · Jobs{" "}
            {history?.jobs.length ?? 0} · Artifacts{" "}
            {history?.artifacts.length ?? 0} · Reviews{" "}
            {history?.reviews.length ?? 0}
          </p>
        </aside>
      </div>
      {approvalDialog && authority ? (
        <div className="modal-backdrop">
          <section
            className="panel approval-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="renderer-approval-title"
          >
            <h2 id="renderer-approval-title">Approve bounded MP4 rendering</h2>
            <p>
              The exact planning, visual, composition, timeline and narration
              authority will be sealed.
            </p>
            <p>
              Rendering invokes the local renderer and FFmpeg and consumes CPU,
              RAM, disk and time. It makes no TTS or image-provider request.
            </p>
            <p>
              One MP4 review artifact will be created. It is not final media.
              Upstream changes make this approval stale; host restart clears
              process-local authority. Partial files are cleaned where possible.
            </p>
            <p>
              Package: {authority.execution_package_revision_id}
              <br />Audio: {authority.narration_artifact_revision_id}
              <br />Audio SHA: {authority.narration_audio_sha256}
            </p>
            <label>
              <input
                type="checkbox"
                checked={resourceAcknowledged}
                onChange={(event) =>
                  setResourceAcknowledged(event.target.checked)
                }
              />
              I acknowledge local CPU, memory, disk and time usage.
            </label>
            <label>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              I explicitly approve this exact bounded render authority.
            </label>
            <label>
              Optional bounded note
              <textarea
                maxLength={500}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
            <button
              disabled={!resourceAcknowledged || !confirmed || busy}
              onClick={() => void approve()}
            >
              Create renderer-stage approval
            </button>
            <button onClick={() => setApprovalDialog(false)}>Cancel</button>
          </section>
        </div>
      ) : null}
      {startDialog && access.render_package && authority ? (
        <div className="modal-backdrop">
          <section
            className="panel approval-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="render-start-title"
          >
            <h2 id="render-start-title">Start one local MP4 render</h2>
            <p>
              {configuration.renderer_implementation_id} · {profile.width}×
              {profile.height} · {profile.frame_rate} fps · H.264/AAC
            </p>
            <p>
              Timeline: {authority.measured_narration_duration_ms} ms · Scenes:{" "}
              {authority.scene_ids.length} · Visuals:{" "}
              {authority.visual_artifact_sha256s.length}
            </p>
            <p>Render package SHA: {access.render_package.render_package_sha256}</p>
            <p>Local rendering may use substantial CPU, RAM, disk and time.</p>
            <label>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              I explicitly confirm this one bounded render job.
            </label>
            <button disabled={!confirmed || busy} onClick={() => void start()}>
              Start render
            </button>
            <button onClick={() => setStartDialog(false)}>Cancel</button>
          </section>
        </div>
      ) : null}
    </div>
  );
}
