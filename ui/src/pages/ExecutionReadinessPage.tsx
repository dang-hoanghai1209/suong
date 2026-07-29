import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import type {
  ExecutionReadinessAccessV1,
  ExecutionReadinessOperationResultV1,
  ExecutionReadinessReportV1,
  ReadinessReviewReasonV1,
} from "../contracts/v1/production";

function safeError(error: unknown): string {
  if (error instanceof ProductionContractError) {
    return "The backend returned a malformed execution-readiness response.";
  }
  if (error instanceof ProductionBackendUnavailableError) {
    return "The PLAN_ONLY execution-readiness backend is unavailable.";
  }
  return "The readiness review ended without a safe response.";
}

export function ExecutionReadinessPage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [access, setAccess] =
    useState<ExecutionReadinessAccessV1 | null>();
  const [history, setHistory] = useState<
    readonly ExecutionReadinessReportV1[]
  >([]);
  const [selectedStage, setSelectedStage] = useState(0);
  const [selectedScene, setSelectedScene] = useState(0);
  const [selectedLimitations, setSelectedLimitations] = useState<Set<string>>(
    new Set(),
  );
  const [reviewerNote, setReviewerNote] = useState("");
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [confirmation, setConfirmation] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const report = access?.report ?? null;
  const allRequiredSelected =
    report !== null &&
    report.limitations
      .filter((item) => item.acknowledgement_required && !item.acknowledged)
      .every((item) => selectedLimitations.has(item.limitation_code));
  const allAcknowledged =
    report !== null && report.limitations.every((item) => item.acknowledged);

  async function load() {
    if (!repository.getExecutionReadinessAccess) {
      setAccess(null);
      setError("Execution-readiness capability is unavailable.");
      return;
    }
    try {
      const next = await repository.getExecutionReadinessAccess(runId);
      setAccess(next);
      setError(null);
      if (repository.getExecutionReadinessHistory) {
        const prior = await repository.getExecutionReadinessHistory(runId);
        setHistory(prior?.reports ?? []);
      }
    } catch (caught) {
      setAccess(null);
      setError(safeError(caught));
    }
  }

  useEffect(() => {
    void load();
  }, [repository, runId]);

  function useResult(
    result: ExecutionReadinessOperationResultV1,
    success: string,
  ) {
    if (result.error !== null) {
      setError(`${result.error.code}: ${result.error.message}`);
      return;
    }
    const next = result.report ?? result.access?.report ?? null;
    if (next !== null) {
      setAccess((current) =>
        current === null || current === undefined
          ? current
          : { ...current, report: next, mutation_authorized: next.current },
      );
    }
    setSelectedLimitations(new Set());
    setReviewerNote("");
    setMessage(success);
    setError(null);
  }

  async function execute(action: () => Promise<ExecutionReadinessOperationResultV1>, success: string) {
    setBusy(true);
    try {
      useResult(await action(), success);
      await load();
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function initialize() {
    if (!repository.initializeExecutionReadiness || !access?.initialization_authorized) {
      return;
    }
    const timeline = await repository.getTimelineAccess?.(runId);
    const collection = timeline?.collection;
    if (
      collection === null ||
      collection === undefined ||
      collection.accepted_timeline_revision_id === null
    ) {
      setError("Current accepted timeline authority is unavailable.");
      return;
    }
    await execute(
      () =>
        repository.initializeExecutionReadiness!(
          runId,
          collection.collection_revision_id,
          collection.accepted_timeline_revision_id!,
        ),
      "Planning-readiness report initialized. No execution authority was granted.",
    );
  }

  async function acknowledge() {
    if (!repository.acknowledgeReadinessLimitations || !report || !allRequiredSelected) {
      return;
    }
    const codes = report.limitations
      .filter((item) => selectedLimitations.has(item.limitation_code))
      .map((item) => item.limitation_code);
    await execute(
      () =>
        repository.acknowledgeReadinessLimitations!(runId, {
          schema_version: 1,
          report_revision_id: report.report_revision_id,
          source_authority_sha256: report.source_authority_sha256,
          limitation_codes: codes,
          acknowledged: true,
          reviewer_note: reviewerNote || null,
        }),
      "Required limitations acknowledged; none were resolved or removed.",
    );
  }

  async function approve() {
    if (!repository.approveExecutionReadiness || !report || !confirmation) return;
    await execute(
      () =>
        repository.approveExecutionReadiness!(runId, {
          schema_version: 1,
          report_revision_id: report.report_revision_id,
          source_authority_sha256: report.source_authority_sha256,
          purpose: "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
          explicit_confirmation: true,
          reviewer_note: reviewerNote || null,
        }),
      "Planning package approved only for a separate future execution-enablement review.",
    );
    setApprovalOpen(false);
    setConfirmation(false);
  }

  async function review(
    operation: "request-revision" | "reject",
    reason: ReadinessReviewReasonV1,
  ) {
    if (!repository.reviewExecutionReadiness || !report) return;
    if (
      !window.confirm(
        operation === "reject"
          ? "Reject this planning package review?"
          : "Request an upstream planning revision?",
      )
    ) {
      return;
    }
    await execute(
      () =>
        repository.reviewExecutionReadiness!(runId, operation, {
          schema_version: 1,
          report_revision_id: report.report_revision_id,
          source_authority_sha256: report.source_authority_sha256,
          reason_code: reason,
          note: reviewerNote || null,
        }),
      operation === "reject"
        ? "Planning package rejected without granting execution authority."
        : "Upstream planning revision requested.",
    );
  }

  async function clearApproval() {
    if (!repository.clearExecutionReadinessApproval || !report) return;
    if (!window.confirm("Clear the current separate-task review approval?")) return;
    await execute(
      () =>
        repository.clearExecutionReadinessApproval!(runId, {
          schema_version: 1,
          report_revision_id: report.report_revision_id,
          source_authority_sha256: report.source_authority_sha256,
          explicit_confirmation: true,
        }),
      "Separate-task review approval cleared.",
    );
  }

  if (access === undefined) {
    return (
      <div>
        <h1>Execution-readiness review</h1>
        <p>Loading planning-package authority…</p>
      </div>
    );
  }
  if (access === null) {
    return (
      <div className="not-found">
        <h1>Execution-readiness review unavailable</h1>
        <p role="alert">{error}</p>
        <Link to={`/production/runs/${runId}`}>Back to run</Link>
      </div>
    );
  }

  return (
    <div className="readiness-page">
      <header className="readiness-header">
        <div>
          <p className="eyebrow">PLAN_ONLY · process-local</p>
          <h1>Execution-readiness review and final planning preflight</h1>
          <p>
            Planning-package readiness only. Runtime execution environment not
            evaluated. No execution authority granted.
          </p>
        </div>
        <nav aria-label="Readiness workspace navigation">
          <Link to={`/production/runs/${runId}/timeline`}>Timeline planning</Link>
          <Link to={`/production/runs/${runId}`}>Run overview</Link>
        </nav>
      </header>
      <p aria-live="polite">{busy ? "Readiness review in progress." : message}</p>
      {error && <p role="alert">{error}</p>}

      {report === null ? (
        <section className="panel">
          <h2>Initialize planning-readiness report</h2>
          <p>
            {access.blocker_codes.length
              ? access.blocker_codes.join(", ")
              : "The accepted timeline is eligible for planning-readiness review."}
          </p>
          <button disabled={!access.initialization_authorized || busy} onClick={initialize}>
            Generate immutable planning-readiness report
          </button>
        </section>
      ) : (
        <>
          <section className="panel readiness-summary">
            <h2>Planning-package status</h2>
            <dl className="authority-grid">
              <dt>Status</dt>
              <dd>{report.status}</dd>
              <dt>Separate-task review eligibility</dt>
              <dd>
                {report.future_execution_enablement_review_eligible
                  ? "Eligible"
                  : "Not eligible"}
              </dd>
              <dt>Report revision</dt>
              <dd>{report.report_revision_id}</dd>
              <dt>Source-authority fingerprint</dt>
              <dd>{report.source_authority_sha256}</dd>
              <dt>Diagnostics</dt>
              <dd>
                {report.blocker_count} blockers · {report.warning_count} warnings ·{" "}
                {report.informational_count} informational
              </dd>
              <dt>Approval scope</dt>
              <dd>
                {report.approved_for_separate_execution_enablement_review
                  ? "Approved for separate future task review only"
                  : "Not approved"}
              </dd>
            </dl>
          </section>

          <div className="readiness-layout">
            <aside className="panel readiness-checklist" aria-label="Readiness checklists">
              <h2>Stage checklist</h2>
              <ol>
                {report.stage_checks.map((item, index) => (
                  <li key={item.check_id}>
                    <button
                      aria-current={selectedStage === index ? "true" : undefined}
                      onClick={() => setSelectedStage(index)}
                    >
                      {item.stage} · {item.status}
                    </button>
                    {selectedStage === index ? (
                      <p>
                        {item.summary}{" "}
                        {[...item.blocker_codes, ...item.warning_codes, ...item.informational_codes].join(
                          ", ",
                        )}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
              <h2>Scene checklist</h2>
              <ol>
                {report.scene_checks.map((item, index) => (
                  <li key={item.scene_check_id}>
                    <button
                      aria-current={selectedScene === index ? "true" : undefined}
                      onClick={() => setSelectedScene(index)}
                    >
                      {item.position}. {item.scene_id} · {item.readiness_status}
                    </button>
                    {selectedScene === index ? (
                      <dl className="authority-grid">
                        <dt>Scene revision</dt>
                        <dd>{item.scene_revision_id}</dd>
                        <dt>Candidate</dt>
                        <dd>{item.accepted_candidate_id}</dd>
                        <dt>Artifact</dt>
                        <dd>
                          {item.artifact_sha256} · {item.artifact_mime} ·{" "}
                          {item.artifact_width}×{item.artifact_height}
                        </dd>
                        <dt>Composition</dt>
                        <dd>{item.composition_revision_id}</dd>
                        <dt>Timeline segment</dt>
                        <dd>{item.timeline_segment_revision_id}</dd>
                        <dt>Timing</dt>
                        <dd>
                          {item.start_ms}–{item.end_ms} ms · transition{" "}
                          {item.transition_duration_ms} ms
                        </dd>
                        <dt>Narration source</dt>
                        <dd>
                          {item.source_coverage.start}–{item.source_coverage.end} ·{" "}
                          {item.narration_alignment_status}
                        </dd>
                      </dl>
                    ) : null}
                  </li>
                ))}
              </ol>
            </aside>

            <section className="panel readiness-limitations">
              <h2>Known limitations</h2>
              <p>
                Acknowledgement records awareness only; it does not resolve a
                limitation or grant capability.
              </p>
              <fieldset disabled={busy || !access.mutation_authorized}>
                <legend>Required planning-review acknowledgements</legend>
                {report.limitations.map((item) => (
                  <label key={item.limitation_code} className="limitation-row">
                    <input
                      type="checkbox"
                      checked={
                        item.acknowledged ||
                        selectedLimitations.has(item.limitation_code)
                      }
                      disabled={item.acknowledged}
                      onChange={(event) => {
                        const next = new Set(selectedLimitations);
                        if (event.target.checked) next.add(item.limitation_code);
                        else next.delete(item.limitation_code);
                        setSelectedLimitations(next);
                      }}
                    />
                    <span>
                      <strong>{item.limitation_code}</strong>
                      <br />
                      {item.description}
                      {item.acknowledgement_id
                        ? ` · ${item.acknowledgement_id}`
                        : " · acknowledgement required"}
                    </span>
                  </label>
                ))}
              </fieldset>
              <label>
                Optional bounded reviewer note
                <textarea
                  maxLength={500}
                  value={reviewerNote}
                  onChange={(event) => setReviewerNote(event.target.value)}
                />
              </label>
              <button
                disabled={
                  busy ||
                  !access.mutation_authorized ||
                  !allRequiredSelected ||
                  allAcknowledged
                }
                onClick={acknowledge}
              >
                Acknowledge selected required limitations
              </button>
            </section>

            <aside className="panel readiness-authority">
              <h2>Authority chain</h2>
              <dl className="authority-grid">
                <dt>Run</dt>
                <dd>{report.run_id}</dd>
                <dt>StoryPlan current / accepted</dt>
                <dd>
                  {report.authority.story_plan_revision_id} /{" "}
                  {report.authority.accepted_story_plan_revision_id}
                </dd>
                <dt>Narration SHA-256</dt>
                <dd>{report.authority.narration_source_sha256}</dd>
                <dt>Scene collection</dt>
                <dd>{report.authority.scene_collection_revision_id}</dd>
                <dt>Composition collection</dt>
                <dd>{report.authority.composition_collection_revision_id}</dd>
                <dt>Timeline current / accepted</dt>
                <dd>
                  {report.authority.timeline_collection_revision_id} /{" "}
                  {report.authority.accepted_timeline_revision_id}
                </dd>
              </dl>
              <h3>Capability locks</h3>
              <ul>
                <li>Narration generation: not granted</li>
                <li>TTS and audio generation: not granted</li>
                <li>Timeline execution: not granted</li>
                <li>Renderer and video render: not granted</li>
                <li>Subtitles and media muxing: not granted</li>
                <li>Final media and output creation: not granted</li>
                <li>Execution job creation: not granted</li>
              </ul>
              {report.current &&
              report.approved_for_separate_execution_enablement_review &&
              report.approval !== null ? (
                <p>
                  <Link to={`/production/runs/${runId}/execution-enablement`}>
                    Open execution enablement
                  </Link>
                </p>
              ) : null}
              <div className="action-row">
                <button
                  disabled={
                    busy ||
                    !access.mutation_authorized ||
                    !allAcknowledged ||
                    report.approval !== null
                  }
                  onClick={() => setApprovalOpen(true)}
                >
                  Review separate-task approval
                </button>
                <button
                  disabled={busy || report.approval === null}
                  onClick={clearApproval}
                >
                  Clear current review approval
                </button>
                <button
                  disabled={busy || !access.mutation_authorized}
                  onClick={() =>
                    review("request-revision", "TIMELINE_REVIEW_REQUIRED")
                  }
                >
                  Request upstream revision
                </button>
                <button
                  disabled={busy || !access.mutation_authorized}
                  onClick={() => review("reject", "OTHER_BOUNDED_NOTE")}
                >
                  Reject planning package
                </button>
              </div>
              <h3>Immutable report history</h3>
              <ol>
                {history.map((item) => (
                  <li key={item.report_revision_id}>
                    {item.report_revision_id} · {item.status} ·{" "}
                    {item.source_authority_sha256}
                  </li>
                ))}
              </ol>
            </aside>
          </div>
        </>
      )}

      {approvalOpen && report ? (
        <div className="modal-backdrop">
          <section
            className="panel approval-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="approval-title"
          >
            <h2 id="approval-title">Separate future task review approval</h2>
            <p>
              This approval applies only to a separate future
              execution-enablement implementation task. It enables no TTS,
              renderer, FFmpeg, media execution, output creation, or job.
            </p>
            <p>
              Approval becomes stale after upstream changes. Process-local state
              is lost after host restart. Runtime execution environment has not
              been evaluated.
            </p>
            <p>
              Report: {report.report_revision_id}
              <br />
              Fingerprint: {report.source_authority_sha256}
            </p>
            <label>
              <input
                type="checkbox"
                checked={confirmation}
                onChange={(event) => setConfirmation(event.target.checked)}
              />
              I explicitly confirm this planning-package-only approval scope.
            </label>
            <div className="action-row">
              <button disabled={!confirmation || busy} onClick={approve}>
                Approve planning package for separate execution-enablement review
              </button>
              <button
                onClick={() => {
                  setApprovalOpen(false);
                  setConfirmation(false);
                }}
              >
                Cancel
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
