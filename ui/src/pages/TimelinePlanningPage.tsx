import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import type {
  TimelineAccessV1,
  TimelineCollectionV1,
  TimelineReviewReasonV1,
  TimelineSegmentV1,
} from "../contracts/v1/production";

type Editor = {
  windowStart: number;
  windowEnd: number;
  note: string;
};

function editorFrom(segment: TimelineSegmentV1): Editor {
  return {
    windowStart: segment.narration_alignment.planned_window_start_ms,
    windowEnd: segment.narration_alignment.planned_window_end_ms,
    note: segment.note ?? "",
  };
}

function safeError(error: unknown): string {
  if (error instanceof ProductionContractError) {
    return "The backend returned a malformed timeline response.";
  }
  if (error instanceof ProductionBackendUnavailableError) {
    return "The PLAN_ONLY timeline backend is unavailable.";
  }
  return "The timeline operation ended without a safe response.";
}

function milliseconds(value: number): string {
  return `${value.toLocaleString()} ms`;
}

export function TimelinePlanningPage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [access, setAccess] = useState<TimelineAccessV1 | null>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [history, setHistory] = useState<readonly TimelineSegmentV1[]>([]);
  const [playhead, setPlayhead] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const collection = access?.collection ?? null;
  const selected =
    collection?.segments.find((item) => item.segment_id === selectedId) ??
    collection?.segments[0] ??
    null;
  const authoritativeEditor = useMemo(
    () => (selected === null ? null : editorFrom(selected)),
    [selected],
  );
  const dirty =
    editor !== null &&
    authoritativeEditor !== null &&
    JSON.stringify(editor) !== JSON.stringify(authoritativeEditor);

  async function load() {
    if (!repository.getTimelineAccess) {
      setAccess(null);
      setError("Timeline-planning capability is unavailable.");
      return;
    }
    try {
      const next = await repository.getTimelineAccess(runId);
      setAccess(next);
      setEditor(
        next?.collection?.segments[0]
          ? editorFrom(next.collection.segments[0])
          : null,
      );
      setSelectedId((current) =>
        next?.collection?.segments.some((item) => item.segment_id === current)
          ? current
          : (next?.collection?.segments[0]?.segment_id ?? null),
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
    setEditor(authoritativeEditor);
    if (selected !== null && repository.getTimelineSegmentHistory) {
      repository
        .getTimelineSegmentHistory(runId, selected.segment_id)
        .then((value) => setHistory(value?.revisions ?? []))
        .catch(() => setHistory([]));
    } else {
      setHistory([]);
    }
  }, [authoritativeEditor, repository, runId, selected?.segment_id]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function useResult(
    result: Awaited<
      ReturnType<NonNullable<typeof repository.saveTimelineSegment>>
    >,
    success: string,
  ) {
    if (result.error !== null) {
      setError(`${result.error.code}: ${result.error.message}`);
      return;
    }
    const next = result.collection ?? result.access?.collection ?? null;
    if (next !== null) {
      setAccess((current) =>
        current === null || current === undefined
          ? current
          : { ...current, collection: next },
      );
    }
    setMessage(success);
    setError(null);
  }

  async function initialize() {
    if (
      !repository.initializeTimeline ||
      !access?.initialization_authorized ||
      !access.current_composition_collection_revision_id
    )
      return;
    setBusy(true);
    try {
      useResult(
        await repository.initializeTimeline(
          runId,
          access.current_composition_collection_revision_id,
        ),
        "Timeline initialized from accepted compositions. No execution authority was granted.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (
      !repository.saveTimelineSegment ||
      !collection ||
      !selected ||
      !editor ||
      !dirty
    )
      return;
    setBusy(true);
    try {
      useResult(
        await repository.saveTimelineSegment(runId, selected.segment_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          base_segment_revision_id: selected.segment_revision_id,
          changes: {
            narration_window_start_ms: editor.windowStart,
            narration_window_end_ms: editor.windowEnd,
            note: editor.note || null,
          },
        }),
        "Timeline segment revision saved.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function restore(revision: TimelineSegmentV1) {
    if (!repository.restoreTimelineSegment || !collection || !selected) return;
    if (
      !window.confirm(
        `Restore ${revision.segment_revision_id} as a new segment revision?`,
      )
    )
      return;
    setBusy(true);
    try {
      useResult(
        await repository.restoreTimelineSegment(runId, selected.segment_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          base_segment_revision_id: selected.segment_revision_id,
          restore_segment_revision_id: revision.segment_revision_id,
          reason: "Explicit timeline-history restore.",
        }),
        "Prior timing values restored as a new revision.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function review(
    operation: "accept" | "request-revision",
    reason: TimelineReviewReasonV1,
  ) {
    if (!repository.reviewTimeline || !collection || dirty) return;
    if (
      !window.confirm(
        operation === "accept"
          ? "Accept this timeline for execution review only?"
          : "Request a timeline planning revision?",
      )
    )
      return;
    setBusy(true);
    try {
      useResult(
        await repository.reviewTimeline(runId, operation, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          reason_code: reason,
          note:
            operation === "accept"
              ? "Accepted for execution review only."
              : "Timeline planning revision requested.",
        }),
        operation === "accept"
          ? "Accepted for execution review only. Execution remains disabled."
          : "Timeline planning revision requested.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  function select(segmentId: string) {
    if (dirty && !window.confirm("Discard unsaved timeline changes?")) return;
    setSelectedId(segmentId);
    setMessage(`Selected ${segmentId}.`);
  }

  if (access === undefined) {
    return (
      <div>
        <h1>Timeline planning</h1>
        <p>Loading current timeline authority…</p>
      </div>
    );
  }
  if (access === null) {
    return (
      <div className="not-found">
        <h1>Timeline planning unavailable</h1>
        <p role="alert">{error}</p>
        <Link to={`/production/runs/${runId}`}>Back to run</Link>
      </div>
    );
  }

  return (
    <div className="timeline-page">
      <header className="timeline-header">
        <div>
          <p className="eyebrow">PLAN_ONLY · process-local</p>
          <h1>Timeline planning and narration alignment</h1>
          <p>
            Text/source and planned timing alignment only. Not audio synchronized,
            rendered, frame-accurate, or final media timing.
          </p>
        </div>
        <nav aria-label="Timeline workspace navigation">
          <Link to={`/production/runs/${runId}/compositions`}>
            Composition planning
          </Link>
          <Link to={`/production/runs/${runId}/scenes`}>Scene planning</Link>
        </nav>
      </header>
      <p aria-live="polite">
        {busy ? "Timeline operation in progress." : message}
        {dirty ? " Unsaved changes." : ""}
      </p>
      {error && <p role="alert">{error}</p>}
      {collection === null ? (
        <section className="panel">
          <h2>Initialize planning timeline</h2>
          <p>
            {access.blocker_codes.length
              ? access.blocker_codes.join(", ")
              : "Current accepted compositions are ready."}
          </p>
          <button
            disabled={!access.initialization_authorized || busy}
            onClick={initialize}
          >
            Initialize timeline
          </button>
        </section>
      ) : (
        <>
          <section className="panel timeline-overview">
            <h2>Timeline planning preview</h2>
            <div
              className="timeline-track"
              aria-label="Ordered textual timeline preview"
            >
              {collection.segments.map((segment) => (
                <button
                  key={segment.segment_id}
                  className={
                    segment.segment_id === selected?.segment_id
                      ? "timeline-block timeline-block--active"
                      : "timeline-block"
                  }
                  style={{
                    width: `${Math.max(
                      8,
                      (segment.effective_visible_duration_ms /
                        collection.effective_timeline_duration_ms) *
                        100,
                    )}%`,
                  }}
                  onClick={() => select(segment.segment_id)}
                >
                  {segment.position}. {segment.scene_id}
                  <small>
                    {milliseconds(segment.start_ms)}–{milliseconds(segment.end_ms)}
                  </small>
                </button>
              ))}
            </div>
            <label>
              Local planning playhead: {milliseconds(playhead)}
              <input
                type="range"
                min={0}
                max={collection.effective_timeline_duration_ms}
                step={100}
                value={playhead}
                onChange={(event) => setPlayhead(Number(event.target.value))}
              />
            </label>
            <p>
              Planned {milliseconds(collection.total_planned_duration_ms)} ·
              transition overlap{" "}
              {milliseconds(collection.total_transition_overlap_ms)} · effective{" "}
              {milliseconds(collection.effective_timeline_duration_ms)}
            </p>
          </section>
          <div className="timeline-workspace">
            <aside className="panel timeline-nav" aria-label="Timeline segments">
              <h2>Segments</h2>
              {collection.segments.map((segment) => (
                <button
                  key={segment.segment_id}
                  aria-current={
                    segment.segment_id === selected?.segment_id
                      ? "true"
                      : undefined
                  }
                  onClick={() => select(segment.segment_id)}
                >
                  <strong>
                    {segment.position}. {segment.scene_id}
                  </strong>
                  <span>{segment.segment_id}</span>
                  <span>{segment.status}</span>
                  <small>
                    source {segment.source_coverage.start}–
                    {segment.source_coverage.end}
                  </small>
                </button>
              ))}
            </aside>
            {selected && editor && (
              <>
                <section className="panel timeline-editor">
                  <h2>Segment timing</h2>
                  <dl>
                    <dt>Start/end</dt>
                    <dd>
                      {milliseconds(selected.start_ms)}–
                      {milliseconds(selected.end_ms)}
                    </dd>
                    <dt>Duration</dt>
                    <dd>{milliseconds(selected.duration_ms)}</dd>
                    <dt>Visible after overlap</dt>
                    <dd>
                      {milliseconds(selected.effective_visible_duration_ms)}
                    </dd>
                    <dt>Transition</dt>
                    <dd>
                      {selected.transition_out_intent} ·{" "}
                      {milliseconds(selected.transition_out_ms)}
                    </dd>
                  </dl>
                  <h3>Canonical narration source</h3>
                  <blockquote>{selected.canonical_narration_segment}</blockquote>
                  <p>
                    Read-only source offsets: {selected.source_coverage.start}–
                    {selected.source_coverage.end}. Measured audio alignment:
                    unavailable. TTS alignment: not generated.
                  </p>
                  <fieldset disabled={!access.editable || busy}>
                    <legend>Planning-only narration window</legend>
                    <label>
                      Window start (milliseconds)
                      <input
                        type="number"
                        min={selected.start_ms}
                        max={selected.end_ms - 1}
                        step={1}
                        value={editor.windowStart}
                        aria-invalid={
                          editor.windowStart < selected.start_ms ||
                          editor.windowStart >= editor.windowEnd
                        }
                        onChange={(event) =>
                          setEditor({
                            ...editor,
                            windowStart: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                    <label>
                      Window end (milliseconds)
                      <input
                        type="number"
                        min={selected.start_ms + 1}
                        max={selected.end_ms}
                        step={1}
                        value={editor.windowEnd}
                        aria-invalid={
                          editor.windowEnd <= editor.windowStart ||
                          editor.windowEnd > selected.end_ms
                        }
                        onChange={(event) =>
                          setEditor({
                            ...editor,
                            windowEnd: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                    <label>
                      Timeline planning note
                      <textarea
                        maxLength={500}
                        value={editor.note}
                        onChange={(event) =>
                          setEditor({ ...editor, note: event.target.value })
                        }
                      />
                    </label>
                  </fieldset>
                  <div className="action-row">
                    {collection.accepted_for_execution_review ? (
                      <Link
                        to={`/production/runs/${runId}/execution-readiness`}
                      >
                        Execution-readiness review
                      </Link>
                    ) : null}
                    <button
                      disabled={!dirty || busy || !access.editable}
                      onClick={save}
                    >
                      Save segment revision
                    </button>
                    <button
                      disabled={!dirty || busy}
                      onClick={() => setEditor(authoritativeEditor)}
                    >
                      Reset unsaved changes
                    </button>
                  </div>
                </section>
                <aside className="panel timeline-authority">
                  <h2>Authority and validation</h2>
                  <dl>
                    <dt>Run</dt>
                    <dd>{runId}</dd>
                    <dt>StoryPlan revision</dt>
                    <dd>{collection.story_revision_id}</dd>
                    <dt>Narration SHA-256</dt>
                    <dd>{collection.narration_source_sha256}</dd>
                    <dt>Scene collection</dt>
                    <dd>{collection.scene_plan_collection_revision_id}</dd>
                    <dt>Composition collection</dt>
                    <dd>{collection.composition_collection_revision_id}</dd>
                    <dt>Timeline collection</dt>
                    <dd>{collection.collection_revision_id}</dd>
                    <dt>Segment revision</dt>
                    <dd>{selected.segment_revision_id}</dd>
                    <dt>Candidate</dt>
                    <dd>{selected.accepted_candidate_id}</dd>
                    <dt>Artifact SHA-256</dt>
                    <dd>{selected.candidate_artifact_sha256}</dd>
                    <dt>Timeline execution authority</dt>
                    <dd>Not granted</dd>
                    <dt>Narration/TTS/audio capability</dt>
                    <dd>Not granted</dd>
                    <dt>Renderer/video/final media capability</dt>
                    <dd>Not granted</dd>
                  </dl>
                  <h3>Validation</h3>
                  <p>
                    Coverage {collection.narration_coverage_valid ? "valid" : "blocked"}
                    {" · "}ordering {collection.ordering_valid ? "valid" : "blocked"}
                    {" · "}duration {collection.duration_valid ? "valid" : "blocked"}
                    {" · "}transitions{" "}
                    {collection.transition_valid ? "valid" : "blocked"}
                  </p>
                  <p>
                    Continuity:{" "}
                    {selected.continuity_codes.join(", ") || "No additional codes"}
                  </p>
                  <div className="action-row">
                    <button
                      disabled={
                        dirty ||
                        busy ||
                        !access.editable ||
                        !collection.overall_ready_for_execution_review
                      }
                      onClick={() => review("accept", "OTHER_BOUNDED_NOTE")}
                    >
                      Accept for execution review
                    </button>
                    <button
                      disabled={dirty || busy || !access.editable}
                      onClick={() =>
                        review("request-revision", "NARRATION_WINDOW_UNSUITABLE")
                      }
                    >
                      Request timeline revision
                    </button>
                  </div>
                  <h3>Segment history</h3>
                  <ol>
                    {history.map((item) => (
                      <li key={item.segment_revision_id}>
                        {item.segment_revision_id} · {item.status}{" "}
                        <button
                          disabled={
                            busy ||
                            item.segment_revision_id === selected.segment_revision_id
                          }
                          onClick={() => restore(item)}
                        >
                          Restore as new revision
                        </button>
                      </li>
                    ))}
                  </ol>
                </aside>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
