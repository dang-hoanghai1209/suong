import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { ProductionRepository } from "../api/productionClient";
import { validateExecutionEnablementAccess } from "../api/executionEnablementValidation";
import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  ExecutionEnablementAccessV1,
  ExecutionPackageCapabilityLocksV1,
  ExecutionPackageV1,
} from "../contracts/v1/production";
import { MockProductionRepository } from "../mock/mockProductionRepository";

const runId = "plan-0123456789abcdef0123";
const locks: ExecutionPackageCapabilityLocksV1 = {
  full_render_enabled: false,
  narration_generation_capability: false,
  tts_capability: false,
  audio_generation_capability: false,
  audio_measurement_capability: false,
  timeline_execution_authority: false,
  renderer_execution_authority: false,
  render_authority: false,
  video_render_authority: false,
  subtitle_generation_capability: false,
  media_muxing_capability: false,
  output_creation_capability: false,
  execution_job_creation_capability: false,
  final_media_capability: false,
};

function packageValue(
  overrides: Partial<ExecutionPackageV1> = {},
): ExecutionPackageV1 {
  const fingerprint = "a".repeat(64);
  return {
    ...locks,
    schema_version: 1,
    package_id: "execution-package-0001",
    package_revision_id: "execution-package-revision-0001",
    package_revision_number: 1,
    run_id: runId,
    status: "SEALED_FOR_NARRATION_STAGE_REVIEW",
    current: true,
    execution_package_created: true,
    eligible_for_narration_stage_review: true,
    package_source_authority_sha256: fingerprint,
    authority: {
      schema_version: 1,
      readiness_report_id: "readiness-report-0001",
      readiness_report_revision_id: "readiness-report-revision-0001",
      readiness_source_authority_sha256: "b".repeat(64),
      readiness_approval_id: "readiness-approval-0001",
      readiness_approval_purpose: "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
      story_plan_revision_id: "revision-0001",
      accepted_story_plan_revision_id: "revision-0001",
      narration_source_sha256: "c".repeat(64),
      scene_collection_revision_id: "scene-collection-revision-0001",
      ordered_scene_ids: ["scene_01"],
      ordered_scene_revision_ids: ["scene-revision-0001"],
      visual_collection_revision_ids: ["visual-collection-revision-0001"],
      accepted_candidate_ids: ["visual-candidate-0001"],
      accepted_candidate_revision_ids: ["visual-candidate-revision-0001"],
      accepted_candidate_artifact_sha256s: ["d".repeat(64)],
      accepted_candidate_mimes: ["image/png"],
      accepted_candidate_dimensions: [[576, 1024]],
      composition_collection_revision_id: "composition-collection-revision-0001",
      composition_ids: ["composition-0001"],
      composition_revision_ids: ["composition-revision-0001"],
      timeline_collection_revision_id: "timeline-collection-revision-0001",
      accepted_timeline_revision_id: "timeline-revision-0001",
      timeline_source_authority_sha256: "e".repeat(64),
      effective_timeline_duration_ms: 32_000,
    },
    execution_enablement_contract_version: "execution_enablement_v1",
    review_history: [{
      schema_version: 1,
      review_id: "execution-package-review-0001",
      package_revision_id: "execution-package-revision-0001",
      package_source_authority_sha256: fingerprint,
      action: "PACKAGE_CREATED",
      reason_code: null,
      note: null,
      created_at: "2026-01-02T00:00:01Z",
    }],
    superseded_reason: null,
    created_at: "2026-01-02T00:00:01Z",
    process_local: true,
    ...overrides,
  };
}

function access(pkg: ExecutionPackageV1 | null = null): ExecutionEnablementAccessV1 {
  return {
    ...locks,
    schema_version: 1,
    run_id: runId,
    execution_package_creation_authorized: pkg === null,
    blocker_codes: [],
    current_readiness_report_id: "readiness-report-0001",
    current_readiness_report_revision_id: "readiness-report-revision-0001",
    current_readiness_source_authority_sha256: "b".repeat(64),
    current_readiness_approval_id: "readiness-approval-0001",
    package: pkg,
    package_history_count: pkg === null ? 0 : 1,
    execution_package_created: pkg !== null,
    eligible_for_narration_stage_review:
      pkg?.eligible_for_narration_stage_review ?? false,
    process_local: true,
  };
}

