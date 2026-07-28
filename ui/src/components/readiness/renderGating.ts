export interface RenderGatePresentation {
  readonly enabled: boolean;
  readonly reasonCodes: readonly string[];
}

function invalidCapabilities(): RenderGatePresentation {
  return {
    enabled: false,
    reasonCodes: ["CAPABILITIES_INVALID"],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function evaluateRenderGate(capabilities: unknown): RenderGatePresentation {
  try {
    if (!isRecord(capabilities)) {
      return invalidCapabilities();
    }

    const gateFields = [
      capabilities.full_render_enabled,
      capabilities.backend_render_capability,
      capabilities.synthetic_closure_passed,
      capabilities.live_canary_passed,
    ];
    const reasonCodes = capabilities.render_lock_reason_codes;
    if (
      gateFields.some((value) => typeof value !== "boolean") ||
      !Array.isArray(reasonCodes) ||
      reasonCodes.some((code) => typeof code !== "string")
    ) {
      return invalidCapabilities();
    }

    const enabled =
      capabilities.full_render_enabled === true &&
      capabilities.backend_render_capability === true &&
      capabilities.synthetic_closure_passed === true &&
      capabilities.live_canary_passed === true;

    return {
      enabled,
      reasonCodes: enabled ? [] : [...reasonCodes],
    };
  } catch {
    return invalidCapabilities();
  }
}

const reasonLabels: Readonly<Record<string, string>> = {
  BACKEND_RENDER_CAPABILITY_DISABLED: "Backend render capability is not approved.",
  SYNTHETIC_CLOSURE_PENDING: "Synthetic lifecycle closure has not passed.",
  LIVE_CANARY_PENDING: "Live canary approval is pending.",
  CAPABILITIES_INVALID: "Render capability data is invalid or incomplete.",
};

export function renderLockReasonLabel(code: string): string {
  return reasonLabels[code] ?? "Rendering is locked by backend capability policy.";
}
