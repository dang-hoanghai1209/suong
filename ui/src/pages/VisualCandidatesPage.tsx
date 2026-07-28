import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import type {
  GenerateVisualCandidatesRequestV1,
  VisualCandidateAccessV1,
  VisualCandidateV1,
  VisualRejectionReasonV1,
} from "../contracts/v1/production";

const reviewReasons: readonly VisualRejectionReasonV1[] = [
  "CHARACTER_IDENTITY_MISMATCH",
  "POSE_MISMATCH",
  "ACTION_MISMATCH",
  "ENVIRONMENT_MISMATCH",
  "OBJECT_MISMATCH",
  "COMPOSITION_MISMATCH",
  "STYLE_MISMATCH",
  "CONTINUITY_MISMATCH",
  "TEXT_OR_WATERMARK_PRESENT",
  "INVALID_ANATOMY",
  "LOW_IMAGE_QUALITY",
  "DUPLICATE_CANDIDATE",
  "OTHER_BOUNDED_NOTE",
];

function safeError(error: unknown): string {
  if (error instanceof ProductionContractError) {
    return "The backend returned a malformed visual-candidate response.";
  }
  if (error instanceof ProductionBackendUnavailableError) {
    return "The PLAN_ONLY visual-candidate backend is unavailable.";
  }
  return "The visual-candidate operation ended without a safe response.";
}

function hashPrefix(value: string): string {
  return `${value.slice(0, 12)}…`;
}

