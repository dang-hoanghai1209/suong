import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { ProductionRepository } from "../api/productionClient";
import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  AcceptStoryPlanRequestV1,
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunViewV1,
  ReplanRequestV1,
  StoryPlanReviewOperationResultV1,
  StoryPlanReviewViewV1,
  StoryPlanRevisionHistoryV1,
  StoryPlanRevisionV1,
} from "../contracts/v1/production";
import { loadPlanOnlySuccessFixture } from "../fixtures/v1/fixtureManifest";
import { mockProductionRepository } from "../mock/mockProductionRepository";
import globalCss from "../styles/global.css?inline";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const runId = "mock-plan-2026-01";

async function reviewFixture() {
  const review = await mockProductionRepository.getStoryPlanReview(runId);
  if (review === null) {
    throw new Error("Review fixture unavailable");
  }
  return structuredClone(review);
}

function plannedRun(): ProductionRunViewV1 {
  return {
    ...loadPlanOnlySuccessFixture(),
    execution_mode: "PLAN_ONLY",
    status: "PLANNED",
    current_stage: "planned",
  };
}

class ReviewRepository implements ProductionRepository {
  readonly run: ProductionRunViewV1;
  review: StoryPlanReviewViewV1;
  readonly revisions = new Map<string, StoryPlanRevisionV1>();
  readonly accept = vi.fn<
    (
      runId: string,
      request: AcceptStoryPlanRequestV1,
    ) => Promise<StoryPlanReviewOperationResultV1>
  >();
  readonly replan = vi.fn<
    (
      runId: string,
      request: ReplanRequestV1,
    ) => Promise<StoryPlanReviewOperationResultV1>
  >();
  readonly revisionLookup = vi.fn<
    (runId: string, revisionId: string) => Promise<StoryPlanRevisionV1 | null>
  >();

  constructor(run: ProductionRunViewV1, review: StoryPlanReviewViewV1) {
    this.run = run;
    this.review = review;
    this.revisions.set(review.current_revision.revision_id, review.current_revision);
    this.accept.mockImplementation(async (_runId, request) => {
      const accepted: StoryPlanReviewViewV1 = {
        ...this.review,
        review_status: "ACCEPTED_FOR_SCENE_PLANNING",
        accepted_revision_id: request.current_revision_id,
        scene_planning_accepted: true,
      };
      this.review = accepted;
      return {
        schema_version: 1,
        review: accepted,
        revision: accepted.current_revision,
        error: null,
      };
    });
    this.replan.mockImplementation(async (_runId, request) => {
      const prior = this.review.current_revision;
      const revision: StoryPlanRevisionV1 = {
        ...structuredClone(prior),
        revision_id: "revision-0002",
        revision_number: 2,
        created_at: "2026-01-15T08:00:05Z",
        reasons: [...request.feedback],
        custom_note: request.custom_note,
      };
      const updated: StoryPlanReviewViewV1 = {
        ...this.review,
        review_status: "REPLAN_REQUESTED",
        current_revision_id: revision.revision_id,
        accepted_revision_id: null,
        scene_planning_accepted: false,
        revision_history: [
          ...this.review.revision_history,
          {
            revision_id: revision.revision_id,
            revision_number: revision.revision_number,
            created_at: revision.created_at,
            reasons: revision.reasons,
            target_duration_seconds: revision.target_duration_seconds,
            beat_count: revision.beat_count,
          },
        ],
        current_revision: revision,
      };
      this.revisions.set(revision.revision_id, revision);
      this.review = updated;
      return {
        schema_version: 1,
        review: updated,
        revision,
        error: null,
      };
    });
    this.revisionLookup.mockImplementation(async (_runId, revisionId) =>
      structuredClone(this.revisions.get(revisionId) ?? null),
    );
  }

  async createPlanOnlyRun(
    _request: PlanOnlyCreateRequestV1,
  ): Promise<PlanOnlyCreateResultV1> {
    throw new Error("not used");
  }

  async getDashboard(): Promise<ProductionDashboardViewV1> {
    return { schema_version: 1, runs: [] };
  }

  async getCapabilities(): Promise<ProductionCapabilitiesV1> {
    return mockProductionRepository.getCapabilities();
  }

  async getHealth(): Promise<PlanOnlyHealthV1> {
    return mockProductionRepository.getHealth();
  }

  async getRun(requestedRunId: string) {
    return requestedRunId === this.run.run_id ? structuredClone(this.run) : null;
  }

  async getStoryPlanReview(requestedRunId: string) {
    return requestedRunId === this.run.run_id
      ? structuredClone(this.review)
      : null;
  }

  acceptStoryPlan(
    requestedRunId: string,
    request: AcceptStoryPlanRequestV1,
  ) {
    return this.accept(requestedRunId, request);
  }

  requestStoryPlanReplan(requestedRunId: string, request: ReplanRequestV1) {
    return this.replan(requestedRunId, request);
  }

