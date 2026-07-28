import type {
  ScenePlanCollectionV1,
  ScenePlanV1,
  StoryPlanReviewViewV1,
} from "../../contracts/v1/production";

export function SceneAuthorityPanel({
  collection,
  scene,
  review,
}: {
  readonly collection: ScenePlanCollectionV1;
  readonly scene: ScenePlanV1;
  readonly review: StoryPlanReviewViewV1;
}) {
  const prior = collection.scenes[scene.order - 2]?.scene_id ?? "None";
  const next = collection.scenes[scene.order]?.scene_id ?? "None";
  return (
    <aside className="surface scene-authority" aria-labelledby="scene-authority-title">
      <p className="eyebrow">Planning authority only</p>
      <h2 id="scene-authority-title">Authority and readiness</h2>
      <dl>
        <div><dt>Run ID</dt><dd><code>{collection.run_id}</code></dd></div>
        <div><dt>Current StoryPlan revision</dt><dd>{review.current_revision_id}</dd></div>
        <div><dt>Accepted StoryPlan revision</dt><dd>{review.accepted_revision_id}</dd></div>
        <div><dt>Collection revision</dt><dd>{collection.collection_revision_id}</dd></div>
        <div><dt>Scene revision</dt><dd>{scene.scene_revision_id}</dd></div>
        <div><dt>Source beat</dt><dd>{scene.source_beat_id}</dd></div>
        <div><dt>Source narration span</dt><dd>{scene.source_coverage.start}–{scene.source_coverage.end}</dd></div>
        <div><dt>Duration validity</dt><dd>{collection.duration_valid ? "Valid" : "Warning"}</dd></div>
        <div><dt>Identity continuity</dt><dd>Planning required; not visually verified</dd></div>
        <div><dt>Environment continuity</dt><dd>{scene.continuity.environment_continuity}</dd></div>
        <div><dt>Object continuity</dt><dd>{scene.continuity.object_continuity}</dd></div>
        <div><dt>Prior scene</dt><dd>{prior}</dd></div>
        <div><dt>Next scene</dt><dd>{next}</dd></div>
        <div><dt>Ready for visual planning</dt><dd>{collection.ready_for_visual_planning ? "Yes" : "No"}</dd></div>
        <div><dt>Render authority</dt><dd>Not granted</dd></div>
        <div><dt>Media capability</dt><dd>Not granted</dd></div>
      </dl>
      <p>
        “Ready for visual planning” authorizes no provider request, media generation,
        renderer plan, or render execution.
      </p>
      {[...scene.blocker_codes, ...scene.warning_codes].map((code) => (
        <p className="status-callout status-callout--blocking" key={code}>
          <strong>{code}</strong>
        </p>
      ))}
    </aside>
  );
}
