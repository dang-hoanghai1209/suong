import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import type {
  CompositionAccessV1,
  CompositionCollectionV1,
  CompositionFitModeV1,
  CompositionMotionModeV1,
  CompositionReviewReasonV1,
  CompositionTransitionV1,
  SceneCompositionV1,
} from "../contracts/v1/production";

type Editor = {
  fit: CompositionFitModeV1;
  cropX: number;
  cropY: number;
  cropWidth: number;
  cropHeight: number;
  scale: number;
  rotation: number;
  motion: CompositionMotionModeV1;
  transition: CompositionTransitionV1;
  transitionDuration: number;
  overlay: string;
  note: string;
};

function editorFrom(value: SceneCompositionV1): Editor {
  return {
    fit: value.fit_mode,
    cropX: value.crop.x,
    cropY: value.crop.y,
    cropWidth: value.crop.width,
    cropHeight: value.crop.height,
    scale: value.placement.scale,
    rotation: value.placement.rotation_degrees,
    motion: value.motion_intent.mode,
    transition: value.transition_intent,
    transitionDuration: value.transition_duration_seconds,
    overlay: value.layers.find((layer) => layer.layer_type === "SAFE_COLOR_WASH")
      ?.color ?? "",
    note: value.note ?? "",
  };
}

function safeError(error: unknown): string {
  if (error instanceof ProductionContractError) {
    return "The backend returned a malformed composition response.";
  }
  if (error instanceof ProductionBackendUnavailableError) {
    return "The PLAN_ONLY composition backend is unavailable.";
  }
  return "The composition operation ended without a safe response.";
}

