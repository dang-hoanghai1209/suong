import { useEffect, useRef, useState } from "react";

import type {
  ReplanFeedbackDimensionV1,
  ReplanRequestV1,
  StoryPlanReviewViewV1,
  StoryPlanRevisionV1,
} from "../../contracts/v1/production";

const feedbackOptions: readonly {
  readonly value: ReplanFeedbackDimensionV1;
  readonly label: string;
}[] = [
  { value: "narration_too_short", label: "Narration is too short" },
  { value: "narration_too_long", label: "Narration is too long" },
  { value: "story_focus_incorrect", label: "Story focus is incorrect" },
  { value: "emotional_progression_weak", label: "Emotional progression is weak" },
  { value: "character_scope_incorrect", label: "Character scope is incorrect" },
  { value: "scene_count_unsuitable", label: "Scene count is unsuitable" },
  { value: "custom_note", label: "Add a custom note" },
];

export function ReviewDecisionPanel({
  review,
  selectedRevision,
  runIsPlanned,
  busyAction,
  errorMessage,
  onAccept,
  onReplan,
  onSelectRevision,
  onReturnCurrent,
}: {
  readonly review: StoryPlanReviewViewV1 | null;
  readonly selectedRevision: StoryPlanRevisionV1 | null;
  readonly runIsPlanned: boolean;
  readonly busyAction: "accept" | "replan" | "revision" | null;
  readonly errorMessage: string | null;
  readonly onAccept: () => void;
  readonly onReplan: (request: ReplanRequestV1) => Promise<boolean>;
  readonly onSelectRevision: (revisionId: string) => void;
  readonly onReturnCurrent: () => void;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [feedback, setFeedback] = useState<readonly ReplanFeedbackDimensionV1[]>(
    [],
  );
  const [customNote, setCustomNote] = useState("");
  const [noteError, setNoteError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (dialogOpen) {
      dialogRef.current
        ?.querySelector<HTMLInputElement>("input[type='checkbox']")
        ?.focus();
    }
  }, [dialogOpen]);

  function closeDialog() {
    setDialogOpen(false);
    setNoteError(null);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }

  function toggleFeedback(value: ReplanFeedbackDimensionV1, checked: boolean) {
    setFeedback((current) =>
      feedbackOptions
        .map((option) => option.value)
        .filter((item) =>
          item === value ? checked : current.includes(item),
        ),
    );
    setNoteError(null);
  }

  async function submitReplan() {
    if (review === null || feedback.length === 0) {
      setNoteError("Select at least one bounded feedback reason.");
      return;
    }
    const customSelected = feedback.includes("custom_note");
    if (
      customSelected &&
      (customNote.trim().length === 0 || Array.from(customNote).length > 500)
    ) {
      setNoteError("Enter a custom note between 1 and 500 characters.");
      return;
    }
    const succeeded = await onReplan({
      schema_version: 1,
      base_revision_id: review.current_revision_id,
      feedback,
      custom_note: customSelected ? customNote : null,
    });
    if (succeeded) {
      setFeedback([]);
      setCustomNote("");
      closeDialog();
    }
  }

  const accepted =
    review?.review_status === "ACCEPTED_FOR_SCENE_PLANNING";
  const selectedIsCurrent =
    review !== null &&
    selectedRevision?.revision_id === review.current_revision_id;

  return (
    <>
      <section className="surface review-decision" aria-labelledby="review-decision-title">
        <p className="eyebrow">Bounded PLAN_ONLY authority</p>
        <h2 id="review-decision-title">Review decision</h2>
        <p role="status" aria-live="polite">
          Review state: <strong>{review?.review_status ?? "UNAVAILABLE"}</strong>
          {busyAction !== null ? `; ${busyAction} operation in progress` : ""}
        </p>
        <p>
          Acceptance unlocks only future scene planning. It does not authorize
          rendering, media generation, or FULL_RENDER.
        </p>
        <div className="review-actions">
          <button
            type="button"
            disabled={
              review === null ||
              !runIsPlanned ||
              accepted ||
              busyAction !== null
            }
            onClick={onAccept}
          >
            {busyAction === "accept"
              ? "Accepting StoryPlan…"
              : "Accept for scene planning"}
          </button>
          <button
            ref={triggerRef}
            type="button"
            className="secondary-action"
            disabled={review === null || !runIsPlanned || busyAction !== null}
            onClick={() => setDialogOpen(true)}
          >
            Request replan
          </button>
        </div>
        {!runIsPlanned ? (
          <p>Blocked or failed runs cannot be accepted or replanned.</p>
        ) : null}
        {errorMessage ? (
          <div className="status-callout status-callout--blocking" role="alert">
            <p>{errorMessage}</p>
          </div>
        ) : null}
      </section>

      <section className="surface" aria-labelledby="revision-history-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Immutable process-local snapshots</p>
            <h2 id="revision-history-title">Revision history</h2>
          </div>
          {review !== null && !selectedIsCurrent ? (
            <button type="button" className="secondary-action" onClick={onReturnCurrent}>
              Return to current revision
            </button>
          ) : null}
        </div>
        {review ? (
          <>
            <p>
              State resets when the local host stops. Current revision:{" "}
              <code>{review.current_revision_id}</code>
            </p>
            <ol className="revision-list">
              {review.revision_history.map((revision) => (
                <li key={revision.revision_id}>
                  <button
                    type="button"
                    aria-current={
                      selectedRevision?.revision_id === revision.revision_id
                        ? "true"
                        : undefined
                    }
                    disabled={busyAction !== null}
                    onClick={() => onSelectRevision(revision.revision_id)}
                  >
                    Revision {revision.revision_number}:{" "}
                    <code>{revision.revision_id}</code>
                  </button>
                  <span>{revision.created_at}</span>
                  <span>{revision.reasons.join(", ")}</span>
                </li>
              ))}
            </ol>
          </>
        ) : (
          <p>No authoritative review history is available.</p>
        )}
      </section>

      {dialogOpen ? (
        <div
          ref={dialogRef}
          className="review-dialog-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="replan-dialog-title"
        >
          <div className="review-dialog">
            <h2 id="replan-dialog-title">Request a bounded replan</h2>
            <p>
              The prior revision remains immutable. A successful request creates the
              next revision using the approved StoryPlan producer.
            </p>
            <fieldset>
              <legend>Feedback reasons</legend>
              <div className="replan-reasons">
                {feedbackOptions.map((option) => (
                  <label key={option.value}>
                    <input
                      type="checkbox"
                      checked={feedback.includes(option.value)}
                      onChange={(event) =>
                        toggleFeedback(option.value, event.currentTarget.checked)
                      }
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            {feedback.includes("custom_note") ? (
              <div className="field-stack">
                <label htmlFor="replan-custom-note">Custom note</label>
                <textarea
                  id="replan-custom-note"
                  value={customNote}
                  rows={5}
                  aria-invalid={noteError ? "true" : undefined}
                  aria-describedby="replan-note-help replan-note-error"
                  onChange={(event) => {
                    setCustomNote(event.currentTarget.value);
                    setNoteError(null);
                  }}
                />
                <p id="replan-note-help">
                  1–500 characters. This note cannot modify StoryPlan authority fields.
                </p>
              </div>
            ) : null}
            <div className="replan-review">
              <h3>Feedback to submit</h3>
              <p>
                {feedback.length > 0 ? feedback.join(", ") : "No reasons selected"}
              </p>
            </div>
            {noteError ? (
              <p id="replan-note-error" className="field-error" role="alert">
                {noteError}
              </p>
            ) : null}
            <div className="workflow-actions">
              <button type="button" className="secondary-action" onClick={closeDialog}>
                Cancel
              </button>
              <button
                type="button"
                disabled={busyAction !== null}
                onClick={submitReplan}
              >
                {busyAction === "replan" ? "Requesting replan…" : "Submit replan request"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
