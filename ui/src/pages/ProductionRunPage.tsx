import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import { ReviewDecisionPanel } from "../components/storyPlanReview/ReviewDecisionPanel";
import { StoryPlanReviewSections } from "../components/storyPlanReview/StoryPlanReviewSections";
import { StatusBadge } from "../components/status/StatusBadge";
import type {
  ReplanRequestV1,
  ProductionRunViewV1,
  StoryPlanReviewViewV1,
  StoryPlanRevisionV1,
} from "../contracts/v1/production";

function safeOperationMessage(caught: unknown): string {
  if (caught instanceof ProductionContractError) {
    return "The backend returned a malformed StoryPlan review response.";
  }
  if (caught instanceof ProductionBackendUnavailableError) {
    return "The PLAN_ONLY review backend is unavailable.";
  }
  return "The StoryPlan review operation ended without a safe response.";
}

export function ProductionRunPage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [run, setRun] = useState<ProductionRunViewV1 | null | undefined>(
    undefined,
  );
  const [review, setReview] = useState<StoryPlanReviewViewV1 | null>(null);
  const [selectedRevision, setSelectedRevision] =
    useState<StoryPlanRevisionV1 | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [reviewUnavailable, setReviewUnavailable] = useState(false);
  const [busyAction, setBusyAction] = useState<
    "accept" | "replan" | "revision" | null
  >(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const operationInFlight = useRef(false);

  useEffect(() => {
    let active = true;
    setRun(undefined);
    setReview(null);
    setSelectedRevision(null);
    setUnavailable(false);
    setReviewUnavailable(false);
    setOperationError(null);
    void repository
      .getRun(runId)
      .then(async (loadedRun) => {
        if (!active) {
          return;
        }
        setRun(loadedRun);
        if (loadedRun === null) {
          return;
        }
        try {
          const loadedReview = await repository.getStoryPlanReview(runId);
          if (active) {
            setReview(loadedReview);
            setSelectedRevision(loadedReview?.current_revision ?? null);
          }
        } catch {
          if (active) {
            setReviewUnavailable(true);
          }
        }
      })
      .catch(() => {
        if (active) {
          setUnavailable(true);
          setRun(null);
        }
      });
    return () => {
      active = false;
    };
  }, [repository, runId]);

  async function acceptStoryPlan() {
    if (
      review === null ||
      run?.status !== "PLANNED" ||
      operationInFlight.current
    ) {
      return;
    }
    operationInFlight.current = true;
    setBusyAction("accept");
    setOperationError(null);
    try {
      const result = await repository.acceptStoryPlan(run.run_id, {
        schema_version: 1,
        current_revision_id: review.current_revision_id,
      });
      if (result.error !== null) {
        setOperationError(`${result.error.code}: ${result.error.message}`);
      } else if (result.review !== null && result.revision !== null) {
        setReview(result.review);
        setSelectedRevision(result.revision);
      } else {
        setOperationError("The acceptance response was incomplete.");
      }
    } catch (caught) {
      setOperationError(safeOperationMessage(caught));
    } finally {
      operationInFlight.current = false;
      setBusyAction(null);
    }
  }

  async function requestReplan(request: ReplanRequestV1): Promise<boolean> {
    if (
      review === null ||
      run?.status !== "PLANNED" ||
      operationInFlight.current
    ) {
      return false;
    }
    operationInFlight.current = true;
    setBusyAction("replan");
    setOperationError(null);
    try {
      const result = await repository.requestStoryPlanReplan(run.run_id, request);
      if (result.error !== null) {
        setOperationError(`${result.error.code}: ${result.error.message}`);
        return false;
      }
      if (result.review === null || result.revision === null) {
        setOperationError("The replan response was incomplete.");
        return false;
      }
      setReview(result.review);
      setSelectedRevision(result.revision);
      return true;
    } catch (caught) {
      setOperationError(safeOperationMessage(caught));
      return false;
    } finally {
      operationInFlight.current = false;
      setBusyAction(null);
    }
  }

  async function selectRevision(revisionId: string) {
    if (run === null || run === undefined || operationInFlight.current) {
      return;
    }
    if (selectedRevision?.revision_id === revisionId) {
      return;
    }
    operationInFlight.current = true;
    setBusyAction("revision");
    setOperationError(null);
    try {
      const revision = await repository.getStoryPlanRevision(
        run.run_id,
        revisionId,
      );
      if (revision === null) {
        setOperationError("The requested StoryPlan revision was not found.");
      } else {
        setSelectedRevision(revision);
      }
    } catch (caught) {
      setOperationError(safeOperationMessage(caught));
    } finally {
      operationInFlight.current = false;
      setBusyAction(null);
    }
  }

  async function copyNarration() {
    const narration = selectedRevision?.story_plan.narration_text;
    if (!narration) {
      return;
    }
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard unavailable");
      }
      await navigator.clipboard.writeText(narration);
      setCopyStatus("Canonical narration copied.");
    } catch {
      setCopyStatus("Narration could not be copied.");
    }
  }

  if (run === undefined) {
    return (
      <div className="page-stack">
        <h1>Loading production run</h1>
        <p role="status" aria-live="polite">
          Loading StoryPlan review…
        </p>
      </div>
    );
  }

  if (unavailable) {
    return (
      <div className="page-stack not-found" role="alert">
        <p className="eyebrow">Unavailable</p>
        <h1>PLAN_ONLY backend unavailable</h1>
        <p>The production run could not be loaded safely.</p>
        <Link to="/production">Return to production dashboard</Link>
      </div>
    );
  }

  if (run === null) {
    return (
      <div className="page-stack not-found" role="alert">
        <p className="eyebrow">404</p>
        <h1>Production run not found</h1>
        <p>The requested production run is not registered.</p>
        <Link to="/production">Return to production dashboard</Link>
      </div>
    );
  }

  const selectedStory = selectedRevision?.story_plan;
  const blocking = run.status === "BLOCKED" || run.status === "FAILED";

  return (
    <div className="page-stack story-review-workspace">
      <header className="page-heading">
        <div>
          <p className="eyebrow">
            {run.execution_mode === "MOCK"
              ? "Synthetic UI mock"
              : "Backend PLAN_ONLY"}
          </p>
          <h1>Production run</h1>
          <code className="copyable-run-id">{run.run_id}</code>
        </div>
        <StatusBadge status={run.status} />
      </header>

      <section
        className={`surface status-callout ${blocking ? "status-callout--blocking" : ""}`}
        aria-labelledby="run-summary-title"
        {...(blocking ? { role: "alert" } : { "aria-live": "polite" as const })}
      >
        <div>
          <p className="eyebrow">Run summary</p>
          <h2 id="run-summary-title">{run.current_stage.replaceAll("_", " ")}</h2>
        </div>
        <dl className="review-detail-grid">
          <div>
            <dt>Exact run ID</dt>
            <dd>
              <code>{run.run_id}</code>
            </dd>
          </div>
          <div>
            <dt>Run status</dt>
            <dd>{run.status}</dd>
          </div>
          <div>
            <dt>Contract schema</dt>
            <dd>{run.schema_version}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{run.created_at}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{run.updated_at}</dd>
          </div>
          <div>
            <dt>Input mode</dt>
            <dd>{run.normalized_input.input_mode}</dd>
          </div>
          <div>
            <dt>Production mode</dt>
            <dd>{run.execution_mode}</dd>
          </div>
          <div>
            <dt>Requested scenes</dt>
            <dd>{selectedStory?.requested_scene_count ?? "Not available"}</dd>
          </div>
          <div>
            <dt>Planned scenes</dt>
            <dd>{selectedStory?.scenes.length ?? "Not available"}</dd>
          </div>
          <div>
            <dt>Selected revision</dt>
            <dd>{selectedRevision?.revision_id ?? "Not available"}</dd>
          </div>
          <div>
            <dt>Current revision</dt>
            <dd>{review?.current_revision_id ?? "Not available"}</dd>
          </div>
          <div>
            <dt>Review status</dt>
            <dd>{review?.review_status ?? "UNAVAILABLE"}</dd>
          </div>
        </dl>
      </section>

      {reviewUnavailable ? (
        <section className="status-callout status-callout--blocking" role="alert">
          <h2>StoryPlan review unavailable</h2>
          <p>The review projection failed closed. No review action is available.</p>
        </section>
      ) : null}

      <div className="story-review-layout">
        <div className="story-review-primary">
          <StoryPlanReviewSections
            run={run}
            revision={selectedRevision}
            copyStatus={copyStatus}
            onCopyNarration={copyNarration}
          />
        </div>
        <aside className="story-review-sidebar" aria-label="StoryPlan review controls">
          <ReviewDecisionPanel
            review={review}
            selectedRevision={selectedRevision}
            runIsPlanned={run.status === "PLANNED"}
            busyAction={busyAction}
            errorMessage={operationError}
            onAccept={acceptStoryPlan}
            onReplan={requestReplan}
            onSelectRevision={selectRevision}
            onReturnCurrent={() =>
              setSelectedRevision(review?.current_revision ?? null)
            }
          />
          <details className="surface diagnostics-disclosure">
            <summary>Diagnostics</summary>
            <dl>
              <div>
                <dt>Run contract</dt>
                <dd>v{run.schema_version}</dd>
              </div>
              <div>
                <dt>Review contract</dt>
                <dd>{review ? `v${review.schema_version}` : "Unavailable"}</dd>
              </div>
              <div>
                <dt>Render authority</dt>
                <dd>{review?.render_authority === false ? "Not granted" : "Unavailable"}</dd>
              </div>
              <div>
                <dt>Media capability</dt>
                <dd>{review?.media_capability === false ? "Not granted" : "Unavailable"}</dd>
              </div>
            </dl>
          </details>
        </aside>
      </div>

      <section className="surface" aria-labelledby="run-readiness-title">
        <p className="eyebrow">Render readiness</p>
        <h2 id="run-readiness-title">Full render locked</h2>
        <p>
          {run.render_readiness.reason_codes.map((code) => (
            <code key={code}>{code}</code>
          ))}
        </p>
      </section>

      {review?.review_status === "ACCEPTED_FOR_SCENE_PLANNING" &&
      review.accepted_revision_id === review.current_revision_id ? (
        <Link
          className="primary-link-action"
          to={`/production/runs/${encodeURIComponent(run.run_id)}/scenes`}
        >
          Open scene planning workspace
        </Link>
      ) : null}

      <Link to="/production">Return to production dashboard</Link>
    </div>
  );
}