export function CompositionPlanningPage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [access, setAccess] = useState<CompositionAccessV1 | null>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [history, setHistory] = useState<readonly SceneCompositionV1[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const collection = access?.collection ?? null;
  const selected =
    collection?.compositions.find((item) => item.scene_id === selectedId) ??
    collection?.compositions[0] ??
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
    if (!repository.getCompositionAccess) {
      setAccess(null);
      setError("Composition planning capability is unavailable.");
      return;
    }
    try {
      const next = await repository.getCompositionAccess(runId);
      setAccess(next);
      setSelectedId((current) =>
        next?.collection?.compositions.some((item) => item.scene_id === current)
          ? current
          : (next?.collection?.compositions[0]?.scene_id ?? null),
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
    setImageFailed(false);
    if (selected !== null && repository.getCompositionHistory) {
      repository
        .getCompositionHistory(runId, selected.scene_id)
        .then((value) => setHistory(value?.revisions ?? []))
        .catch(() => setHistory([]));
    } else {
      setHistory([]);
    }
  }, [authoritativeEditor, repository, runId, selected?.scene_id]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function useResult(
    result: Awaited<
      ReturnType<NonNullable<typeof repository.saveComposition>>
    >,
    success: string,
  ) {
    if (result.error !== null) {
      setError(`${result.error.code}: ${result.error.message}`);
      return;
    }
    const nextCollection = result.collection ?? result.access?.collection ?? null;
    setAccess((current) =>
      current === null || current === undefined || nextCollection === null
        ? current
        : { ...current, collection: nextCollection },
    );
    setMessage(success);
    setError(null);
  }

  async function initialize() {
    if (!repository.initializeCompositions || !access?.initialization_authorized) return;
    const sceneRevision = access.current_scene_plan_collection_revision_id;
    if (!sceneRevision) return;
    setBusy(true);
    try {
      useResult(
        await repository.initializeCompositions(runId, sceneRevision),
        "Composition collection initialized. No execution authority was granted.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (
      !repository.saveComposition ||
      !collection ||
      !selected ||
      !editor ||
      !dirty
    )
      return;
    setBusy(true);
    try {
      useResult(
        await repository.saveComposition(runId, selected.scene_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          base_composition_revision_id: selected.composition_revision_id,
          changes: {
            fit_mode: editor.fit,
            crop: {
              x: editor.cropX,
              y: editor.cropY,
              width: editor.cropWidth,
              height: editor.cropHeight,
            },
            placement: {
              ...selected.placement,
              scale: editor.scale,
              rotation_degrees: editor.rotation,
            },
            motion_intent: {
              ...selected.motion_intent,
              mode: editor.motion,
            },
            transition_intent: editor.transition,
            transition_duration_seconds: editor.transitionDuration,
            overlay_color: editor.overlay || null,
            note: editor.note || null,
          },
        }),
        "Composition revision saved.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function review(
    operation: "accept" | "request-revision",
    reason: CompositionReviewReasonV1,
  ) {
    if (!repository.reviewComposition || !collection || !selected || dirty) return;
    if (!window.confirm(`${operation === "accept" ? "Accept" : "Request revision for"} ${selected.scene_id}?`)) return;
    setBusy(true);
    try {
      useResult(
        await repository.reviewComposition(runId, selected.scene_id, operation, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          base_composition_revision_id: selected.composition_revision_id,
          reason_code: reason,
          note: operation === "accept" ? "Accepted for timeline planning only." : "Composition revision requested.",
        }),
        operation === "accept"
          ? "Accepted for timeline planning only."
          : "Composition revision requested.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function restore(revision: SceneCompositionV1) {
    if (!repository.restoreComposition || !collection || !selected) return;
    if (!window.confirm(`Restore values from ${revision.composition_revision_id} as a new revision?`)) return;
    setBusy(true);
    try {
      useResult(
        await repository.restoreComposition(runId, selected.scene_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          base_composition_revision_id: selected.composition_revision_id,
          restore_composition_revision_id: revision.composition_revision_id,
          reason: "Explicit composition-history restore.",
        }),
        "Prior values restored as a new revision.",
      );
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setBusy(false);
    }
  }

  function select(sceneId: string) {
    if (dirty && !window.confirm("Discard unsaved composition changes?")) return;
    setSelectedId(sceneId);
  }

  if (access === undefined) return <div><h1>Composition planning</h1><p>Loading…</p></div>;
  if (access === null) return <div><h1>Composition planning unavailable</h1><p role="alert">{error}</p><Link to={`/production/runs/${runId}`}>Back to run</Link></div>;

  return (
    <div className="composition-page">
      <header className="composition-header">
        <div>
          <p className="eyebrow">PLAN_ONLY · process-local</p>
          <h1>Composition planning</h1>
          <p>Accepted visual assembly for timeline planning. No frame or media is rendered.</p>
        </div>
        <nav aria-label="Composition workspace navigation">
          <Link to={`/production/runs/${runId}/scenes`}>Scene planning</Link>
          <Link to={`/production/runs/${runId}`}>Run overview</Link>
        </nav>
      </header>
      <div aria-live="polite">{message}{dirty ? " Unsaved changes." : ""}</div>
      {error && <p role="alert">{error}</p>}
      {collection === null ? (
        <section className="panel">
          <h2>Initialize compositions</h2>
          <p>{access.blocker_codes.length ? access.blocker_codes.join(", ") : "Current accepted visuals are ready."}</p>
          <button disabled={!access.initialization_authorized || busy} onClick={initialize}>Initialize composition collection</button>
        </section>
      ) : (
        <div className="composition-workspace">
          <aside className="panel composition-nav" aria-label="Scene compositions">
            <h2>Scenes</h2>
            {collection.compositions.map((item) => (
              <button
                key={item.scene_id}
                aria-current={item.scene_id === selected?.scene_id ? "true" : undefined}
                onClick={() => select(item.scene_id)}
              >
                <strong>{item.position}. {item.scene_id}</strong>
                <span>{item.status}</span>
                <small>{item.composition_revision_id}</small>
              </button>
            ))}
            {collection.missing_scene_ids.map((id) => <p key={id}>Missing accepted visual: {id}</p>)}
          </aside>
          {selected && editor && (
            <>
              <section className="panel composition-center">
                <h2>Planning preview</h2>
                <p className="preview-disclaimer">Browser geometry preview · not a rendered frame · not motion or color verification</p>
                <div className="composition-frame" aria-label={`9:16 planning preview for ${selected.scene_id}`}>
                  {!imageFailed ? (
                    <img
                      src={selected.artifact_url}
                      alt={`Accepted visual source ${selected.accepted_candidate_id}`}
                      onError={() => setImageFailed(true)}
                      style={{
                        objectFit: editor.fit === "CONTAIN" ? "contain" : "cover",
                        transform: `scale(${Math.min(2, Math.max(0.5, editor.scale))}) rotate(${Math.min(10, Math.max(-10, editor.rotation))}deg)`,
                        opacity: selected.placement.opacity,
                      }}
                    />
                  ) : <p role="img">Accepted visual preview unavailable.</p>}
                  {editor.overlay && <div className="composition-overlay" style={{ backgroundColor: editor.overlay }} />}
                  <div className="safe-zone title-safe" aria-hidden="true" />
                  <div className="safe-zone subtitle-safe" aria-hidden="true" />
                </div>
                <p>Motion intent: {editor.motion} · Transition: {editor.transition} ({editor.transitionDuration}s)</p>
                <fieldset disabled={!access.editable || busy}>
                  <legend>Fit and crop</legend>
                  <label>Fit mode<select value={editor.fit} onChange={(event) => setEditor({...editor, fit: event.target.value as CompositionFitModeV1})}>{["CONTAIN","COVER","FIT_WIDTH","FIT_HEIGHT","MANUAL_CROP"].map((value) => <option key={value}>{value}</option>)}</select></label>
                  {(["cropX","cropY","cropWidth","cropHeight"] as const).map((key) => <label key={key}>{key}<input type="number" min="0" max="1" step="0.01" value={editor[key]} onChange={(event) => setEditor({...editor, [key]: Number(event.target.value)})} /></label>)}
                </fieldset>
                <fieldset disabled={!access.editable || busy}>
                  <legend>Placement and intent</legend>
                  <label>Scale<input type="number" min="0.5" max="2" step="0.01" value={editor.scale} onChange={(event) => setEditor({...editor, scale: Number(event.target.value)})} /></label>
                  <label>Rotation degrees<input type="number" min="-10" max="10" step="0.5" value={editor.rotation} onChange={(event) => setEditor({...editor, rotation: Number(event.target.value)})} /></label>
                  <label>Motion<select value={editor.motion} onChange={(event) => setEditor({...editor, motion: event.target.value as CompositionMotionModeV1})}>{["STATIC","SLOW_ZOOM_IN","SLOW_ZOOM_OUT","PAN_LEFT","PAN_RIGHT","PAN_UP","PAN_DOWN","CUSTOM_START_END"].map((value) => <option key={value}>{value}</option>)}</select></label>
                  <label>Transition<select value={editor.transition} onChange={(event) => setEditor({...editor, transition: event.target.value as CompositionTransitionV1})}>{["CUT","CROSSFADE","FADE_THROUGH_COLOR","DIP_TO_BLACK","HOLD_THEN_CUT"].map((value) => <option key={value}>{value}</option>)}</select></label>
                  <label>Transition seconds<input type="number" min="0" max="2" step="0.1" value={editor.transitionDuration} onChange={(event) => setEditor({...editor, transitionDuration: Number(event.target.value)})} /></label>
                  <label>Color wash<input type="text" pattern="#[0-9A-F]{6}([0-9A-F]{2})?" placeholder="#1A2B3C" value={editor.overlay} onChange={(event) => setEditor({...editor, overlay: event.target.value})} /></label>
                  <label>Planning note<textarea maxLength={500} value={editor.note} onChange={(event) => setEditor({...editor, note: event.target.value})} /></label>
                </fieldset>
                <div className="action-row">
                  <button disabled={!dirty || busy || !access.editable} onClick={save}>Save revision</button>
                  <button disabled={!dirty || busy} onClick={() => setEditor(authoritativeEditor)}>Reset unsaved changes</button>
                </div>
              </section>
              <aside className="panel composition-authority">
                <h2>Authority and review</h2>
                <dl>
                  <dt>Run</dt><dd>{runId}</dd>
                  <dt>StoryPlan revision</dt><dd>{selected.story_revision_id}</dd>
                  <dt>Scene collection</dt><dd>{selected.scene_plan_collection_revision_id}</dd>
                  <dt>Scene revision</dt><dd>{selected.scene_revision_id}</dd>
                  <dt>Visual collection</dt><dd>{selected.visual_collection_revision_id}</dd>
                  <dt>Candidate</dt><dd>{selected.accepted_candidate_id}</dd>
                  <dt>Artifact SHA-256</dt><dd>{selected.accepted_candidate_sha256}</dd>
                  <dt>Source</dt><dd>{selected.source_width}×{selected.source_height} {selected.source_mime}</dd>
                  <dt>Composition revision</dt><dd>{selected.composition_revision_id}</dd>
                  <dt>Render authority</dt><dd>Not granted</dd>
                  <dt>Timeline execution authority</dt><dd>Not granted</dd>
                  <dt>Final media capability</dt><dd>Not granted</dd>
                </dl>
                <h3>Validation</h3>
                <p>{selected.validation.valid ? "Technically valid; human review required." : selected.validation.blocker_codes.join(", ")}</p>
                <p>Continuity: {selected.continuity_codes.join(", ") || "No additional codes"}</p>
                <div className="action-row">
                  <button disabled={dirty || busy || !selected.validation.valid || !access.editable} onClick={() => review("accept", "OTHER_BOUNDED_NOTE")}>Accept for timeline planning</button>
                  <button disabled={dirty || busy || !access.editable} onClick={() => review("request-revision", "FRAMING_UNSUITABLE")}>Request revision</button>
                </div>
                <h3>Revision history</h3>
                <ol>{history.map((item) => <li key={item.composition_revision_id}>{item.composition_revision_id} · {item.status} <button disabled={busy || item.composition_revision_id === selected.composition_revision_id} onClick={() => restore(item)}>Restore as new revision</button></li>)}</ol>
              </aside>
            </>
          )}
        </div>
      )}
    </div>
  );
}
