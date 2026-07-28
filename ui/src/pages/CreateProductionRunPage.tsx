import { Link } from "react-router-dom";

export function CreateProductionRunPage() {
  return (
    <div className="page-stack">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Create video</p>
          <h1>Start with a clear plan</h1>
          <p>UI.1 provides structure only. No submission leaves this browser.</p>
        </div>
      </header>

      <form className="form-stack" onSubmit={(event) => event.preventDefault()}>
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
            defaultValue="Learning to make room for uncertainty"
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
              <select id="language" defaultValue="en">
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
            <label className="mode-option">
              <input type="radio" name="execution-mode" value="MOCK" defaultChecked />
              <span>
                <strong>MOCK</strong>
                <small>Bundled fixtures. Zero network and no persistence.</small>
              </span>
            </label>
            <label className="mode-option mode-option--disabled">
              <input
                type="radio"
                name="execution-mode"
                value="PLAN_ONLY"
                aria-describedby="plan-only-mode-help"
                disabled
              />
              <span>
                <strong>PLAN_ONLY</strong>
                <small id="plan-only-mode-help">Backend integration pending.</small>
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
            <h2 id="submission-title">Review a synthetic plan</h2>
            <p>This opens deterministic fixture data and performs no submission.</p>
          </div>
          <Link className="button-link" to="/production/runs/mock-plan-2026-01">
            Open mock review
          </Link>
        </section>
      </form>
    </div>
  );
}
