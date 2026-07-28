import type {
  ProductionRunViewV1,
  PublicConditionV1,
  StoryPlanRevisionV1,
} from "../../contracts/v1/production";

function ExactSeconds({ value }: { readonly value: number }) {
  return <code>{value} s</code>;
}

function ConditionGroup({
  heading,
  conditions,
  emptyMessage,
}: {
  readonly heading: string;
  readonly conditions: readonly PublicConditionV1[];
  readonly emptyMessage: string;
}) {
  const headingId = `condition-${heading.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <section className="condition-group" aria-labelledby={headingId}>
      <h3 id={headingId}>{heading}</h3>
      {conditions.length === 0 ? (
        <p>{emptyMessage}</p>
      ) : (
        <ul className="condition-list">
          {conditions.map((condition, index) => (
            <li key={`${condition.severity}-${condition.code}-${index}`}>
              <span className="state-icon" aria-hidden="true">
                {condition.severity === "WARNING"
                  ? "!"
                  : condition.severity === "INFO"
                    ? "i"
                    : "■"}
              </span>
              <div>
                <h4>{condition.title}</h4>
                <code>{condition.code}</code>
                <p>{condition.detail}</p>
                {[...condition.beat_ids, ...condition.scene_ids].length > 0 ? (
                  <p className="condition-links">
                    {condition.beat_ids.map((beatId) => (
                      <a href={`#${beatId}`} key={beatId}>
                        {beatId}
                      </a>
                    ))}
                    {condition.scene_ids.map((sceneId) => (
                      <a href={`#${sceneId}`} key={sceneId}>
                        {sceneId}
                      </a>
                    ))}
                  </p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function StoryPlanReviewSections({
  run,
  revision,
  copyStatus,
  onCopyNarration,
}: {
  readonly run: ProductionRunViewV1;
  readonly revision: StoryPlanRevisionV1 | null;
  readonly copyStatus: string | null;
  readonly onCopyNarration: () => void;
}) {
  const story = revision?.story_plan ?? null;
  const timelineByScene = new Map(
    (revision?.timeline.rows ?? []).map((row) => [row.scene_id, row]),
  );
  const conditions = revision?.warnings.conditions ?? run.warnings.conditions;
  const blockingConditions = conditions.filter(
    (condition) =>
      condition.severity === "BLOCKED" || condition.severity === "ERROR",
  );
  const warnings = conditions.filter(
    (condition) => condition.severity === "WARNING",
  );
  const information = conditions.filter(
    (condition) => condition.severity === "INFO",
  );
  const wordCount =
    story === null
      ? 0
      : story.narration_text.trim().split(/\s+/u).filter(Boolean).length;
  const characterCount =
    story === null ? 0 : Array.from(story.narration_text).length;

  return (
    <div className="story-review-sections">
      <section className="surface" aria-labelledby="story-narration-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Canonical StoryPlan authority</p>
            <h2 id="story-narration-title">Story narration</h2>
          </div>
          {story ? (
            <button type="button" className="secondary-action" onClick={onCopyNarration}>
              Copy narration
            </button>
          ) : null}
        </div>
        {story ? (
          <>
            <p className="canonical-narration">{story.narration_text}</p>
            <dl className="inline-details">
              <div>
                <dt>Characters</dt>
                <dd>{characterCount}</dd>
              </div>
              <div>
                <dt>Words</dt>
                <dd>{wordCount}</dd>
              </div>
              <div>
                <dt>Target duration</dt>
                <dd>
                  <ExactSeconds value={story.target_duration_seconds} />
                </dd>
              </div>
              <div>
                <dt>Estimated narration duration</dt>
                <dd>Not supplied</dd>
              </div>
            </dl>
            {copyStatus ? (
              <p role="status" aria-live="polite">
                {copyStatus}
              </p>
            ) : null}
          </>
        ) : (
          <p>Canonical narration is unavailable for this run.</p>
        )}
      </section>

      <section className="surface" aria-labelledby="semantic-beats-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Exact narration partition</p>
            <h2 id="semantic-beats-title">Semantic beats</h2>
          </div>
        </div>
        {story ? (
          <ol className="review-beat-list">
            {story.semantic_beats.map((beat) => {
              const codes = conditions
                .filter((condition) => condition.beat_ids.includes(beat.beat_id))
                .map((condition) => condition.code);
              const relatedScene = story.scenes.find(
                (scene) => scene.source_beat_id === beat.beat_id,
              );
              return (
                <li id={beat.beat_id} key={beat.beat_id}>
                  <div className="review-item-heading">
                    <span>{beat.order}</span>
                    <div>
                      <code>{beat.beat_id}</code>
                      <h3>{beat.semantic_purpose}</h3>
                    </div>
                    <ExactSeconds value={beat.duration_seconds} />
                  </div>
                  <p className="beat-narration">{beat.narration_segment}</p>
                  <dl className="review-detail-grid">
                    <div>
                      <dt>Source span</dt>
                      <dd>
                        <code>
                          {beat.source_span.start}:{beat.source_span.end}
                        </code>
                      </dd>
                    </div>
                    <div>
                      <dt>Related scene</dt>
                      <dd>
                        <code>{relatedScene?.scene_id ?? "Not available"}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>Transition intent</dt>
                      <dd>{beat.transition_intent}</dd>
                    </div>
                    <div>
                      <dt>Condition codes</dt>
                      <dd>{codes.length > 0 ? codes.join(", ") : "None"}</dd>
                    </div>
                  </dl>
                </li>
              );
            })}
          </ol>
        ) : (
          <p>Semantic beats are unavailable for this run.</p>
        )}
      </section>

      <section className="surface" aria-labelledby="scenes-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Read-only scene projection</p>
            <h2 id="scenes-title">Scenes</h2>
          </div>
        </div>
        <p>This is a planning projection. No scene media has been generated.</p>
        {story ? (
          <ol className="scene-list">
            {story.scenes.map((scene) => {
              const timeline = timelineByScene.get(scene.scene_id);
              return (
                <li id={scene.scene_id} key={scene.scene_id}>
                  <div className="scene-list__identity">
                    <code>{scene.scene_id}</code>
                    <span>{scene.order}</span>
                  </div>
                  <div>
                    <h3>{scene.meaning}</h3>
                    <p>
                      From <code>{scene.source_beat_id}</code>
                    </p>
                    <p>{scene.visual_intent}</p>
                    <p>
                      Presentation status: <strong>Planned projection</strong>
                    </p>
                  </div>
                  <div>
                    <ExactSeconds value={scene.planned_duration_seconds} />
                    <p>
                      Timeline{" "}
                      {timeline
                        ? `${timeline.start_seconds}–${timeline.end_seconds} s`
                        : "not supplied"}
                    </p>
                  </div>
                </li>
              );
            })}
          </ol>
        ) : (
          <p>Scene projection is unavailable for this run.</p>
        )}
      </section>

      <section className="surface" aria-labelledby="duration-assessment-title">
        <p className="eyebrow">Planned values only</p>
        <h2 id="duration-assessment-title">Duration assessment</h2>
        {revision ? (
          <dl className="review-detail-grid">
            <div>
              <dt>StoryPlan target duration</dt>
              <dd>
                <ExactSeconds
                  value={revision.duration_assessment.target_duration_seconds}
                />
              </dd>
            </div>
            <div>
              <dt>Target minimum</dt>
              <dd>
                <ExactSeconds value={revision.duration_assessment.target_min_seconds} />
              </dd>
            </div>
            <div>
              <dt>Target maximum</dt>
              <dd>
                <ExactSeconds value={revision.duration_assessment.target_max_seconds} />
              </dd>
            </div>
            <div>
              <dt>Total semantic-beat duration</dt>
              <dd>
                <ExactSeconds
                  value={revision.duration_assessment.semantic_beat_total_seconds}
                />
              </dd>
            </div>
            <div>
              <dt>Total scene-planning duration</dt>
              <dd>
                <ExactSeconds
                  value={revision.duration_assessment.scene_planning_total_seconds}
                />
              </dd>
            </div>
            <div>
              <dt>Duration eligibility</dt>
              <dd>{revision.duration_assessment.status}</dd>
            </div>
            <div>
              <dt>Duration reason</dt>
              <dd>
                <code>{revision.duration_assessment.reason_code}</code>
              </dd>
            </div>
            <div>
              <dt>Value authority</dt>
              <dd>{revision.duration_assessment.value_authority}</dd>
            </div>
          </dl>
        ) : (
          <p>Duration assessment is unavailable for this run.</p>
        )}
        <p>No measured narration, render-clip, or video duration exists.</p>
      </section>

      <section className="surface" aria-labelledby="identity-scope-title">
        <p className="eyebrow">Planning requirements, not asset verification</p>
        <h2 id="identity-scope-title">Character and identity scope</h2>
        {revision ? (
          <dl className="review-detail-grid">
            <div>
              <dt>Requested character scope</dt>
              <dd>{revision.identity_scope.requested_scope}</dd>
            </div>
            <div>
              <dt>Eligibility</dt>
              <dd>{revision.identity_scope.eligibility_status}</dd>
            </div>
            <div>
              <dt>Recurring woman required</dt>
              <dd>Yes</dd>
            </div>
            <div>
              <dt>Anonymous background people</dt>
              <dd>
                {revision.identity_scope.anonymous_background_people_allowed
                  ? "Allowed"
                  : "Not requested"}
              </dd>
            </div>
            <div>
              <dt>Identity continuity</dt>
              <dd>Required during future scene planning</dd>
            </div>
            <div>
              <dt>Visual identity verified</dt>
              <dd>No — no generated assets exist</dd>
            </div>
          </dl>
        ) : (
          <p>Identity eligibility is unavailable for this run.</p>
        )}
      </section>

      <section className="surface" aria-labelledby="planner-metadata-title">
        <p className="eyebrow">Presentation-safe producer identity</p>
        <h2 id="planner-metadata-title">Planner metadata</h2>
        {story ? (
          <dl className="review-detail-grid">
            <div>
              <dt>Planner ID</dt>
              <dd>
                <code>{story.planner_metadata.planner_id}</code>
              </dd>
            </div>
            <div>
              <dt>Planner version</dt>
              <dd>{story.planner_metadata.planner_version}</dd>
            </div>
            <div>
              <dt>Deterministic</dt>
              <dd>Yes</dd>
            </div>
            <div>
              <dt>External calls</dt>
              <dd>{story.planner_metadata.external_calls}</dd>
            </div>
            <div>
              <dt>Story planning producer</dt>
              <dd>Authorized</dd>
            </div>
            <div>
              <dt>Render-production eligible</dt>
              <dd>No</dd>
            </div>
          </dl>
        ) : (
          <p>Planner metadata is unavailable for this run.</p>
        )}
      </section>

      <section className="surface" aria-labelledby="warnings-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Exact backend reason codes</p>
            <h2 id="warnings-title">Warnings and conditions</h2>
          </div>
          <span>{conditions.length}</span>
        </div>
        <ConditionGroup
          heading="Blocking issues"
          conditions={blockingConditions}
          emptyMessage="No blocking conditions were supplied."
        />
        <ConditionGroup
          heading="Warnings"
          conditions={warnings}
          emptyMessage="No warnings were supplied."
        />
        <ConditionGroup
          heading="Informational conditions"
          conditions={information}
          emptyMessage="No informational conditions were supplied."
        />
      </section>
    </div>
  );
}
