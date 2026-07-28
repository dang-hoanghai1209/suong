import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { App } from "../app/App";
import { RenderReadinessSummary } from "../components/readiness/RenderReadinessSummary";
import { evaluateRenderGate } from "../components/readiness/renderGating";
import type { ProductionCapabilitiesV1 } from "../contracts/v1/production";
import { loadRenderDisabledCapabilitiesFixture } from "../fixtures/v1/fixtureManifest";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function allGatesEnabled(): ProductionCapabilitiesV1 {
  return {
    ...loadRenderDisabledCapabilitiesFixture(),
    supported_execution_modes: ["MOCK", "PLAN_ONLY", "FULL_RENDER"],
    backend_render_capability: true,
    synthetic_closure_passed: true,
    live_canary_passed: true,
    full_render_enabled: true,
    render_lock_reason_codes: [],
  };
}

describe("defensive render presentation gate", () => {
  it("requires every exact boolean gate", () => {
    const enabled = allGatesEnabled();
    expect(evaluateRenderGate(enabled).enabled).toBe(true);

    for (const field of [
      "full_render_enabled",
      "backend_render_capability",
      "synthetic_closure_passed",
      "live_canary_passed",
    ] as const) {
      expect(evaluateRenderGate({ ...enabled, [field]: false }).enabled).toBe(false);
    }
  });

  it.each([
    ["true string", { ...allGatesEnabled(), full_render_enabled: "true" }],
    ["false string", { ...allGatesEnabled(), full_render_enabled: "false" }],
    ["numeric one", { ...allGatesEnabled(), backend_render_capability: 1 }],
    [
      "missing field",
      {
        full_render_enabled: true,
        backend_render_capability: true,
        synthetic_closure_passed: true,
        render_lock_reason_codes: [],
      },
    ],
    ["null", null],
    ["array", []],
    ["malformed reasons", { ...allGatesEnabled(), render_lock_reason_codes: "LOCKED" }],
    ["non-string reason", { ...allGatesEnabled(), render_lock_reason_codes: [1] }],
    [
      "throwing property",
      Object.defineProperty({}, "full_render_enabled", {
        get() {
          throw new Error("malformed capability getter");
        },
      }),
    ],
  ])("fails closed without throwing for %s", (_label, capabilities) => {
    expect(() => evaluateRenderGate(capabilities)).not.toThrow();
    expect(evaluateRenderGate(capabilities)).toEqual({
      enabled: false,
      reasonCodes: ["CAPABILITIES_INVALID"],
    });
  });

  it("does not mutate malformed caller input", () => {
    const capabilities = {
      ...allGatesEnabled(),
      full_render_enabled: "true",
      render_lock_reason_codes: ["CALLER_REASON"],
    };
    const before = structuredClone(capabilities);

    evaluateRenderGate(capabilities);

    expect(capabilities).toEqual(before);
  });

  it("preserves and displays stable reason codes with readable labels", () => {
    const capabilities = loadRenderDisabledCapabilitiesFixture();
    render(
      <RenderReadinessSummary capabilities={capabilities} />,
    );

    for (const code of capabilities.render_lock_reason_codes) {
      expect(screen.getByText(code)).toBeInTheDocument();
    }
    expect(screen.getByText("Backend render capability is not approved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  });

  it("ignores query parameters, local storage, and route state as authority", async () => {
    localStorage.setItem("full_render_enabled", "true");
    localStorage.setItem("backend_render_capability", "true");
    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/production/new",
            search: "?full_render_enabled=true",
            state: { full_render_enabled: true, backend_render_capability: true },
          },
        ]}
      >
        <App />
      </MemoryRouter>,
    );

    await userEvent.type(
      await screen.findByLabelText("Topic content"),
      "A careful topic",
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("radio", { name: /FULL_RENDER/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  });
});
