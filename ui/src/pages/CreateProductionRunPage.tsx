import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ProductionContractError } from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";

export function CreateProductionRunPage() {
  const repository = useProductionRepository();
  const navigate = useNavigate();
  const [sourceContent, setSourceContent] = useState(
    "Learning to make room for uncertainty",
  );
  const [language, setLanguage] = useState<"en" | "vi">("en");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{
    readonly title: string;
    readonly detail: string;
  } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await repository.createPlanOnlyRun({
        schema_version: 1,
        input_mode: "TOPIC",
        source_content: sourceContent,
        language,
        character_scope: "recurring_female",
        requested_scene_count: 8,
      });
      if (result.error !== null) {
        setError({
          title: result.error.status === "BLOCKED" ? "Planning blocked" : "Planning failed",
          detail: result.error.message,
        });
      } else if (result.run !== null) {
        navigate(`/production/runs/${encodeURIComponent(result.run.run_id)}`);
      }
    } catch (caught) {
      setError({
        title:
          caught instanceof ProductionContractError
            ? "Invalid backend response"
            : "Backend unavailable",
        detail:
          caught instanceof ProductionContractError
            ? "The PLAN_ONLY backend response failed contract validation."
            : "The PLAN_ONLY planning backend could not be reached safely.",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page-stack">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Create video</p>
          <h1>Start with a clear plan</h1>
          <p>Create a backend-backed plan without generating narration, images, or video.</p>
        </div>
      </header>

      <form className="form-stack" onSubmit={submit}>
        <section className="surface" aria-labelledby="input-section-title">
          <div className="section-heading">
            <span className="step-number">1</span>
            <div>
              <h2 id="input-section-title">Input</h2>
              <p>Describe the emotional story you want to plan.</p>
            </div>
          </div>
          <label htmlFor="source-content">Topic or narration</label>
          <textarea
            id="source-content"
            name="source-content"
            rows={5}
            value={sourceContent}
            onChange={(event) => setSourceContent(event.currentTarget.value)}
            required
          />
        </section>

        <section className="surface" aria-labelledby="settings-section-title">
          <div className="section-heading">
            <span className="step-number">2</span>
            <div>
              <h2 id="settings-section-title">Production settings</h2>
              <p>Unavailable authority remains visibly locked.</p>
            </div>
          </div>
          <div className="field-grid">
            <div>
              <label htmlFor="language">Language</label>
              <select
                id="language"
                value={language}
                onChange={(event) =>
                  setLanguage(event.currentTarget.value === "vi" ? "vi" : "en")
                }
              >
                <option value="en">English</option>
                <option value="vi">Vietnamese</option>
              </select>
            </div>
            <div>
              <label htmlFor="aspect-ratio">Aspect ratio</label>
              <select
                id="aspect-ratio"
                value="9:16"
                aria-describedby="aspect-ratio-help"
                disabled
              >
                <option value="9:16">9:16 — Portrait</option>
              </select>
              <p className="field-help" id="aspect-ratio-help">
                Locked by the current production contract.
              </p>
            </div>
            <div>
              <label htmlFor="voice">Narration voice</label>
              <select id="voice" value="" aria-describedby="voice-help" disabled>
                <option value="">Not available</option>
              </select>
              <p className="field-help" id="voice-help">
                No topic-production voice contract is available.
              </p>
            </div>
            <div>
              <label htmlFor="visual-mode">Visual mode</label>
              <select id="visual-mode" value="illustrated_scene" disabled>
                <option value="illustrated_scene">Illustrated scene</option>
              </select>
            </div>
          </div>
        </section>

        <section className="surface" aria-labelledby="mode-section-title">
          <div className="section-heading">
            <span className="step-number">3</span>
            <div>
              <h2 id="mode-section-title">Execution mode</h2>
              <p>Select only a capability currently available to this client.</p>
            </div>
          </div>
          <fieldset className="mode-grid">
            <legend className="sr-only">Execution mode</legend>
            <label className="mode-option mode-option--disabled">
              <input
                type="radio"
                name="execution-mode"
                value="MOCK"
                aria-describedby="mock-mode-help"
                disabled
              />
              <span>
                <strong>MOCK</strong>
                <small id="mock-mode-help">Available only through explicit test fixtures.</small>
              </span>
            </label>
            <label className="mode-option">
              <input
                type="radio"
                name="execution-mode"
                value="PLAN_ONLY"
                aria-describedby="plan-only-mode-help"
                defaultChecked
              />
              <span>
                <strong>PLAN_ONLY</strong>
                <small id="plan-only-mode-help">
                  Canonical planning only. Rendering remains unavailable.
                </small>
              </span>
            </label>
            <label className="mode-option mode-option--disabled">
              <input
                type="radio"
                name="execution-mode"
                value="FULL_RENDER"
                aria-describedby="full-render-mode-help"
                disabled
              />
              <span>
                <strong>FULL_RENDER</strong>
                <small id="full-render-mode-help">
                  Locked until backend, synthetic closure, and live canary approval.
                </small>
              </span>
            </label>
          </fieldset>
        </section>

        <section className="surface submission-area" aria-labelledby="submission-title">
          <div>
            <h2 id="submission-title">Create the plan</h2>
            <p>The backend returns planning presentation data only.</p>
          </div>
          <button type="submit" disabled={submitting}>
            {submitting ? "Planning…" : "Create PLAN_ONLY run"}
          </button>
        </section>
        {error && (
          <section className="surface status-callout status-callout--blocking" role="alert">
            <div>
              <h2>{error.title}</h2>
              <p>{error.detail}</p>
            </div>
          </section>
        )}
      </form>
    </div>
  );
}
