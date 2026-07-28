import type { ScenePlanV1 } from "../../contracts/v1/production";

export function SceneNavigation({
  scenes,
  selectedSceneId,
  dirty,
  onSelect,
}: {
  readonly scenes: readonly ScenePlanV1[];
  readonly selectedSceneId: string | null;
  readonly dirty: boolean;
  readonly onSelect: (sceneId: string) => void;
}) {
  return (
    <nav className="surface scene-navigation" aria-label="Scene planning navigation">
      <p className="eyebrow">Canonical collection order</p>
      <h2>Scenes</h2>
      <ol>
        {scenes.map((scene) => (
          <li key={scene.scene_id}>
            <button
              type="button"
              aria-current={scene.scene_id === selectedSceneId ? "page" : undefined}
              onClick={() => onSelect(scene.scene_id)}
            >
              <span>
                Scene {scene.order}: <code>{scene.scene_id}</code>
              </span>
              <small>
                {scene.source_beat_id} · {scene.planned_duration_seconds}s
              </small>
              <small>
                {scene.status}
                {scene.blocker_codes.length ? " · blocked" : ""}
                {scene.warning_codes.length ? " · warning" : ""}
                {dirty && scene.scene_id === selectedSceneId ? " · unsaved" : ""}
              </small>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
