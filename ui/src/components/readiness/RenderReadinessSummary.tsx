import { evaluateRenderGate, renderLockReasonLabel } from "./renderGating";

export function RenderReadinessSummary({
  capabilities,
}: {
  readonly capabilities: unknown;
}) {
  const gate = evaluateRenderGate(capabilities);

  return (
    <section className="readiness-panel" aria-labelledby="render-readiness-title">
      <div className="readiness-panel__heading">
        <span className="state-icon state-icon--blocked" aria-hidden="true">
          ■
        </span>
        <div>
          <p className="eyebrow">Render readiness</p>
          <h2 id="render-readiness-title">{gate.enabled ? "Ready" : "Full render locked"}</h2>
        </div>
      </div>
      {gate.enabled ? (
        <p>Backend rendering gates are approved.</p>
      ) : (
        <ul className="reason-list">
          {gate.reasonCodes.map((code) => (
            <li key={code}>
              <code>{code}</code>
              <span>{renderLockReasonLabel(code)}</span>
            </li>
          ))}
        </ul>
      )}
      <button type="button" disabled>
        Render video
      </button>
      <p className="supporting-copy">
        This interface cannot grant render authority. Backend enforcement is required.
      </p>
    </section>
  );
}