  async getStoryPlanRevisions(
    requestedRunId: string,
  ): Promise<StoryPlanRevisionHistoryV1 | null> {
    return requestedRunId === this.run.run_id
      ? {
          schema_version: 1,
          run_id: requestedRunId,
          current_revision_id: this.review.current_revision_id,
          revisions: this.review.revision_history,
        }
      : null;
  }

  getStoryPlanRevision(requestedRunId: string, revisionId: string) {
    return this.revisionLookup(requestedRunId, revisionId);
  }
}

function renderReview(repository: ProductionRepository) {
  return render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={[`/production/runs/${runId}`]}>
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

async function readyRepository() {
  return new ReviewRepository(plannedRun(), await reviewFixture());
}

describe("StoryPlan review workspace", () => {
  it("renders every structured section and exact canonical projection", async () => {
    renderReview(await readyRepository());
    await screen.findByRole("heading", { name: "Production run" });

    for (const heading of [
      "Story narration",
      "Semantic beats",
      "Scenes",
      "Duration assessment",
      "Character and identity scope",
      "Planner metadata",
      "Warnings and conditions",
      "Revision history",
      "Review decision",
    ]) {
      expect(screen.getByRole("heading", { name: heading })).toBeVisible();
    }
    expect(screen.getByText("Review state:", { exact: false })).toHaveTextContent(
      "UNREVIEWED",
    );
    expect(
      screen.getAllByText("Uncertainty can feel like a closed door.", {
        exact: false,
      }).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Characters").nextElementSibling).toHaveTextContent("206");
    expect(screen.getByText("Words").nextElementSibling).toHaveTextContent("37");
    const beats = screen.getByRole("heading", { name: "Semantic beats" }).closest(
      "section",
    );
    expect(beats).not.toBeNull();
    expect(within(beats as HTMLElement).getAllByText(/^beat_/).slice(0, 8)).toHaveLength(8);
    expect(screen.getByText("StoryPlan target duration").nextElementSibling).toHaveTextContent(
      "35 s",
    );
    expect(screen.getByText("Target minimum").nextElementSibling).toHaveTextContent(
      "32 s",
    );
    expect(screen.getByText("Target maximum").nextElementSibling).toHaveTextContent(
      "38 s",
    );
    expect(screen.getByText("mock.plan_only")).toBeVisible();
    expect(screen.getByText("Render-production eligible").nextElementSibling).toHaveTextContent(
      "No",
    );
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Render video" }).every(
      (button) => button.hasAttribute("disabled"),
    )).toBe(true);
    expect(screen.queryByRole("button", { name: /Edit narration|Generate|Split|Merge/ }))
      .not.toBeInTheDocument();
  });

  it("separates blockers, warnings, information, and unknown codes", async () => {
    const repository = await readyRepository();
    const conditions = [
      {
        code: "UNKNOWN_INFORMATION_CODE",
        title: "Unknown information",
        detail: "Preserved safely.",
        severity: "INFO" as const,
        scene_ids: [],
        beat_ids: [],
      },
      {
        code: "BEAT_WARNING",
        title: "Beat warning",
        detail: "Affects one beat.",
        severity: "WARNING" as const,
        scene_ids: [],
        beat_ids: ["beat_01"],
      },
      {
        code: "BLOCKING_POLICY",
        title: "Blocking policy",
        detail: "Blocks progression.",
        severity: "BLOCKED" as const,
        scene_ids: ["scene_01"],
        beat_ids: [],
      },
    ];
    repository.review = {
      ...repository.review,
      current_revision: {
        ...repository.review.current_revision,
        warnings: {
          schema_version: 1,
          warning_count: conditions.length,
          conditions,
        },
      },
    };
    renderReview(repository);

    expect(await screen.findByText("UNKNOWN_INFORMATION_CODE")).toBeVisible();
    expect(screen.getAllByText("BEAT_WARNING").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("BLOCKING_POLICY")).toBeVisible();
    expect(screen.getByRole("link", { name: "beat_01" })).toHaveAttribute(
      "href",
      "#beat_01",
    );
    expect(screen.getByRole("link", { name: "scene_01" })).toHaveAttribute(
      "href",
      "#scene_01",
    );
  });

  it("accepts only for scene planning and prevents duplicate submission", async () => {
    const repository = await readyRepository();
    let resolveAccept:
      | ((result: StoryPlanReviewOperationResultV1) => void)
      | undefined;
    repository.accept.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveAccept = resolve;
        }),
    );
    renderReview(repository);
    const accept = await screen.findByRole("button", {
      name: "Accept for scene planning",
    });

    await userEvent.click(accept);
    await userEvent.click(accept);

    expect(repository.accept).toHaveBeenCalledTimes(1);
    expect(repository.accept).toHaveBeenCalledWith(runId, {
      schema_version: 1,
      current_revision_id: "revision-0001",
    });
    expect(screen.getByRole("button", { name: /Accepting StoryPlan/ })).toBeDisabled();
    const acceptedReview: StoryPlanReviewViewV1 = {
      ...repository.review,
      review_status: "ACCEPTED_FOR_SCENE_PLANNING",
      accepted_revision_id: "revision-0001",
      scene_planning_accepted: true,
    };
    resolveAccept?.({
      schema_version: 1,
      review: acceptedReview,
      revision: acceptedReview.current_revision,
      error: null,
    });
    expect(
      await screen.findByText("Review state:", { exact: false }),
    ).toHaveTextContent("ACCEPTED_FOR_SCENE_PLANNING");
    expect(screen.getAllByText(/does not authorize rendering/).length).toBeGreaterThan(0);
  });

  it("validates bounded feedback and creates a selectable revision", async () => {
    const repository = await readyRepository();
    renderReview(repository);
    const trigger = await screen.findByRole("button", { name: "Request replan" });
    await userEvent.click(trigger);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Narration is too short" })).toHaveFocus();

    await userEvent.click(screen.getByRole("button", { name: "Submit replan request" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Select at least one bounded feedback reason.",
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Story focus is incorrect" }),
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "Add a custom note" }));
    await userEvent.type(screen.getByLabelText("Custom note"), "Keep the focus narrow.");
    await userEvent.click(screen.getByRole("button", { name: "Submit replan request" }));

    expect(repository.replan).toHaveBeenCalledWith(runId, {
      schema_version: 1,
      base_revision_id: "revision-0001",
      feedback: ["story_focus_incorrect", "custom_note"],
      custom_note: "Keep the focus narrow.",
    });
    expect((await screen.findAllByText(/revision-0002/)).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await userEvent.click(screen.getByRole("button", { name: /Revision 1/ }));
    expect(repository.revisionLookup).toHaveBeenCalledWith(runId, "revision-0001");
    expect(
      await screen.findByRole("button", { name: "Return to current revision" }),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Return to current revision" }),
    );
    expect(screen.getByText("Selected revision").nextElementSibling).toHaveTextContent(
      "revision-0002",
    );
  });

  it.each(["BLOCKED", "FAILED"] as const)(
    "disables acceptance and replan for %s runs",
    async (status) => {
      const repository = new ReviewRepository(
        { ...plannedRun(), status },
        await reviewFixture(),
      );
      renderReview(repository);

      expect(
        await screen.findByRole("button", { name: "Accept for scene planning" }),
      ).toBeDisabled();
      expect(screen.getByRole("button", { name: "Request replan" })).toBeDisabled();
      expect(screen.getByText(/cannot be accepted or replanned/)).toBeVisible();
    },
  );

  it("copies exact narration and handles clipboard failure without crashing", async () => {
    const repository = await readyRepository();
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) };
    vi.stubGlobal("navigator", Object.assign(Object.create(navigator), { clipboard }));
    renderReview(repository);
    await userEvent.click(
      await screen.findByRole("button", { name: "Copy narration" }),
    );
    expect(clipboard.writeText).toHaveBeenCalledWith(
      repository.review.current_revision.story_plan.narration_text,
    );
    expect(screen.getByText("Canonical narration copied.")).toBeVisible();

    cleanup();
    clipboard.writeText.mockRejectedValueOnce(new Error("denied"));
    renderReview(repository);
    await userEvent.click(
      await screen.findByRole("button", { name: "Copy narration" }),
    );
    expect(screen.getByText("Narration could not be copied.")).toBeVisible();
  });

  it("fails closed for stale operations and malformed review loading", async () => {
    const repository = await readyRepository();
    repository.replan.mockResolvedValueOnce({
      schema_version: 1,
      review: null,
      revision: null,
      error: {
        schema_version: 1,
        request_id: "request-stale-revision",
        status: "BLOCKED",
        code: "STALE_REVISION",
        message: "The StoryPlan revision is stale.",
        retryable: false,
        field_errors: [],
        details: {},
      },
    });
    renderReview(repository);
    await userEvent.click(
      await screen.findByRole("button", { name: "Request replan" }),
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Story focus is incorrect" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Submit replan request" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "STALE_REVISION: The StoryPlan revision is stale.",
    );
    expect(screen.getByRole("dialog")).toBeVisible();

    cleanup();
    const unavailableRepository = await readyRepository();
    vi.spyOn(unavailableRepository, "getStoryPlanReview").mockRejectedValue(
      new Error("malformed"),
    );
    renderReview(unavailableRepository);
    expect(
      await screen.findByRole("heading", { name: "StoryPlan review unavailable" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Accept for scene planning" }),
    ).toBeDisabled();
  });

  it("keeps diagnostics, keyboard controls, and responsive containment explicit", async () => {
    renderReview(await readyRepository());
    const diagnostics = await screen.findByText("Diagnostics");
    await userEvent.click(diagnostics);
    expect(screen.getAllByText("Not granted")).toHaveLength(2);
    expect(
      document.querySelectorAll(
        "[tabindex]:not([tabindex='0']):not([tabindex='-1'])",
      ),
    ).toHaveLength(0);
    expect(globalCss).toContain(".story-review-layout");
    expect(globalCss).toContain(".review-dialog");
    expect(globalCss).toContain("grid-template-columns: 1fr");
    expect(globalCss).toContain("overflow-wrap: anywhere");
  });
});
