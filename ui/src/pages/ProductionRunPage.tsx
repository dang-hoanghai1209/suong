import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { StatusBadge } from "../components/status/StatusBadge";
import type { ProductionRunViewV1, TimelineRowV1 } from "../contracts/v1/production";
import { useProductionRepository } from "../app/ProductionRepositoryContext";

function ExactDuration({ value }: { readonly value: number | null }) {
  return <code>{value === null ? "Not available" : `${value} s`}</code>;
}

function TimelineRow({ row }: { readonly row: TimelineRowV1 }) {
  return (
    <tr>
      <th scope="row">
        <code>{row.scene_id}</code>
      </th>
      <td>
        <ExactDuration value={row.start_seconds} />
      </td>
      <td>
        <ExactDuration value={row.narration_slot_duration_seconds} />
      </td>
      <td>
        <ExactDuration value={row.render_clip_duration_seconds} />
      </td>
      <td>
        <ExactDuration value={row.end_seconds} />
      </td>
    </tr>
  );
}

export function ProductionRunPage() {
  const repository = useProductionRepository();
  const { runId = "mock-plan-2026-01" } = useParams();
  const [run, setRun] = useState<ProductionRunViewV1 | null | undefined>(undefined);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let active = true;
    setRun(undefined);
    setUnavailable(false);
    void repository
      .getRun(runId)
      .then((value) => {
        if (active) {
          setRun(value);
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

  if (run === undefined) {
    return (
      <div className="page-stack">
        <h1>Loading production run</h1>
        <p role="status" aria-live="polite">
          Loading plan review…
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

  const blocking = run.status === "BLOCKED" || run.status === "FAILED";

  return (
    <div className="page-stack">
      <header className="page-heading">
        <div>
          <p className="eyebrow">
            {run.execution_mode === "MOCK" ? "Synthetic UI mock" : "Backend PLAN_ONLY"}
          </p>
          <h1>Production run</h1>
          <code>{run.run_id}</code>
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
        <dl className="inline-details">
          <div>
            <dt>Mode</dt>
            <dd>{run.execution_mode}</dd>
          </div>
          <div>
            <dt>Warnings</dt>
            <dd>{run.warnings.warning_count}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{run.created_at}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{run.updated_at}</dd>
          </div>
        </dl>
      </section>

      <section className="surface" aria-labelledby="story-plan-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">StoryPlan</p>
            <h2 id="story-plan-title">
              {run.story_plan?.topic ?? "Canonical plan unavailable"}
            </h2>
          </div>
          {run.story_plan && <ExactDuration value={run.story_plan.target_duration_seconds} />}
        </div>
        {run.story_plan ? (
          <>
            <p className="narration">{run.story_plan.narration_text}</p>
            <div className="arc-list" aria-label="Emotional arc">
              {run.story_plan.emotional_arc.map((state, index) => (
                <span key={`${index}-${state}`}>{state}</span>
              ))}
            </div>
          </>
        ) : (
          <p>Not available</p>
        )}
      </section>

      <section className="surface" aria-labelledby="scenes-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Canonical order</p>
            <h2 id="scenes-title">Scenes</h2>
          </div>
        </div>
        {run.story_plan ? (
          <ol className="scene-list">
            {run.story_plan.scenes.map((scene) => (
              <li key={scene.scene_id}>
                <div className="scene-list__identity">
                  <code>{scene.scene_id}</code>
                  <span>{scene.order}</span>
                </div>
                <div>
                  <h3>{scene.meaning}</h3>
                  <p>{scene.visual_intent}</p>
                </div>
                <ExactDuration value={scene.planned_duration_seconds} />
              </li>
            ))}
          </ol>
        ) : (
          <p>Not available</p>
        )}
      </section>

      <section className="surface" aria-labelledby="timeline-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Backend-shaped authority</p>
            <h2 id="timeline-title">Timeline</h2>
          </div>
          <span className="authority-label">{run.timeline?.authority ?? "Not available"}</span>
        </div>
        {run.timeline ? (
          <>
            <div className="table-scroll">
              <table>
                <caption className="sr-only">
                  Scene timeline values supplied by the planning backend
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Scene</th>
                    <th scope="col">Start</th>
                    <th scope="col">Narration slot</th>
                    <th scope="col">Render clip</th>
                    <th scope="col">End</th>
                  </tr>
                </thead>
                <tbody>
                  {run.timeline.rows.map((row) => (
                    <TimelineRow key={row.scene_id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
            <dl className="timeline-summary">
              <div>
                <dt>Transition profile</dt>
                <dd>{run.timeline.transition_profile_id ?? "Not available"}</dd>
              </div>
              <div>
                <dt>Configured transition</dt>
                <dd>
                  <ExactDuration value={run.timeline.configured_transition_seconds} />
                </dd>
              </div>
              <div>
                <dt>Effective transition</dt>
                <dd>
                  <ExactDuration value={run.timeline.effective_transition_seconds} />
                </dd>
              </div>
              <div>
                <dt>Total</dt>
                <dd>
                  <ExactDuration value={run.timeline.total_duration_seconds} />
                </dd>
              </div>
            </dl>
          </>
        ) : (
          <p>Not available</p>
        )}
      </section>

      <section className="surface" aria-labelledby="warnings-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Validation</p>
            <h2 id="warnings-title">Warnings and conditions</h2>
          </div>
          <span>{run.warnings.warning_count}</span>
        </div>
        {run.warnings.conditions.length === 0 ? (
          <p>No warnings were supplied for this plan.</p>
        ) : (
          <ul className="condition-list">
            {run.warnings.conditions.map((condition) => (
              <li key={condition.code}>
                <span className="state-icon" aria-hidden="true">
                  {condition.severity === "WARNING" ? "!" : "■"}
                </span>
                <div>
                  <h3>{condition.title}</h3>
                  <code>{condition.code}</code>
                  <p>{condition.detail}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="surface" aria-labelledby="run-readiness-title">
        <p className="eyebrow">Render readiness</p>
        <h2 id="run-readiness-title">Full render locked</h2>
        <p>
          {run.render_readiness.reason_codes.map((code) => (
            <code key={code}>{code}</code>
          ))}
        </p>
      </section>

      <Link to="/production">Return to production dashboard</Link>
    </div>
  );
}
