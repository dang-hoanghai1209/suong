import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import type { NarrationStageAccessV1 } from "../contracts/v1/production";

function safeError(error: unknown): string {
  if (error instanceof ProductionContractError) {
    return "The backend returned a malformed narration-stage response.";
  }
  if (error instanceof ProductionBackendUnavailableError) {
    return "The narration and TTS backend is unavailable.";
  }
  return "Narration-stage review ended without a safe response.";
}

const lockedCapabilities = [
  "Timeline execution",
  "Renderer execution",
  "Video rendering",
  "Subtitle generation",
  "Media muxing",
  "Output and execution-job creation",
  "Final media",
];

export function NarrationStagePage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [access, setAccess] = useState<NarrationStageAccessV1 | null>();
  const [approvalDialog, setApprovalDialog] = useState(false);
  const [generationDialog, setGenerationDialog] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [externalAcknowledged, setExternalAcknowledged] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!repository.getNarrationStageAccess) {
      setAccess(null);
      setError("Narration-stage capability is unavailable.");
      return;
    }
    try {
      setAccess(await repository.getNarrationStageAccess(runId));
      setError(null);
    } catch (caught) {
      setAccess(null);
      setError(safeError(caught));
    }
  }

  useEffect(() => {
    void load();
  }, [repository, runId]);

  async function approve() {
    if (
      !repository.approveNarrationStage ||
      !access?.execution_package_id ||
      !access.execution_package_revision_id ||
      !access.package_source_authority_sha256 ||
      !confirmed ||
      !externalAcknowledged
    ) return;
    setBusy(true);
    try {
      const result = await repository.approveNarrationStage(runId, {
        schema_version: 1,
        execution_package_id: access.execution_package_id,
        execution_package_revision_id: access.execution_package_revision_id,
        package_source_authority_sha256: access.package_source_authority_sha256,
        explicit_external_provider_acknowledgement: true,
        explicit_confirmation: true,
        note: note || null,
      });
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else setMessage("Narration-stage approval created. No provider call occurred.");
      await load();
      setApprovalDialog(false);
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
      setConfirmed(false);
      setExternalAcknowledged(false);
    }
  }

  async function generate() {
    const approval = access?.approval;
    if (
      !repository.generateNarrationAudio ||
      !access?.execution_package_id ||
      !access.execution_package_revision_id ||
      !access.package_source_authority_sha256 ||
      !approval ||
      !confirmed
    ) return;
    setBusy(true);
    try {
      const result = await repository.generateNarrationAudio(runId, {
        schema_version: 1,
        execution_package_id: access.execution_package_id,
        execution_package_revision_id: access.execution_package_revision_id,
        package_source_authority_sha256: access.package_source_authority_sha256,
        approval_id: approval.approval_id,
        approval_revision_id: approval.approval_revision_id,
        explicit_confirmation: true,
      });
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else setMessage("Audio artifact generated for review.");
      await load();
      setGenerationDialog(false);
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
      setConfirmed(false);
    }
  }

  async function review(
    operation: "accept" | "reject" | "request-regeneration",
  ) {
    const artifact = access?.artifact;
    if (!artifact || !repository.reviewNarrationAudio) return;
    const prompt =
      operation === "accept"
        ? "Accept this audio artifact for separate renderer-stage review?"
        : operation === "reject"
          ? "Reject this audio artifact?"
          : "Request explicit narration regeneration?";
    if (!window.confirm(prompt)) return;
    setBusy(true);
    try {
      const result = await repository.reviewNarrationAudio(
        runId,
        artifact.artifact_id,
        operation,
        {
          schema_version: 1,
          artifact_revision_id: artifact.artifact_revision_id,
          audio_sha256: artifact.audio_sha256,
          explicit_confirmation: true,
          reason: operation === "accept" ? null : note || "Narration audio review.",
        },
      );
      if (result.error) setError(`${result.error.code}: ${result.error.message}`);
      else setMessage(
        operation === "accept"
          ? "Accepted for separate renderer-stage review."
          : "Narration artifact review recorded.",
      );
      await load();
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  if (access === undefined) return <h1>Narration and TTS</h1>;
  if (access === null) return (
    <div className="not-found">
      <h1>Narration and TTS unavailable</h1>
      <p role="alert">{error}</p>
      <Link to={`/production/runs/${runId}/execution-enablement`}>
        Back to execution enablement
      </Link>
    </div>
  );
  const source = access.narration_source;
  const provider = access.provider_configuration;
  const artifact = access.artifact;
  const audioUrl = artifact
    ? `/api/v1/plan-only/runs/${encodeURIComponent(runId)}/narration-stage/artifacts/${encodeURIComponent(artifact.artifact_id)}/audio`
    : null;

  return (
    <div className="readiness-page">
      <header className="readiness-header">
        <div>
          <p className="eyebrow">Audio only · process-local authority</p>
          <h1>Narration and TTS</h1>
          <p>No video rendering or media muxing has started.</p>
        </div>
        <nav aria-label="Narration-stage navigation">
          <Link to={`/production/runs/${runId}/execution-enablement`}>
            Sealed package
          </Link>
          <Link to={`/production/runs/${runId}`}>Run overview</Link>
        </nav>
      </header>
      <p className="warning-banner">{access.restart_warning}</p>
      <p aria-live="polite">{busy ? "Narration operation in progress." : message}</p>
      {error && <p role="alert">{error}</p>}
      <div className="readiness-layout">
        <section className="panel">
          <h2>Canonical narration source</h2>
          {source ? (
            <>
              <pre className="narration-preview">{source.narration_text}</pre>
              <dl className="authority-grid">
                <dt>Source SHA-256</dt><dd>{source.narration_source_sha256}</dd>
                <dt>Characters / UTF-8 bytes</dt>
                <dd>{source.character_count} / {source.utf8_byte_count}</dd>
                <dt>Editable at this stage</dt><dd>No</dd>
              </dl>
            </>
          ) : <p>{access.blocker_codes.join(", ")}</p>}
        </section>
        <section className="panel">
          <h2>Configured production TTS</h2>
          <dl className="authority-grid">
            <dt>Configured</dt><dd>{provider.provider_configured ? "Yes" : "No"}</dd>
            <dt>Provider / model</dt><dd>{provider.provider_display_name} / {provider.model_display_name}</dd>
            <dt>Voice</dt><dd>{provider.voice_display_name}</dd>
            <dt>Language</dt><dd>{provider.language}</dd>
            <dt>Output</dt><dd>WAV</dd>
            <dt>Style</dt><dd>Gentle, soft, slow, natural; no whisper</dd>
          </dl>
          {provider.provider_id === "kiraap-tts" && (
            <p>
              Usage is subject to the quota of the Google project configured in
              KiraAP.
            </p>
          )}
          {!provider.provider_configured && (
            <p role="alert">TTS_PROVIDER_NOT_CONFIGURED: generation is disabled.</p>
          )}
          <button
            disabled={!access.narration_stage_approval_capability || busy || !!access.approval?.current}
            onClick={() => setApprovalDialog(true)}
          >
            Review narration-stage approval
          </button>
          <button
            disabled={!access.narration_generation_capability || busy || !!artifact}
            onClick={() => setGenerationDialog(true)}
          >
            Generate narration audio
          </button>
        </section>
        <section className="panel">
          <h2>Audio artifact review</h2>
          {artifact && audioUrl ? (
            <>
              <p>Audio artifact generated for review.</p>
              <audio controls preload="metadata" src={audioUrl}>
                Your browser does not support audio playback.
              </audio>
              <dl className="authority-grid">
                <dt>Status</dt><dd>{artifact.status}</dd>
                <dt>Audio SHA-256</dt><dd>{artifact.audio_sha256}</dd>
                <dt>Bytes</dt><dd>{artifact.byte_length}</dd>
                <dt>Measured duration</dt><dd>{artifact.measured_duration_ms} ms</dd>
                <dt>Timeline duration</dt>
                <dd>{artifact.duration_comparison.accepted_timeline_duration_ms} ms</dd>
                <dt>Alignment</dt><dd>{artifact.duration_comparison.duration_alignment_status}</dd>
              </dl>
              <label>Bounded review reason
                <textarea maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
              </label>
              <div className="action-row">
                <button
                  disabled={
                    busy ||
                    artifact.duration_comparison.timeline_realignment_required ||
                    artifact.narration_audio_artifact_accepted
                  }
                  onClick={() => void review("accept")}
                >
                  Accept for separate renderer-stage review
                </button>
                <button disabled={busy} onClick={() => void review("reject")}>Reject</button>
                <button disabled={busy} onClick={() => void review("request-regeneration")}>
                  Request regeneration
                </button>
              </div>
              {artifact.eligible_for_renderer_stage_review && (
                <Link to={`/production/runs/${runId}/renderer-stage`}>
                  Continue to bounded renderer-stage review
                </Link>
              )}
            </>
          ) : <p>No audio artifact has been generated.</p>}
        </section>
        <aside className="panel">
          <h2>Operational locks</h2>
          <ul>{lockedCapabilities.map((name) => <li key={name}>{name}: not granted</li>)}</ul>
          <p>Provider calls require separate explicit approval and generation confirmation.</p>
        </aside>
      </div>
      {approvalDialog && source ? (
        <div className="modal-backdrop">
          <section className="panel approval-dialog" role="dialog" aria-modal="true" aria-labelledby="tts-approval-title">
            <h2 id="tts-approval-title">Approve narration audio generation</h2>
            <p>The exact sealed package and immutable narration source will be used.</p>
            <p>One configured TTS request may consume quota or incur cost. No renderer, MP4, muxing, or final output is created.</p>
            <p>Package: {access.execution_package_revision_id}<br />Fingerprint: {access.package_source_authority_sha256}<br />Narration SHA: {source.narration_source_sha256}</p>
            <label><input type="checkbox" checked={externalAcknowledged} onChange={(event) => setExternalAcknowledged(event.target.checked)} />I acknowledge external provider quota or cost.</label>
            <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I explicitly confirm narration-stage approval.</label>
            <label>Optional bounded note<textarea maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} /></label>
            <button disabled={!externalAcknowledged || !confirmed || busy} onClick={() => void approve()}>Create narration-stage approval</button>
            <button onClick={() => setApprovalDialog(false)}>Cancel</button>
          </section>
        </div>
      ) : null}
      {generationDialog && source ? (
        <div className="modal-backdrop">
          <section className="panel approval-dialog" role="dialog" aria-modal="true" aria-labelledby="tts-generation-title">
            <h2 id="tts-generation-title">Generate narration audio</h2>
            <p>{provider.provider_display_name} · {provider.model_display_name} · {provider.voice_display_name} · Vietnamese</p>
            <p>{source.character_count} characters · {source.utf8_byte_count} bytes<br />Source SHA: {source.narration_source_sha256}</p>
            <p>This creates audio only. One external request may occur. No video rendering starts.</p>
            <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I explicitly confirm this TTS request.</label>
            <button disabled={!confirmed || busy || !provider.provider_configured} onClick={() => void generate()}>Generate narration audio</button>
            <button onClick={() => setGenerationDialog(false)}>Cancel</button>
          </section>
        </div>
      ) : null}
    </div>
  );
}
