import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import type {
  ExecutionEnablementAccessV1,
  ExecutionPackageOperationResultV1,
  ExecutionPackageReviewReasonV1,
  ExecutionPackageV1,
} from "../contracts/v1/production";

function safeError(error: unknown): string {
  if (error instanceof ProductionContractError) {
    return "The backend returned a malformed execution-enablement response.";
  }
  if (error instanceof ProductionBackendUnavailableError) {
    return "The PLAN_ONLY execution-enablement backend is unavailable.";
  }
  return "Execution-package review ended without a safe response.";
}

const capabilityLocks = [
  "Narration generation",
  "TTS",
  "Audio generation and measurement",
  "Timeline execution",
  "Renderer and video render",
  "Subtitle generation and media muxing",
  "Output and execution-job creation",
  "Final media",
];

export function ExecutionEnablementPage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [access, setAccess] = useState<ExecutionEnablementAccessV1 | null>();
  const [history, setHistory] = useState<readonly ExecutionPackageV1[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!repository.getExecutionEnablementAccess) {
      setAccess(null);
      setError("Execution-enablement capability is unavailable.");
      return;
    }
    try {
      const next = await repository.getExecutionEnablementAccess(runId);
      setAccess(next);
      if (repository.getExecutionPackageHistory) {
        setHistory((await repository.getExecutionPackageHistory(runId))?.packages ?? []);
      }
      setError(null);
    } catch (caught) {
      setAccess(null);
      setError(safeError(caught));
    }
  }

  useEffect(() => {
    void load();
  }, [repository, runId]);

  async function act(
    operation: () => Promise<ExecutionPackageOperationResultV1>,
    success: string,
  ) {
    setBusy(true);
    try {
      const result = await operation();
      if (result.error !== null) {
        setError(`${result.error.code}: ${result.error.message}`);
      } else {
        setMessage(success);
        setError(null);
      }
      await load();
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function createPackage() {
    if (
      !repository.createExecutionPackage ||
      !access?.execution_package_creation_authorized ||
      !access.current_readiness_report_revision_id ||
      !access.current_readiness_source_authority_sha256 ||
      !access.current_readiness_approval_id ||
      !confirmed
    ) return;
    await act(
      () => repository.createExecutionPackage!(runId, {
        schema_version: 1,
        readiness_report_revision_id: access.current_readiness_report_revision_id!,
        readiness_source_authority_sha256:
          access.current_readiness_source_authority_sha256!,
        readiness_approval_id: access.current_readiness_approval_id!,
        explicit_confirmation: true,
        note: note || null,
      }),
      "Sealed execution package created. No narration, rendering or media execution has started.",
    );
    setDialogOpen(false);
    setConfirmed(false);
    setNote("");
  }

  async function mutate(
    operation: "request-revision" | "cancel-package",
    reason: ExecutionPackageReviewReasonV1,
  ) {
    const pkg = access?.package;
    if (!repository.mutateExecutionPackage || !pkg?.current) return;
    if (!window.confirm(
      operation === "cancel-package"
        ? "Cancel this sealed execution package?"
        : "Request a planning-package revision?",
    )) return;
    await act(
      () => repository.mutateExecutionPackage!(runId, operation, {
        schema_version: 1,
        package_revision_id: pkg.package_revision_id,
        package_source_authority_sha256: pkg.package_source_authority_sha256,
        explicit_confirmation: true,
        reason_code: reason,
        note: note || null,
      }),
      operation === "cancel-package"
        ? "Execution package cancelled without changing readiness authority."
        : "Planning-package revision requested; no upstream authority was mutated.",
    );
  }

  if (access === undefined) return <h1>Execution enablement</h1>;
  if (access === null) {
    return <div className="not-found">
      <h1>Execution enablement unavailable</h1>
      <p role="alert">{error}</p>
      <Link to={`/production/runs/${runId}/execution-readiness`}>Back to readiness</Link>
    </div>;
  }
  const pkg = access.package;
  return (
    <div className="readiness-page">
      <header className="readiness-header">
        <div>
          <p className="eyebrow">PLAN_ONLY · process-local</p>
          <h1>Execution enablement</h1>
          <p>No narration, rendering or media execution has started.</p>
        </div>
        <nav aria-label="Execution-enablement navigation">
          <Link to={`/production/runs/${runId}/execution-readiness`}>Readiness review</Link>
          <Link to={`/production/runs/${runId}`}>Run overview</Link>
        </nav>
      </header>
      <p aria-live="polite">{busy ? "Execution-package review in progress." : message}</p>
      {error && <p role="alert">{error}</p>}
      <div className="readiness-layout">
        <section className="panel">
          <h2>Sealed execution package</h2>
          {pkg ? (
            <dl className="authority-grid">
              <dt>Status</dt><dd>{pkg.status}</dd>
              <dt>Package revision</dt><dd>{pkg.package_revision_id}</dd>
              <dt>Package fingerprint</dt><dd>{pkg.package_source_authority_sha256}</dd>
              <dt>Eligible for separate narration-stage review</dt>
              <dd>{pkg.eligible_for_narration_stage_review ? "Yes" : "No"}</dd>
            </dl>
          ) : (
            <p>{access.blocker_codes.length
              ? access.blocker_codes.join(", ")
              : "Current readiness approval may be sealed explicitly."}</p>
          )}
          <button
            disabled={!access.execution_package_creation_authorized || busy}
            onClick={() => setDialogOpen(true)}
          >
            Review package creation
          </button>
          <div className="action-row">
            <button disabled={!pkg?.current || busy} onClick={() => void mutate("request-revision", "PLANNING_PACKAGE_REVIEW_REQUIRED")}>
              Request planning revision
            </button>
            <button disabled={!pkg?.current || busy} onClick={() => void mutate("cancel-package", "EXECUTION_SCOPE_CONCERN")}>
              Cancel sealed package
            </button>
          </div>
        </section>
        <section className="panel">
          <h2>Readiness approval authority</h2>
          <dl className="authority-grid">
            <dt>Report</dt><dd>{access.current_readiness_report_revision_id ?? "Unavailable"}</dd>
            <dt>Readiness fingerprint</dt><dd>{access.current_readiness_source_authority_sha256 ?? "Unavailable"}</dd>
            <dt>Approval</dt><dd>{access.current_readiness_approval_id ?? "Unavailable"}</dd>
          </dl>
          {pkg && <dl className="authority-grid">
            <dt>StoryPlan current / accepted</dt>
            <dd>{pkg.authority.story_plan_revision_id} / {pkg.authority.accepted_story_plan_revision_id}</dd>
            <dt>Scene collection</dt><dd>{pkg.authority.scene_collection_revision_id}</dd>
            <dt>Timeline current / accepted</dt>
            <dd>{pkg.authority.timeline_collection_revision_id} / {pkg.authority.accepted_timeline_revision_id}</dd>
          </dl>}
        </section>
        <aside className="panel">
          <h2>Operational capability locks</h2>
          <ul>{capabilityLocks.map((name) => <li key={name}>{name}: not granted</li>)}</ul>
          <p>
            Host restart resets this process-local package state. EX.2 requires
            a separate explicit implementation and approval.
          </p>
          <h3>Immutable package history</h3>
          <ol>{history.map((item) => (
            <li key={item.package_revision_id}>
              {item.package_revision_id} · {item.status} · {item.package_source_authority_sha256}
            </li>
          ))}</ol>
        </aside>
      </div>
      {dialogOpen ? (
        <div className="modal-backdrop">
          <section className="panel approval-dialog" role="dialog" aria-modal="true" aria-labelledby="package-title">
            <h2 id="package-title">Create sealed execution package</h2>
            <p>
              Package creation seals the exact current planning authority. No
              TTS is called, no audio is created, no renderer is called, and no
              output is created.
            </p>
            <p>
              Upstream changes make the package stale. Host restart resets
              process-local state. EX.2 requires separate implementation and approval.
            </p>
            <p>
              Report: {access.current_readiness_report_revision_id}<br />
              Fingerprint: {access.current_readiness_source_authority_sha256}<br />
              Approval: {access.current_readiness_approval_id}
            </p>
            <label>Optional bounded note
              <textarea maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
            </label>
            <label>
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
              I explicitly confirm this planning-authority-only seal.
            </label>
            <div className="action-row">
              <button disabled={!confirmed || busy} onClick={() => void createPackage()}>
                Create sealed execution package
              </button>
              <button onClick={() => { setDialogOpen(false); setConfirmed(false); }}>Cancel</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
