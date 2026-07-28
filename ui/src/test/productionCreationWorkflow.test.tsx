import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { ProductionRepository } from "../api/productionClient";
import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunViewV1,
} from "../contracts/v1/production";
import {
  CONTENT_MAX_CHARACTERS,
  FIXED_ASPECT_RATIO,
  FIXED_TARGET_DURATION_SECONDS,
  initialProductionCreationDraft,
  mapDraftToPlanOnlyRequest,
  validateCreationDraft,
  type ProductionCreationDraft,
} from "../productionCreation/creationDraft";
import globalCss from "../styles/global.css?inline";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

class WorkflowRepository implements ProductionRepository {
  readonly create = vi.fn<
    (request: PlanOnlyCreateRequestV1) => Promise<PlanOnlyCreateResultV1>
  >();

  createPlanOnlyRun(request: PlanOnlyCreateRequestV1) {
    return this.create(request);
  }

  async getDashboard(): Promise<ProductionDashboardViewV1> {
    return { schema_version: 1, runs: [] };
  }

  async getCapabilities(): Promise<ProductionCapabilitiesV1> {
    return {
      schema_version: 1,
      supported_execution_modes: ["PLAN_ONLY"],
      supported_input_modes: ["TOPIC"],
      supported_languages: ["en", "vi"],
      supported_aspect_ratios: ["9:16"],
      supported_visual_modes: ["illustrated_scene"],
      supported_character_scopes: [
        "recurring_female",
        "female_with_anonymous_background",
      ],
      backend_render_capability: false,
      synthetic_closure_passed: false,
      live_canary_passed: false,
      full_render_enabled: false,
      render_lock_reason_codes: ["PLAN_ONLY_NO_RENDER_AUTHORITY"],
    };
  }

  async getHealth(): Promise<PlanOnlyHealthV1> {
    return {
      schema_version: 1,
      status: "ok",
      contract_version: "v1",
      plan_only_available: true,
      render_available: false,
    };
  }

  async getRun(_runId: string): Promise<ProductionRunViewV1 | null> {
    return null;
  }
}

function renderWorkflow(repository = new WorkflowRepository()) {
  render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={["/production/new"]}>
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
  return repository;
}

async function enterTopic(content = "A careful topic") {
  fireEvent.change(await screen.findByLabelText("Topic content"), {
    target: { value: content },
  });
}

async function continueStep() {
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
}

async function reachReview(content = "A careful topic") {
  await enterTopic(content);
  await continueStep();
  await continueStep();
  await continueStep();
  await screen.findByRole("heading", { name: "Review StoryPlan request" });
}

describe("authoritative creation draft", () => {
  it("maps exact supported values without rewriting source content", () => {
    const sourceContent = "  First paragraph.\n\nSecond paragraph.  ";
    const draft: ProductionCreationDraft = {
      ...initialProductionCreationDraft,
      sourceContent,
      language: "vi",
      requestedSceneCount: 7,
      characterScope: "female_with_anonymous_background",
    };

    expect(mapDraftToPlanOnlyRequest(draft)).toEqual({
      schema_version: 1,
      input_mode: "TOPIC",
      source_content: sourceContent,
      language: "vi",
      character_scope: "female_with_anonymous_background",
      requested_scene_count: 7,
    });
    expect(Object.keys(mapDraftToPlanOnlyRequest(draft))).not.toContain(
      "target_duration_seconds",
    );
    expect(FIXED_TARGET_DURATION_SECONDS).toBe(35);
    expect(FIXED_ASPECT_RATIO).toBe("9:16");
  });

  it("enforces content and enum bounds before mapping", () => {
    expect(
      validateCreationDraft({
        ...initialProductionCreationDraft,
        sourceContent: " ",
      }).map((issue) => issue.field),
    ).toContain("sourceContent");
    expect(
      validateCreationDraft({
        ...initialProductionCreationDraft,
        sourceContent: "x".repeat(CONTENT_MAX_CHARACTERS),
      }),
    ).toEqual([]);
    expect(
      validateCreationDraft({
        ...initialProductionCreationDraft,
        sourceContent: "x".repeat(CONTENT_MAX_CHARACTERS + 1),
      }).map((issue) => issue.field),
    ).toContain("sourceContent");

    const unsupported = {
      ...initialProductionCreationDraft,
      sourceContent: "Topic",
      inputMode: "NARRATION",
      characterScope: "family",
    } as unknown as ProductionCreationDraft;
    expect(validateCreationDraft(unsupported).map((issue) => issue.field)).toEqual([
      "inputMode",
      "characterScope",
    ]);
    expect(() => mapDraftToPlanOnlyRequest(unsupported)).toThrow();
  });
});