function repositoryWith(overrides: Partial<ProductionRepository>): ProductionRepository {
  return Object.assign(new MockProductionRepository(), overrides);
}
function renderWorkspace(repository: ProductionRepository) {
  return render(
    <MemoryRouter initialEntries={[`/production/runs/${runId}/execution-enablement`]}>
      <ProductionRepositoryProvider repository={repository}>
        <App />
      </ProductionRepositoryProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("execution-enablement workspace", () => {
  it("shows locked authority without operational controls", async () => {
    renderWorkspace(repositoryWith({
      getExecutionEnablementAccess: vi.fn().mockResolvedValue({
        ...access(null),
        execution_package_creation_authorized: false,
        blocker_codes: ["READINESS_APPROVAL_UNAVAILABLE"],
      }),
      getExecutionPackageHistory: vi.fn().mockResolvedValue(null),
    }));
    expect(await screen.findByText("READINESS_APPROVAL_UNAVAILABLE")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Execution enablement" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Review package creation" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /render|tts|audio|export/i })).not.toBeInTheDocument();
  });

  it("requires explicit confirmation and submits only readiness identities", async () => {
    const pkg = packageValue();
    const create = vi.fn().mockResolvedValue({
      schema_version: 1, access: null, package: pkg, error: null,
    });
    const getAccess = vi.fn()
      .mockResolvedValueOnce(access(null))
      .mockResolvedValue(access(pkg));
    renderWorkspace(repositoryWith({
      getExecutionEnablementAccess: getAccess,
      getExecutionPackageHistory: vi.fn().mockResolvedValue({
        schema_version: 1, run_id: runId, packages: [pkg],
      }),
      createExecutionPackage: create,
    }));
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Review package creation" }));
    expect(screen.getByRole("button", { name: "Create sealed execution package" })).toBeDisabled();
    await user.click(screen.getByLabelText(/explicitly confirm/i));
    await user.click(screen.getByRole("button", { name: "Create sealed execution package" }));
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    expect(create).toHaveBeenCalledWith(runId, {
      schema_version: 1,
      readiness_report_revision_id: "readiness-report-revision-0001",
      readiness_source_authority_sha256: "b".repeat(64),
      readiness_approval_id: "readiness-approval-0001",
      explicit_confirmation: true,
      note: null,
    });
    expect(await screen.findByText("Eligible for separate narration-stage review")).toBeVisible();
  });

  it("displays authority, capability locks, process-local warning, and history", async () => {
    const pkg = packageValue();
    renderWorkspace(repositoryWith({
      getExecutionEnablementAccess: vi.fn().mockResolvedValue(access(pkg)),
      getExecutionPackageHistory: vi.fn().mockResolvedValue({
        schema_version: 1, run_id: runId, packages: [pkg],
      }),
    }));
    expect(await screen.findByText(pkg.package_source_authority_sha256)).toBeVisible();
    expect(screen.getByText(/Host restart resets this process-local package state/)).toBeVisible();
    expect(screen.getByText("TTS: not granted")).toBeVisible();
    expect(screen.getAllByText(/execution-package-revision-0001/).length).toBeGreaterThan(0);
  });

  it("fails closed on forged capability, malformed payload, and backend outage", async () => {
    const forged = structuredClone(access(packageValue())) as unknown as Record<string, unknown>;
    forged.tts_capability = true;
    expect(() => validateExecutionEnablementAccess(forged)).toThrow();
    const malformed = structuredClone(access(packageValue())) as unknown as Record<string, unknown>;
    malformed.output_path = "forged";
    expect(() => validateExecutionEnablementAccess(malformed)).toThrow();
    renderWorkspace(repositoryWith({
      getExecutionEnablementAccess: vi.fn().mockRejectedValue(new Error("offline")),
    }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Execution-package review ended without a safe response.",
    );
  });
});
