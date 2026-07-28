import type {
  ScenePlanEditChangesV1,
  ScenePlanV1,
} from "../../contracts/v1/production";

export type SceneDraft = Required<ScenePlanEditChangesV1>;

export function draftFromScene(scene: ScenePlanV1): SceneDraft {
  return {
    objective: scene.objective,
    emotional_intent: scene.emotional_intent,
    environment: scene.environment,
    character_action: scene.character_action,
    objects: scene.objects,
    composition_guidance: scene.composition_guidance,
    continuity_notes: scene.continuity_notes,
    camera_motion_intent: scene.camera_motion_intent,
    transition_intent: scene.transition_intent,
    planned_duration_seconds: scene.planned_duration_seconds,
    planning_note: scene.planning_note,
  };
}

export function SceneEditor({
  scene,
  draft,
  editable,
  dirty,
  busy,
  onChange,
  onSave,
  onReset,
}: {
  readonly scene: ScenePlanV1;
  readonly draft: SceneDraft;
  readonly editable: boolean;
  readonly dirty: boolean;
  readonly busy: boolean;
  readonly onChange: (draft: SceneDraft) => void;
  readonly onSave: () => void;
  readonly onReset: () => void;
}) {
  const set = <K extends keyof SceneDraft>(key: K, value: SceneDraft[K]) =>
    onChange({ ...draft, [key]: value });
  const describedBy = "scene-editor-authority-note";
  return (
    <section className="surface scene-editor" aria-labelledby="scene-editor-title">
      <p className="eyebrow">Explicit Save revision; no autosave</p>
      <h2 id="scene-editor-title">
        Scene {scene.order}: <code>{scene.scene_id}</code>
      </h2>
      <p id={describedBy}>
        StoryPlan identity, source spans, beat IDs, producer metadata, and authority
        fields are immutable.
      </p>
      <div className="scene-editor-fields">
        <label>
          Scene objective
          <textarea
            value={draft.objective}
            maxLength={1000}
            disabled={!editable || busy}
            aria-describedby={describedBy}
            onChange={(event) => set("objective", event.target.value)}
          />
        </label>
        <label>
          Emotional intent
          <textarea
            value={draft.emotional_intent}
            maxLength={500}
            disabled={!editable || busy}
            onChange={(event) => set("emotional_intent", event.target.value)}
          />
        </label>
        <label>
          Environment
          <textarea
            value={draft.environment}
            maxLength={500}
            disabled={!editable || busy}
            onChange={(event) => set("environment", event.target.value)}
          />
        </label>
        <label>
          Character action or pose request
          <textarea
            value={draft.character_action}
            maxLength={500}
            disabled={!editable || busy}
            onChange={(event) => set("character_action", event.target.value)}
          />
        </label>
        <label>
          Objects or props (one per line)
          <textarea
            value={draft.objects.join("\n")}
            maxLength={1000}
            disabled={!editable || busy}
            onChange={(event) =>
              set(
                "objects",
                event.target.value
                  .split("\n")
                  .map((item) => item.trim())
                  .filter(Boolean),
              )
            }
          />
        </label>
        <label>
          Composition guidance
          <textarea
            value={draft.composition_guidance}
            maxLength={1000}
            disabled={!editable || busy}
            onChange={(event) => set("composition_guidance", event.target.value)}
          />
        </label>
        <label>
          Continuity notes
          <textarea
            value={draft.continuity_notes}
            maxLength={1000}
            disabled={!editable || busy}
            onChange={(event) => set("continuity_notes", event.target.value)}
          />
        </label>
        <label>
          Camera-motion intent
          <input
            value={draft.camera_motion_intent}
            maxLength={300}
            disabled={!editable || busy}
            onChange={(event) => set("camera_motion_intent", event.target.value)}
          />
        </label>
        <label>
          Transition intent
          <textarea
            value={draft.transition_intent}
            maxLength={500}
            disabled={!editable || busy}
            onChange={(event) => set("transition_intent", event.target.value)}
          />
        </label>
        <label>
          Planned duration (seconds)
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={draft.planned_duration_seconds}
            disabled={!editable || busy}
            aria-invalid={draft.planned_duration_seconds <= 0}
            onChange={(event) =>
              set("planned_duration_seconds", Number(event.target.value))
            }
          />
        </label>
        <label>
          Planning note
          <textarea
            value={draft.planning_note}
            maxLength={1000}
            disabled={!editable || busy}
            onChange={(event) => set("planning_note", event.target.value)}
          />
        </label>
      </div>
      <div className="review-actions">
        <button
          type="button"
          disabled={!editable || !dirty || busy || draft.planned_duration_seconds <= 0}
          onClick={onSave}
        >
          {busy ? "Saving revision…" : "Save revision"}
        </button>
        <button
          type="button"
          className="secondary-action"
          disabled={!dirty || busy}
          onClick={onReset}
        >
          Reset unsaved changes
        </button>
      </div>
    </section>
  );
}