describe("five-step PLAN_ONLY workflow", () => {
  it("shows all steps, blocks invalid forward progress, and focuses the field", async () => {
    renderWorkflow();

    const stepNavigation = screen.getByRole("navigation", {
      name: "StoryPlan creation steps",
    });
    for (const label of [
      "Content",
      "Production profile",
      "Character scope",
      "Review",
      "Create StoryPlan",
    ]) {
      expect(within(stepNavigation).getByText(label)).toBeVisible();
    }
    expect(within(stepNavigation).getByText("Content").parentElement).toHaveAttribute(
      "aria-current",
      "step",
    );

    await continueStep();

    const content = screen.getByLabelText("Topic content");
    expect(content).toHaveFocus();
    expect(content).toHaveAttribute("aria-invalid", "true");
    expect(content).toHaveAccessibleDescription(/Enter a topic before continuing/);
    expect(screen.getByText("Step 1 of 5: Content")).toBeVisible();
  });

  it("preserves values across backward and completed-step navigation", async () => {
    renderWorkflow();
    await enterTopic("Women building a calm morning ritual");
    await continueStep();

    await userEvent.selectOptions(screen.getByLabelText("Language"), "vi");
    await userEvent.selectOptions(screen.getByLabelText("StoryPlan scenes"), "7");
    await continueStep();
    await userEvent.click(
      screen.getByRole("radio", {
        name: /Recurring woman with anonymous background people/,
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(screen.getByLabelText("Language")).toHaveValue("vi");
    expect(screen.getByLabelText("StoryPlan scenes")).toHaveValue("7");
    await userEvent.click(screen.getByRole("button", { name: /Content Completed/ }));
    expect(screen.getByLabelText("Topic content")).toHaveValue(
      "Women building a calm morning ritual",
    );
    expect(
      within(screen.getByRole("group", { name: "Input mode" })).getAllByRole("radio"),
    ).toHaveLength(1);
  });

  it("exposes only contract fields and safe character scopes", async () => {
    renderWorkflow();
    await enterTopic();
    await continueStep();

    expect(screen.getByLabelText("Language")).toBeVisible();
    expect(screen.getByLabelText("StoryPlan scenes")).toBeVisible();
    expect(screen.getByText("35 seconds — planned target")).toBeVisible();
    expect(screen.getByText("9:16 — portrait")).toBeVisible();
    expect(screen.getByRole("radio", { name: /PLAN_ONLY/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /FULL_RENDER/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /FULL_RENDER/ })).toHaveAccessibleDescription(
      "Disabled because this workflow has no render authority.",
    );

    await continueStep();
    expect(
      screen.getByRole("radio", { name: /One recurring woman/ }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", {
        name: /Recurring woman with anonymous background people/,
      }),
    ).toBeEnabled();
    expect(screen.queryByText(/recurring male|family|unresolved/i)).not.toBeInTheDocument();
  });

  it("reviews exact values and preserves paragraphs before explicit submission", async () => {
    const source = "  First paragraph.\n\nSecond paragraph.  ";
    renderWorkflow();
    await enterTopic(source);
    await continueStep();
    await userEvent.selectOptions(screen.getByLabelText("Language"), "vi");
    await userEvent.selectOptions(screen.getByLabelText("StoryPlan scenes"), "7");
    await continueStep();
    await userEvent.click(
      screen.getByRole("radio", {
        name: /Recurring woman with anonymous background people/,
      }),
    );
    await continueStep();

    const review = screen.getByRole("heading", {
      name: "Review StoryPlan request",
    }).closest("section");
    expect(review).not.toBeNull();
    const reviewedContent = (review as HTMLElement).querySelector(".review-content");
    expect(reviewedContent).not.toBeNull();
    expect(reviewedContent).toHaveTextContent(source, { normalizeWhitespace: false });
    expect(within(review as HTMLElement).getByText("Vietnamese")).toBeVisible();
    expect(within(review as HTMLElement).getByText("7")).toBeVisible();
    expect(within(review as HTMLElement).getAllByText("Not available — omitted")).toHaveLength(
      2,
    );
    expect(within(review as HTMLElement).getByText("PLAN_ONLY")).toBeVisible();
    expect(within(review as HTMLElement).getByText(/StoryPlan only/)).toBeVisible();
    expect(within(review as HTMLElement).getByText(/No narration, images/)).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Edit content" }));
    expect(screen.getByLabelText("Topic content")).toHaveValue(source);
  });

  it("submits once with the reviewed payload and announces pending creation", async () => {
    const repository = renderWorkflow();
    let resolveCreate: ((result: PlanOnlyCreateResultV1) => void) | undefined;
    repository.create.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );
    await reachReview("Exact submitted topic");

    const form = screen.getByRole("button", { name: "Create StoryPlan" }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);
    fireEvent.submit(form as HTMLFormElement);

    expect(repository.create).toHaveBeenCalledTimes(1);
    expect(repository.create).toHaveBeenCalledWith({
      schema_version: 1,
      input_mode: "TOPIC",
      source_content: "Exact submitted topic",
      language: "en",
      character_scope: "recurring_female",
      requested_scene_count: 8,
    });
    expect(screen.getByText(/Planning…/).closest('[role="status"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "StoryPlan creation already in progress",
    );
    resolveCreate?.({
      schema_version: 1,
      run: null,
      error: {
        schema_version: 1,
        request_id: "request-0123456789abcdef",
        status: "FAILED",
        code: "PLANNING_FAILED",
        message: "Planning did not complete.",
        retryable: false,
        field_errors: [],
        details: {},
      },
    });
  });

  it("preserves the in-memory draft after a backend error", async () => {
    const repository = renderWorkflow();
    repository.create.mockResolvedValue({
      schema_version: 1,
      run: null,
      error: {
        schema_version: 1,
        request_id: "request-0123456789abcdef",
        status: "BLOCKED",
        code: "UNSUPPORTED_IDENTITY_POLICY",
        message: "The selected scope is blocked.",
        retryable: false,
        field_errors: [],
        details: {},
      },
    });
    const source = "Topic retained after failure";
    await reachReview(source);
    await userEvent.click(screen.getByRole("button", { name: "Create StoryPlan" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The selected scope is blocked.",
    );
    expect(screen.getByText(source)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Review StoryPlan request" })).toBeVisible();
  });

  it("protects only meaningful drafts from browser leave", async () => {
    renderWorkflow();
    const emptyEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(emptyEvent);
    expect(emptyEvent.defaultPrevented).toBe(false);

    await enterTopic("Unsaved topic");
    const changedEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(changedEvent);
    expect(changedEvent.defaultPrevented).toBe(true);
  });

  it("keeps accessibility and responsive containment explicit", async () => {
    renderWorkflow();

    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(document.querySelectorAll("[tabindex]:not([tabindex='0']):not([tabindex='-1'])")).toHaveLength(
      0,
    );
    expect(screen.getByRole("group", { name: "Content" })).toBeVisible();
    expect(screen.getByRole("group", { name: "Input mode" })).toBeVisible();
    expect(globalCss).toContain("@media (max-width: 1023px)");
    expect(globalCss).toContain("@media (max-width: 767px)");
    expect(globalCss).toContain(".creation-workspace");
    expect(globalCss).toContain("grid-template-columns: 1fr");
    expect(globalCss).toContain("overflow-wrap: anywhere");
  });
});
