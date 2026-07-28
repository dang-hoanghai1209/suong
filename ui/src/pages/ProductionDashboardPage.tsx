import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { StatusBadge } from "../components/status/StatusBadge";
import type { ProductionDashboardViewV1 } from "../contracts/v1/production";
import { useProductionRepository } from "../app/ProductionRepositoryContext";

export function ProductionDashboardPage() {
  const repository = useProductionRepository();
  const [dashboard, setDashboard] = useState<ProductionDashboardViewV1 | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    void repository
      .getDashboard()
      .then((value) => {
        if (active) {
          setDashboard(value);
        }
      })
      .catch(() => {
        if (active) {
          setError(true);
        }
      });
    return () => {
      active = false;
    };
  }, [repository]);

  return (
    <div className="page-stack">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Production workspace</p>
          <h1>Video plans</h1>
          <p>Review plan-only runs before any generation or rendering is available.</p>
        </div>
        <Link className="button-link" to="/production/new">
          Create video
        </Link>
      </header>

      <section className="surface" aria-labelledby="recent-runs-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Environment: PLAN_ONLY</p>
            <h2 id="recent-runs-title">Recent runs</h2>
          </div>
        </div>
        {error ? (
          <div role="alert">
            <h3>Backend unavailable</h3>
            <p>The PLAN_ONLY run list could not be loaded.</p>
          </div>
        ) : dashboard === null ? (
          <p role="status" aria-live="polite">
            Loading runs…
          </p>
        ) : dashboard.runs.length === 0 ? (
          <div className="empty-state">
            <span className="empty-state__mark" aria-hidden="true">
              ○
            </span>
            <h3>No production plans yet</h3>
            <p>Create a backend-backed plan without generating media.</p>
            <Link to="/production/new">Start a plan</Link>
          </div>
        ) : (
          <div className="run-list">
            {dashboard.runs.map((run) => (
              <article key={run.run_id} className="run-card">
                <div>
                  <code>{run.run_id}</code>
                  <h3>{run.current_stage}</h3>
                </div>
                <StatusBadge status={run.status} />
                <Link to={`/production/runs/${encodeURIComponent(run.run_id)}`}>
                  Review plan
                </Link>
                <dl className="inline-details">
                  <div>
                    <dt>Warnings</dt>
                    <dd>{run.warning_count}</dd>
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
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