export function VisualCandidatesPage() {
  const repository = useProductionRepository();
  const { runId = "", sceneId = "" } = useParams();
  const [access, setAccess] = useState<VisualCandidateAccessV1 | null>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<readonly string[]>([]);
  const [candidateCount, setCandidateCount] = useState(2);
  const [compositionEmphasis, setCompositionEmphasis] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [imageFailures, setImageFailures] = useState<ReadonlySet<string>>(new Set());
  const [reviewAction, setReviewAction] = useState<
    "reject" | "request-revision" | null
  >(null);
  const reviewTrigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let active = true;
    setAccess(undefined);
    setError(null);
    if (!repository.getVisualCandidateAccess) {
      setAccess(null);
      setError("Visual-candidate capability is unavailable.");
      return () => {
        active = false;
      };
    }
    repository
      .getVisualCandidateAccess(runId, sceneId)
      .then((value) => {
        if (!active) return;
        setAccess(value);
        setSelectedId(value?.collection?.candidates[0]?.candidate_id ?? null);
      })
      .catch((caught) => {
        if (!active) return;
        setAccess(null);
        setError(safeError(caught));
      });
    return () => {
      active = false;
    };
  }, [repository, runId, sceneId]);

  const collection = access?.collection ?? null;
  const selected =
    collection?.candidates.find((item) => item.candidate_id === selectedId) ??
    collection?.candidates[0] ??
    null;
  const compared = useMemo(
    () =>
      collection?.candidates.filter((item) => compareIds.includes(item.candidate_id)) ??
      [],
    [collection, compareIds],
  );

  function applyResult(
    next: Awaited<
      ReturnType<NonNullable<typeof repository.generateVisualCandidates>>
    >,
    success: string,
  ) {
    if (next.error !== null) {
      setError(`${next.error.code}: ${next.error.message}`);
      return false;
    }
    if (next.access === null) {
      setError("The visual-candidate response was incomplete.");
      return false;
    }
    setAccess(next.access);
    setSelectedId(
      next.access.collection?.candidates.at(-1)?.candidate_id ?? selectedId,
    );
    setMessage(success);
    setError(null);
    return true;
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      busy ||
      !access?.request_authorized ||
      collection === null ||
      !repository.generateVisualCandidates
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    const request: GenerateVisualCandidatesRequestV1 = {
      schema_version: 1,
      visual_collection_revision_id: collection.visual_collection_revision_id,
      scene_plan_collection_revision_id:
        collection.scene_plan_collection_revision_id,
      scene_revision_id: collection.scene_revision_id,
      candidate_count: candidateCount,
      aspect_ratio: "9:16",
      composition_emphasis: compositionEmphasis.trim() || null,
      correction_dimensions: [],
      note: null,
    };
    try {
      applyResult(
        await repository.generateVisualCandidates(runId, sceneId, request),
        "Visual candidate generation completed.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function acceptCandidate() {
    if (
      busy ||
      selected === null ||
      collection === null ||
      !repository.mutateVisualCandidate ||
      !window.confirm(
        `Accept ${selected.candidate_id} for composition planning only?`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      applyResult(
        await repository.mutateVisualCandidate(
          runId,
          sceneId,
          selected.candidate_id,
          "accept",
          {
            schema_version: 1,
            visual_collection_revision_id:
              collection.visual_collection_revision_id,
            scene_plan_collection_revision_id:
              collection.scene_plan_collection_revision_id,
            scene_revision_id: collection.scene_revision_id,
            candidate_revision_id: selected.candidate_revision_id,
            reason_code: "OTHER_BOUNDED_NOTE",
            note: "Accepted after explicit visual review.",
          },
        ),
        "Candidate accepted for composition planning. No render authority was granted.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function submitReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      busy ||
      selected === null ||
      collection === null ||
      reviewAction === null ||
      !repository.mutateVisualCandidate
    ) {
      return;
    }
    const data = new FormData(event.currentTarget);
    const reason = data.get("reason_code") as VisualRejectionReasonV1;
    const note = String(data.get("note") ?? "").trim();
    if (
      !window.confirm(
        `${reviewAction === "reject" ? "Reject" : "Request a revision for"} ${selected.candidate_id}?`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const succeeded = applyResult(
        await repository.mutateVisualCandidate(
          runId,
          sceneId,
          selected.candidate_id,
          reviewAction,
          {
            schema_version: 1,
            visual_collection_revision_id:
              collection.visual_collection_revision_id,
            scene_plan_collection_revision_id:
              collection.scene_plan_collection_revision_id,
            scene_revision_id: collection.scene_revision_id,
            candidate_revision_id: selected.candidate_revision_id,
            reason_code: reason,
            note: note || null,
          },
        ),
        reviewAction === "reject"
          ? "Candidate rejected."
          : "Revised candidate requested. Generation remains an explicit action.",
      );
      if (succeeded) setReviewAction(null);
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
      window.requestAnimationFrame(() => reviewTrigger.current?.focus());
    }
  }

  function toggleCompare(candidate: VisualCandidateV1) {
    setCompareIds((current) =>
      current.includes(candidate.candidate_id)
        ? current.filter((item) => item !== candidate.candidate_id)
        : current.length < 2
          ? [...current, candidate.candidate_id]
          : [current[1] as string, candidate.candidate_id],
    );
  }

  if (access === undefined) {
    return (
      <div className="page-stack">
        <h1>Visual candidates</h1>
        <p role="status">Loading current visual-candidate authority…</p>
      </div>
    );
  }
  if (access === null) {
    return (
      <div className="page-stack not-found" role="alert">
        <h1>Visual candidate workspace unavailable</h1>
        <p>{error ?? "The run or scene was not found."}</p>
        <Link to={`/production/runs/${encodeURIComponent(runId)}/scenes`}>
          Back to scene planning
        </Link>
      </div>
    );
  }

  return (
    <div className="page-stack visual-candidate-workspace">
      <header className="page-heading">
        <div>
          <p className="eyebrow">PLAN_ONLY · bounded still-image candidates</p>
          <h1>Visual candidate review</h1>
          <code>{runId}</code> · <code>{sceneId}</code>
        </div>
        <nav aria-label="Visual workspace navigation">
          <Link to={`/production/runs/${encodeURIComponent(runId)}/scenes`}>
            Back to scene planning
          </Link>
          {access.current_accepted_candidate_id ? (
            <Link to={`/production/runs/${encodeURIComponent(runId)}/compositions`}>
              Open composition planning
            </Link>
          ) : null}
        </nav>
      </header>

      <p role="status" aria-live="polite">
        {busy ? "Visual-candidate operation in progress." : message}
      </p>
      {error ? (
        <div role="alert" className="status-callout status-callout--blocking">
          {error}
        </div>
      ) : null}
      <p className="status-callout">
        Candidate authority and history are process-local. Generation may contact the
        configured external still-image provider only after Generate candidates is
        selected. No narration, TTS, renderer, or video operation is available.
      </p>

      {!access.request_authorized || collection === null ? (
        <section className="surface" aria-labelledby="visual-lock-title">
          <h2 id="visual-lock-title">Visual candidate workspace locked</h2>
          <p>
            Provider capability: {access.provider_capability.capability_label} ·{" "}
            {access.provider_capability.available ? "available" : "unavailable"}
          </p>
          {access.blocker_codes.map((code) => (
            <code key={code}>{code}</code>
          ))}
        </section>
      ) : (
        <>
          <form className="surface visual-generation-form" onSubmit={generate}>
            <h2>Generate bounded candidates</h2>
            <label>
              Candidate count
              <input
                type="number"
                min={1}
                max={4}
                step={1}
                value={candidateCount}
                aria-invalid={
                  !Number.isInteger(candidateCount) ||
                  candidateCount < 1 ||
                  candidateCount > 4
                }
                onChange={(event) => setCandidateCount(Number(event.target.value))}
                required
              />
            </label>
            <label>
              Approved aspect ratio
              <select value="9:16" disabled>
                <option value="9:16">9:16 portrait</option>
              </select>
            </label>
            <label>
              Optional composition emphasis
              <textarea
                value={compositionEmphasis}
                maxLength={300}
                aria-describedby="composition-emphasis-help"
                onChange={(event) => setCompositionEmphasis(event.target.value)}
              />
            </label>
            <p id="composition-emphasis-help">
              Bounded composition guidance only; this is not a raw prompt field.
            </p>
            <button
              type="submit"
              disabled={
                busy ||
                !Number.isInteger(candidateCount) ||
                candidateCount < 1 ||
                candidateCount > 4
              }
            >
              Generate candidates
            </button>
          </form>

          <div className="visual-review-layout">
            <section className="surface visual-gallery" aria-labelledby="gallery-title">
              <h2 id="gallery-title">Candidate gallery</h2>
              <dl className="review-detail-grid">
                <div><dt>Current StoryPlan</dt><dd>{access.current_story_revision_id ?? "Unavailable"}</dd></div>
                <div><dt>Accepted StoryPlan</dt><dd>{access.accepted_story_revision_id ?? "Not accepted"}</dd></div>
                <div><dt>Scene collection</dt><dd>{access.current_scene_plan_collection_revision_id ?? "Unavailable"}</dd></div>
                <div><dt>Scene revision</dt><dd>{access.current_scene_revision_id ?? "Unavailable"}</dd></div>
                <div><dt>Visual collection</dt><dd>{access.current_visual_collection_revision_id ?? "Unavailable"}</dd></div>
                <div><dt>Current request</dt><dd>{access.current_request_id ?? "None"}</dd></div>
                <div><dt>Accepted candidate</dt><dd>{access.current_accepted_candidate_id ?? "None"}</dd></div>
              </dl>
              <h3>Generation request history</h3>
              <ol>
                {collection.requests.map((request) => {
                  const attempt = collection.attempts.find(
                    (item) => item.request_id === request.request_id,
                  );
                  return (
                    <li key={request.request_id}>
                      <code>{request.request_id}</code> {request.status}
                      {attempt
                        ? ` / ${attempt.returned_candidate_count} valid, ${attempt.invalid_candidate_count} invalid`
                        : ""}
                      <details>
                        <summary>Backend-derived prompt projection</summary>
                        <p>{request.prompt_projection}</p>
                      </details>
                    </li>
                  );
                })}
              </ol>
              {collection.candidates.length ? (
                <div role="listbox" aria-label="Visual candidates">
                  {collection.candidates.map((candidate, index) => (
                    <button
                      key={candidate.candidate_id}
                      type="button"
                      role="option"
                      aria-selected={candidate.candidate_id === selected?.candidate_id}
                      onClick={() => {
                        setSelectedId(candidate.candidate_id);
                        setMessage(`Selected ${candidate.candidate_id}.`);
                      }}
                    >
                      {imageFailures.has(candidate.candidate_id) ? (
                        <span role="img" aria-label="Candidate preview unavailable">
                          Preview unavailable
                        </span>
                      ) : (
                        <img
                          src={candidate.artifact_url}
                          alt={`Visual candidate ${index + 1} for scene ${sceneId}`}
                          loading="lazy"
                          onError={() =>
                            setImageFailures(
                              (current) =>
                                new Set([...current, candidate.candidate_id]),
                            )
                          }
                        />
                      )}
                      <strong>{candidate.candidate_id}</strong>
                      <span>Candidate {index + 1}</span>
                      <span>{candidate.status}</span>
                      <span>
                        {candidate.technical_validation.passed
                          ? "Technically valid"
                          : "Technical blocker"}
                      </span>
                      <span>{candidate.provider_capability_label}</span>
                      <span>{candidate.width}×{candidate.height}</span>
                      <code>{hashPrefix(candidate.sha256)}</code>
                      {candidate.accepted_for_composition_planning ? (
                        <span>Accepted for composition planning</span>
                      ) : null}
                    </button>
                  ))}
                </div>
              ) : (
                <p>No visual candidates have been generated.</p>
              )}
            </section>

            <section className="surface visual-candidate-detail" aria-labelledby="detail-title">
              <h2 id="detail-title">Candidate detail</h2>
              {selected ? (
                <>
                  <img
                    src={selected.artifact_url}
                    alt={`Selected visual candidate for scene ${sceneId}: ${selected.candidate_id}`}
                    onError={() =>
                      setImageFailures(
                        (current) => new Set([...current, selected.candidate_id]),
                      )
                    }
                  />
                  <dl className="review-detail-grid">
                    <div><dt>Candidate</dt><dd><code>{selected.candidate_id}</code></dd></div>
                    <div><dt>Request</dt><dd><code>{selected.request_id}</code></dd></div>
                    <div><dt>Attempt</dt><dd><code>{selected.attempt_id}</code></dd></div>
                    <div><dt>StoryPlan revision</dt><dd>{selected.story_revision_id}</dd></div>
                    <div><dt>Scene collection</dt><dd>{selected.scene_plan_collection_revision_id}</dd></div>
                    <div><dt>Scene revision</dt><dd>{selected.scene_revision_id}</dd></div>
                    <div><dt>Semantic beat</dt><dd>{selected.semantic_beat_id}</dd></div>
                    <div><dt>Source coverage</dt><dd>{selected.source_coverage.start}–{selected.source_coverage.end}</dd></div>
                    <div><dt>Provider capability</dt><dd>{selected.provider_capability_label}</dd></div>
                    <div><dt>Model</dt><dd>{selected.model_label}</dd></div>
                    <div><dt>Dimensions</dt><dd>{selected.width}×{selected.height}</dd></div>
                    <div><dt>MIME</dt><dd>{selected.mime_type}</dd></div>
                    <div><dt>SHA-256</dt><dd><code>{selected.sha256}</code></dd></div>
                    <div><dt>Logical request</dt><dd><code>{hashPrefix(selected.logical_request_hash)}</code></dd></div>
                    <div><dt>Provider request</dt><dd><code>{hashPrefix(selected.provider_request_hash)}</code></dd></div>
                  </dl>
                  <div className="review-actions">
                    <button
                      type="button"
                      disabled={
                        busy ||
                        selected.accepted_for_composition_planning ||
                        selected.status === "REJECTED" ||
                        selected.status === "SUPERSEDED" ||
                        !selected.technical_validation.passed ||
                        selected.visual_qc.blocker_codes.length > 0
                      }
                      onClick={acceptCandidate}
                    >
                      Accept for composition planning
                    </button>
                    <button
                      ref={reviewTrigger}
                      type="button"
                      className="secondary-action"
                      disabled={busy || selected.status === "SUPERSEDED"}
                      onClick={() => setReviewAction("reject")}
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      className="secondary-action"
                      disabled={busy || selected.status === "SUPERSEDED"}
                      onClick={() => setReviewAction("request-revision")}
                    >
                      Request revised candidate
                    </button>
                  </div>
                </>
              ) : (
                <p>Select a candidate to inspect it.</p>
              )}
            </section>

            <aside className="surface visual-qc-panel" aria-labelledby="qc-title">
              <h2 id="qc-title">QC and authority</h2>
              <p>Technical blockers</p>
              {(selected?.technical_validation.blocker_codes ?? []).map((code) => (
                <code key={code}>{code}</code>
              ))}
              <p>Visual warnings</p>
              {(selected?.visual_qc.warning_codes ?? []).map((code) => (
                <code key={code}>{code}</code>
              ))}
              <p>Information</p>
              {(selected?.visual_qc.information_codes ?? []).map((code) => (
                <code key={code}>{code}</code>
              ))}
              <dl>
                <div><dt>Render authority</dt><dd>Not granted</dd></div>
                <div><dt>Video authority</dt><dd>Not granted</dd></div>
                <div><dt>Final media capability</dt><dd>Not granted</dd></div>
                <div><dt>TTS capability</dt><dd>Not granted</dd></div>
              </dl>
              <h3>Review history</h3>
              <ol>
                {selected?.review_history.map((review) => (
                  <li key={review.review_id}>
                    <code>{review.review_id}</code> {review.action} ·{" "}
                    {review.reason_code}
                  </li>
                ))}
              </ol>
            </aside>
          </div>

          <section className="surface visual-compare" aria-labelledby="compare-title">
            <h2 id="compare-title">Compare candidates</h2>
            <div className="compare-controls">
              {collection.candidates.map((candidate) => (
                <label key={candidate.candidate_id}>
                  <input
                    type="checkbox"
                    checked={compareIds.includes(candidate.candidate_id)}
                    onChange={() => toggleCompare(candidate)}
                  />
                  {candidate.candidate_id}
                </label>
              ))}
            </div>
            <div className="compare-grid">
              {compared.map((candidate) => (
                <figure key={candidate.candidate_id}>
                  <img
                    src={candidate.artifact_url}
                    alt={`Comparison preview for ${candidate.candidate_id}`}
                  />
                  <figcaption>{candidate.candidate_id}</figcaption>
                </figure>
              ))}
            </div>
          </section>
        </>
      )}

      {reviewAction !== null && selected !== null ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="visual-review-title"
          className="review-dialog-backdrop"
        >
          <form className="review-dialog" onSubmit={submitReview}>
            <h2 id="visual-review-title">
              {reviewAction === "reject"
                ? "Reject visual candidate"
                : "Request revised candidate"}
            </h2>
            <label>
              Review reason
              <select name="reason_code" defaultValue="COMPOSITION_MISMATCH" autoFocus>
                {reviewReasons.map((reason) => (
                  <option key={reason} value={reason}>
                    {reason}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Optional bounded note
              <textarea name="note" maxLength={500} />
            </label>
            <div className="review-actions">
              <button type="submit" disabled={busy}>Confirm review action</button>
              <button
                type="button"
                className="secondary-action"
                onClick={() => {
                  setReviewAction(null);
                  reviewTrigger.current?.focus();
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
