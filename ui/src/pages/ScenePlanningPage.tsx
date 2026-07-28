import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link, useParams } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import { SceneAuthorityPanel } from "../components/scenePlanning/SceneAuthorityPanel";
import {
  draftFromScene,
  SceneEditor,
  type SceneDraft,
} from "../components/scenePlanning/SceneEditor";
import { SceneNavigation } from "../components/scenePlanning/SceneNavigation";
import type {
  ScenePlanAccessV1,
  ScenePlanOperationResultV1,
  ScenePlanSceneHistoryV1,
  StoryPlanReviewViewV1,
} from "../contracts/v1/production";

function safeError(caught: unknown): string {
  if (caught instanceof ProductionContractError) {
    return "The backend returned a malformed scene-planning response.";
  }
  if (caught instanceof ProductionBackendUnavailableError) {
    return "The PLAN_ONLY scene-planning backend is unavailable.";
  }
  return "The scene-planning operation ended without a safe response.";
}

export function ScenePlanningPage() {
  const repository = useProductionRepository();
  const { runId = "" } = useParams();
  const [review, setReview] = useState<StoryPlanReviewViewV1 | null>(null);
  const [access, setAccess] = useState<ScenePlanAccessV1 | null | undefined>(
    undefined,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SceneDraft | null>(null);
  const [history, setHistory] = useState<ScenePlanSceneHistoryV1 | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [splitOpen, setSplitOpen] = useState(false);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [mobileDetail, setMobileDetail] = useState(false);
  const splitTrigger = useRef<HTMLButtonElement>(null);
  const revisionTrigger = useRef<HTMLButtonElement>(null);

  const collection = access?.collection ?? null;
  const scene =
    collection?.scenes.find((item) => item.scene_id === selectedId) ??
    collection?.scenes[0] ??
    null;
  const authoritativeDraft = useMemo(
    () => (scene ? draftFromScene(scene) : null),
    [scene],
  );
  const dirty =
    draft !== null &&
    authoritativeDraft !== null &&
    JSON.stringify(draft) !== JSON.stringify(authoritativeDraft);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  useEffect(() => {
    let active = true;
    setAccess(undefined);
    setError(null);
    void Promise.all([
      repository.getRun(runId),
      repository.getStoryPlanReview(runId),
      repository.getScenePlan?.(runId) ?? Promise.resolve(null),
    ])
      .then(async ([run, loadedReview, loadedAccess]) => {
        if (!active) return;
        if (run === null || loadedReview === null) {
          setReview(loadedReview);
          setAccess(null);
          return;
        }
        setReview(loadedReview);
        let currentAccess = loadedAccess;
        if (
          loadedReview.review_status === "ACCEPTED_FOR_SCENE_PLANNING" &&
          loadedReview.accepted_revision_id === loadedReview.current_revision_id &&
          (currentAccess?.collection === null ||
            currentAccess?.collection?.source_story_revision_id !==
              loadedReview.current_revision_id) &&
          repository.initializeScenePlan
        ) {
          const initialized = await repository.initializeScenePlan(
            runId,
            loadedReview.current_revision_id,
          );
          if (initialized.error !== null) {
            setError(`${initialized.error.code}: ${initialized.error.message}`);
          }
          currentAccess = initialized.access;
        }
        if (active) {
          setAccess(currentAccess);
          const first = currentAccess?.collection?.scenes[0] ?? null;
          setSelectedId(first?.scene_id ?? null);
          setDraft(first ? draftFromScene(first) : null);
        }
      })
      .catch((caught) => {
        if (active) {
          setAccess(null);
          setError(safeError(caught));
        }
      });
    return () => {
      active = false;
    };
  }, [repository, runId]);

  useEffect(() => {
    if (!scene || !repository.getSceneHistory) {
      setHistory(null);
      return;
    }
    void repository
      .getSceneHistory(runId, scene.scene_id)
      .then(setHistory)
      .catch(() => setHistory(null));
  }, [repository, runId, scene?.scene_id, scene?.scene_revision_id]);

  function applyResult(result: ScenePlanOperationResultV1, success: string) {
    if (result.error !== null) {
      setError(`${result.error.code}: ${result.error.message}`);
      return false;
    }
    if (result.access === null) {
      setError("The scene-planning response was incomplete.");
      return false;
    }
    setAccess(result.access);
    const selected =
      result.access.collection?.scenes.find((item) => item.scene_id === selectedId) ??
      result.access.collection?.scenes[0] ??
      null;
    setSelectedId(selected?.scene_id ?? null);
    setDraft(selected ? draftFromScene(selected) : null);
    setMessage(success);
    setError(null);
    return true;
  }

  async function perform(
    operation: () => Promise<ScenePlanOperationResultV1>,
    success: string,
  ) {
    if (busy) return false;
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      return applyResult(await operation(), success);
    } catch (caught) {
      setError(safeError(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }

  function selectScene(sceneId: string) {
    if (
      dirty &&
      !window.confirm("Discard unsaved scene changes and select another scene?")
    ) {
      return;
    }
    const selected = collection?.scenes.find((item) => item.scene_id === sceneId);
    if (!selected) return;
    setSelectedId(sceneId);
    setDraft(draftFromScene(selected));
    setMobileDetail(true);
    setMessage(`Active scene: ${sceneId}`);
  }

  async function save() {
    if (!scene || !draft || !collection || !repository.saveSceneRevision) return;
    await perform(
      () =>
        repository.saveSceneRevision!(runId, scene.scene_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          base_scene_revision_id: scene.scene_revision_id,
          changes: draft,
        }),
      "Scene revision saved.",
    );
  }

  function reset() {
    if (!scene || !window.confirm("Reset all unsaved changes for this scene?")) return;
    setDraft(draftFromScene(scene));
    setMessage("Unsaved changes reset.");
  }

  async function reorder(direction: "UP" | "DOWN") {
    if (!scene || !collection || !repository.reorderScene || dirty) return;
    await perform(
      () =>
        repository.reorderScene!(runId, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          scene_id: scene.scene_id,
          direction,
        }),
      `Scene moved ${direction.toLowerCase()}.`,
    );
  }

  async function duplicate() {
    if (!scene || !collection || !repository.duplicateScene || dirty) return;
    await perform(
      () =>
        repository.duplicateScene!(runId, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          scene_id: scene.scene_id,
        }),
      "Planning draft duplicated with blocked overlap.",
    );
  }

  async function mergeNext() {
    const next = scene && collection ? collection.scenes[scene.order] : null;
    if (!scene || !next || !collection || !repository.mergeScenes || dirty) return;
    await perform(
      () =>
        repository.mergeScenes!(runId, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          first_scene_id: scene.scene_id,
          second_scene_id: next.scene_id,
        }),
      "Adjacent scenes merged.",
    );
  }

  async function acceptScene() {
    if (!scene || !collection || !repository.acceptScene || dirty) return;
    await perform(
      () =>
        repository.acceptScene!(runId, scene.scene_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          scene_id: scene.scene_id,
          base_scene_revision_id: scene.scene_revision_id,
        }),
      "Scene accepted for visual planning only.",
    );
  }

  async function split(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!scene || !collection || !repository.splitScene) return;
    const form = new FormData(event.currentTarget);
    const success = await perform(
      () =>
        repository.splitScene!(runId, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          scene_id: scene.scene_id,
          base_scene_revision_id: scene.scene_revision_id,
          split_at: Number(form.get("split_at")),
          first_duration_seconds: Number(form.get("first_duration")),
          second_duration_seconds: Number(form.get("second_duration")),
        }),
      "Scene split with exact source coverage.",
    );
    if (success) {
      setSplitOpen(false);
      splitTrigger.current?.focus();
    }
  }

  async function requestRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!scene || !collection || !repository.requestSceneRevision) return;
    const form = new FormData(event.currentTarget);
    const success = await perform(
      () =>
        repository.requestSceneRevision!(runId, scene.scene_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          scene_id: scene.scene_id,
          base_scene_revision_id: scene.scene_revision_id,
          reason_code: form.get("reason_code") as
            | "OBJECTIVE_NEEDS_REVISION"
            | "CONTINUITY_NEEDS_REVISION"
            | "DURATION_NEEDS_REVISION"
            | "SOURCE_LINKAGE_NEEDS_REVISION"
            | "OTHER_PLANNING_REVISION",
          note: String(form.get("note") || "") || null,
        }),
      "Scene revision requested.",
    );
    if (success) {
      setRevisionOpen(false);
      revisionTrigger.current?.focus();
    }
  }

  async function restore(revisionId: string) {
    if (!scene || !collection || !repository.restoreSceneRevision || dirty) return;
    await perform(
      () =>
        repository.restoreSceneRevision!(runId, scene.scene_id, {
          schema_version: 1,
          base_collection_revision_id: collection.collection_revision_id,
          base_scene_revision_id: scene.scene_revision_id,
          restore_scene_revision_id: revisionId,
          reason: "Restore selected prior planning revision",
        }),
      "Prior scene revision restored as a new current revision.",
    );
  }

  if (access === undefined) {
    return <div className="page-stack"><h1>Scene planning</h1><p role="status">Loading scene-planning authority…</p></div>;
  }
  if (access === null || review === null) {
    return (
      <div className="page-stack not-found" role="alert">
        <h1>Scene planning unavailable</h1>
        <p>{error ?? "The run or review was not found."}</p>
        <Link to={`/production/runs/${encodeURIComponent(runId)}`}>Back to StoryPlan review</Link>
      </div>
    );
  }
  if (!access.editable || collection === null || scene === null || draft === null) {
    return (
      <div className="page-stack">
        <h1>Scene planning locked</h1>
        <p role="alert">This workspace is read-only or unavailable.</p>
        {access.blocker_codes.map((code) => <code key={code}>{code}</code>)}
        <p>Accept the current StoryPlan revision before editing scene plans.</p>
        <Link to={`/production/runs/${encodeURIComponent(runId)}`}>Back to StoryPlan review</Link>
      </div>
    );
  }

  return (
    <div className="page-stack scene-planning-workspace">
      <header className="page-heading">
        <div>
          <p className="eyebrow">PLAN_ONLY · process-local scene planning</p>
          <h1>Scene planning workspace</h1>
          <code>{runId}</code>
        </div>
        <Link
          to={`/production/runs/${encodeURIComponent(runId)}`}
          onClick={(event) => {
            if (dirty && !window.confirm("Leave and discard unsaved scene changes?")) {
              event.preventDefault();
            }
          }}
        >
          Back to StoryPlan review
        </Link>
      </header>
      <p role="status" aria-live="polite">
        {busy ? "Scene-planning operation in progress." : message}
        {dirty ? " Unsaved scene changes." : ""}
      </p>
      {error ? <div role="alert" className="status-callout status-callout--blocking">{error}</div> : null}
      <div
        className={`scene-planning-layout ${mobileDetail ? "mobile-scene-detail" : ""}`}
      >
        <SceneNavigation scenes={collection.scenes} selectedSceneId={scene.scene_id} dirty={dirty} onSelect={selectScene} />
        <div className="scene-planning-center">
          <button
            type="button"
            className="small-screen-scene-back"
            onClick={() => {
              setMobileDetail(false);
              window.requestAnimationFrame(() =>
                document
                  .querySelector<HTMLElement>(".scene-navigation button")
                  ?.focus(),
              );
            }}
          >
            Back to scene list
          </button>
          <SceneEditor scene={scene} draft={draft} editable={access.editable} dirty={dirty} busy={busy} onChange={setDraft} onSave={save} onReset={reset} />
          <section className="surface" aria-labelledby="scene-operations-title">
            <h2 id="scene-operations-title">Bounded planning operations</h2>
            <div className="scene-operation-grid">
              <button type="button" disabled={scene.order === 1 || dirty || busy} onClick={() => reorder("UP")}>Move up</button>
              <button type="button" disabled={scene.order === collection.scenes.length || dirty || busy} onClick={() => reorder("DOWN")}>Move down</button>
              <button ref={splitTrigger} type="button" disabled={dirty || busy} onClick={() => setSplitOpen(true)}>Split scene</button>
              <button type="button" disabled={scene.order === collection.scenes.length || dirty || busy} onClick={mergeNext}>Merge with next</button>
              <button type="button" disabled={dirty || busy} onClick={duplicate}>Duplicate as draft</button>
              <button type="button" disabled={dirty || busy || !!scene.blocker_codes.length || !!scene.warning_codes.length} onClick={acceptScene}>Accept for visual planning</button>
              <button ref={revisionTrigger} type="button" disabled={dirty || busy} onClick={() => setRevisionOpen(true)}>Request revision</button>
            </div>
            {scene.status === "ACCEPTED_FOR_VISUAL_PLANNING" ? (
              <Link
                className="primary-link-action"
                to={`/production/runs/${encodeURIComponent(runId)}/scenes/${encodeURIComponent(scene.scene_id)}/visuals`}
              >
                Open visual candidate workspace
              </Link>
            ) : null}
          </section>
          <section className="surface" aria-labelledby="scene-history-title">
            <h2 id="scene-history-title">Scene revision history</h2>
            <ol>
              {history?.revisions.map((revision) => (
                <li key={revision.scene_revision_id}>
                  <code>{revision.scene_revision_id}</code>{" "}
                  {revision.scene_revision_id === scene.scene_revision_id ? (
                    <strong>Current</strong>
                  ) : (
                    <button type="button" disabled={dirty || busy} onClick={() => restore(revision.scene_revision_id)}>
                      Restore as new revision
                    </button>
                  )}
                </li>
              ))}
            </ol>
          </section>
        </div>
        <details className="scene-authority-collapse" open>
          <summary>Authority and readiness summary</summary>
          <SceneAuthorityPanel collection={collection} scene={scene} review={review} />
        </details>
      </div>
      <section className="surface">
        <h2>Duration and coverage summary</h2>
        <dl className="review-detail-grid">
          <div><dt>Selected scene planned duration</dt><dd>{scene.planned_duration_seconds}s</dd></div>
          <div><dt>Total scene-plan duration</dt><dd>{collection.total_planned_duration_seconds}s</dd></div>
          <div><dt>StoryPlan target</dt><dd>{collection.target_duration_seconds}s</dd></div>
          <div><dt>Target range</dt><dd>{collection.target_min_seconds}–{collection.target_max_seconds}s</dd></div>
          <div><dt>Source coverage</dt><dd>{collection.source_coverage_valid ? "Complete" : "Blocked"}</dd></div>
        </dl>
        <p>Scene plans reset when the local host stops.</p>
      </section>
      {splitOpen ? (
        <div role="dialog" aria-modal="true" aria-labelledby="split-title" className="review-dialog-backdrop">
          <form className="review-dialog" onSubmit={split}>
            <h2 id="split-title">Split scene source coverage</h2>
            <label>Split source position<input name="split_at" type="number" min={scene.source_coverage.start + 1} max={scene.source_coverage.end - 1} required autoFocus /></label>
            <label>First duration<input name="first_duration" type="number" min="0.01" step="0.01" required /></label>
            <label>Second duration<input name="second_duration" type="number" min="0.01" step="0.01" required /></label>
            <div className="review-actions"><button type="submit">Create split revision</button><button type="button" className="secondary-action" onClick={() => { setSplitOpen(false); splitTrigger.current?.focus(); }}>Cancel</button></div>
          </form>
        </div>
      ) : null}
      {revisionOpen ? (
        <div role="dialog" aria-modal="true" aria-labelledby="revision-request-title" className="review-dialog-backdrop">
          <form className="review-dialog" onSubmit={requestRevision}>
            <fieldset><legend id="revision-request-title">Request scene revision</legend>
              <label>Reason<select name="reason_code" autoFocus><option value="OBJECTIVE_NEEDS_REVISION">Objective needs revision</option><option value="CONTINUITY_NEEDS_REVISION">Continuity needs revision</option><option value="DURATION_NEEDS_REVISION">Duration needs revision</option><option value="SOURCE_LINKAGE_NEEDS_REVISION">Source linkage needs revision</option><option value="OTHER_PLANNING_REVISION">Other planning revision</option></select></label>
              <label>Optional note<textarea name="note" maxLength={500} /></label>
            </fieldset>
            <div className="review-actions"><button type="submit">Submit revision request</button><button type="button" className="secondary-action" onClick={() => { setRevisionOpen(false); revisionTrigger.current?.focus(); }}>Cancel</button></div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
