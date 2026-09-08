import { beforeEach, describe, expect, it, vi } from "vitest";
import { getBrokerStatus, getEvidence, getGoalLoop, getPlatformAnalytics, getProjectDesk, getProjects, getReviewMaterial, getReviews, getSetupStatus, getWebsiteAnalytics, startGoalLoop, submitProjectBrief, submitReviewAction } from "./client";
import { demoAdapter, demoReviews } from "../fixtures/demoData";
import type { ReviewActionRequest } from "../types";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Review API client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("accepts a verified GA4 zero and rejects a mismatched project", async () => {
    const verified = {
      project_id: "alpha", status: "verified", source: "ga4", report: {
        fetched_at: "2026-09-07T10:00:00Z", report_sha256: "a".repeat(64),
        window: { start_date: "2026-08-10", end_date: "2026-09-05", timezone: "UTC", excluded_latest_days: 2 },
        metrics: { active_users: 4, engaged_sessions: 2, download_clicks: 0 },
      },
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(verified));
    await expect(getWebsiteAnalytics("alpha")).resolves.toMatchObject({report: {metrics: {download_clicks: 0}}});
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({...verified, project_id: "beta"}));
    await expect(getWebsiteAnalytics("alpha")).rejects.toThrow("Invalid website analytics report");
  });

  it("accepts known GA4 setup states and rejects invented ones", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({
      project_id: "alpha", status: "not_connected", source: "ga4", report: null,
      setup: { state: "authentication_required" },
    }));
    await expect(getWebsiteAnalytics("alpha")).resolves.toMatchObject({setup: {state: "authentication_required"}});
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({
      project_id: "alpha", status: "not_connected", source: "ga4", report: null,
      setup: { state: "connected_somehow" },
    }));
    await expect(getWebsiteAnalytics("alpha")).rejects.toThrow("Invalid website analytics report");
  });

  it("accepts bounded setup states and rejects invented remote access", async () => {
    const valid = {
      desk: "running", background: "active", autostart: { state: "not_installed" },
      remote_access: { state: "local_only", provider: "tailscale-serve" },
      publication: { mode: "review", publisher: "disabled" }, host: "must_be_awake",
    } as const;
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(valid));
    await expect(getSetupStatus()).resolves.toEqual(valid);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({
      ...valid, remote_access: { ...valid.remote_access, state: "public" },
    }));
    await expect(getSetupStatus()).rejects.toThrow("Invalid setup status");
  });

  it("accepts safe publisher status details and rejects impossible task timing", async () => {
    const valid = { project_id: "alpha", state: "connected", counts: { failed: 1 }, tasks: [{
      task_id: "publisher-1", agent_id: "publisher", status: "failed",
      attempt: 1, max_attempts: 1, not_before: 0, created_at: 10, updated_at: 11,
    }], truncated: false } as const;
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(valid));
    await expect(getBrokerStatus("alpha")).resolves.toEqual(valid);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({
      ...valid, tasks: [{ ...valid.tasks[0], updated_at: 9 }],
    }));
    await expect(getBrokerStatus("alpha")).rejects.toThrow("Invalid broker status");
  });

  it("keeps native platform metrics separate and rejects a mismatched project", async () => {
    const verified = {project_id: "alpha", status: "verified", source: "native_platform_analytics", report: {
      fetched_at: "2026-09-07T10:00:00Z", report_sha256: "b".repeat(64), platforms: [{
        platform: "youtube", window: {start_date: "2026-09-01", end_date: "2026-09-06", timezone: "UTC"},
        metrics: [{metric_id: "views", label: "Views", value: 0, unit: "count"}],
      }],
    }};
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(verified));
    await expect(getPlatformAnalytics("alpha")).resolves.toMatchObject({report: {platforms: [{platform: "youtube"}]}});
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({...verified, project_id: "beta"}));
    await expect(getPlatformAnalytics("alpha")).rejects.toThrow("Invalid platform analytics report");
  });

  it("loads and normalizes project and review envelopes", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          projects: [
            { project_id: "alpha", display_name: "Alpha", project_profile_revision: "alpha-profile-v1" },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          project_id: "alpha",
          project_profile_revision: "alpha-profile-v1",
          reviews: [
            {
              review_item_version: "1.0",
              project_id: "alpha",
              project_profile_revision: "alpha-profile-v1",
              artifact_id: "launch-brief",
              artifact_revision: "r1",
              artifact_sha256: "a".repeat(64),
              artifact_content: "Exact final product.\nSecond line.",
              title: "Launch brief",
              type: "content-package",
              status: "pending",
              preview: "Approve the launch brief.",
              evidence: [{ label: "Source", ref: "evidence/source.json", sha256: "c".repeat(64) }],
              quality_checks: [{ name: "claims", status: "pass", detail: "3/3 cited" }],
              revision: {
                id: "r1",
                sha256: "a".repeat(64),
                artifact_path: "outbox/artifacts/launch-brief/r1.md",
              },
              action: null,
            },
          ],
        }),
      );

    await expect(getProjects()).resolves.toEqual([
      { id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" },
    ]);
    await expect(getReviews("alpha")).resolves.toMatchObject([
      {
        id: "launch-brief",
        revision: "r1",
        artifactPath: "outbox/artifacts/launch-brief/r1.md",
        artifactContent: "Exact final product.\nSecond line.",
        decision: "Approve the launch brief.",
        evidence: [{ id: "evidence/source.json", label: "Source", ref: "evidence/source.json", sha256: "c".repeat(64) }],
      },
    ]);
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/reviews?project_id=alpha", {
      headers: { Accept: "application/json" },
    });
  });

  it.each(["valid", "wrong_project", "wrong_revision", "wrong_hash", "wrong_status"])("binds recorded revision notes to the exact review: %s", async (variant) => {
    const action = {
      action: "note", project_id: variant === "wrong_project" ? "beta" : "alpha", project_profile_revision: "alpha-v1",
      artifact_id: "lesson", artifact_revision: variant === "wrong_revision" ? "r2" : "r1",
      artifact_sha256: (variant === "wrong_hash" ? "b" : "a").repeat(64),
      note: "Keep the source limitations.", reason: null,
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({project_id:"alpha", project_profile_revision:"alpha-v1", reviews:[{
      review_item_version:"1.0", project_id:"alpha", project_profile_revision:"alpha-v1", artifact_id:"lesson", artifact_revision:"r1",
      artifact_sha256:"a".repeat(64), artifact_content:"Exact lesson.", title:"A lesson", type:"lesson",
      status:variant === "wrong_status" ? "approved" : "rework_requested", preview:"Review this lesson.", evidence:[], quality_checks:[],
      revision:{id:"r1",sha256:"a".repeat(64),artifact_path:"lesson/r1.md"}, action,
    }]}));
    if (variant === "valid") await expect(getReviews("alpha")).resolves.toMatchObject([{reviewNote:"Keep the source limitations."}]);
    else await expect(getReviews("alpha")).rejects.toThrow("Review decision identity mismatch");
  });

  it("loads the customer-safe project start read model without technical review fields", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        project_id: "alpha",
        project_profile_revision: "alpha-profile-v1",
        display_name: "Alpha",
        phase: "Research in progress",
        foundation: [{ id: "facts", title: "Product facts", status: "reference", status_label: "Candidates — not approved", summary: "Verified reference only.", documents: [{ label: "Candidates", markdown: "No approved claim." }] }],
        goal: { title: "Choose the next route", detail: "One bounded decision." },
        current_work: { title: "Check one source", detail: "Finish the current bounded evidence check." },
        audience: { label: "Product operators", detail: "People making a planning decision." },
        success_signal: { label: "Decision-ready brief", detail: "A reviewed, bounded next step." },
        baseline: { status: "not_measured", detail: "No baseline exists yet; it is not estimated." },
        horizon: { label: "Research cycle", detail: "Review after the source set is complete." },
        resources: { detail: "One researcher and limited analytics support are available." },
        do_nothing_option: { detail: "Keep the current plan if the evidence does not support a route." },
        non_goals: ["No publication from research."],
        readiness: {
          completed: 1,
          total: 3,
          steps: [
            { id: "goal", label: "Project goal", status: "complete", detail: "Goal recorded." },
            { id: "research", label: "Research", status: "active", detail: "Evidence collection." },
            { id: "route", label: "Route", status: "waiting", detail: "Choose after evidence." },
          ],
        },
        blockers: [{ title: "Baseline missing", detail: "No measurement starting point.", owner_role: "Analytics lead" }],
        next_roles: [
          { role: "evidence", label: "Evidence researcher", focus: "Collect sources.", status: "active" },
          { role: "growth", label: "Growth coordinator", focus: "Review the completed brief.", status: "complete" },
        ],
        active_research: {
          title: "Audience evidence brief",
          stage: "Evidence collection",
          progress_label: "1 of 3 ready",
          detail: "The active question is bounded.",
          next_step: "Check evidence coverage.",
        },
        authority: {
          publish_mode: "review",
          publish_configuration: "configured",
          automatic_publish: false,
          human_review_required: true,
          ready_to_queue_is_not_go_live: true,
        },
      }),
    );

    const desk = await getProjectDesk({ id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" });
    expect(desk).toMatchObject({
      projectId: "alpha",
      phase: "Research in progress",
      foundation: [{ id: "facts", documents: [{ label: "Candidates", markdown: "No approved claim." }] }],
      audience: { label: "Product operators" },
      goal: { title: "Choose the next route" },
      currentWork: { title: "Check one source" },
      readiness: { completed: 1, total: 3 },
      activeResearch: { nextStep: "Check evidence coverage." },
      baseline: { status: "not_measured" },
      source: "api",
    });
    expect(desk.readiness.steps.map((step) => step.status)).toEqual(["complete", "active", "waiting"]);
    expect(desk.nextRoles.at(-1)?.status).toBe("complete");
    expect(fetch).toHaveBeenCalledWith("/api/project-desk?project_id=alpha", {
      headers: { Accept: "application/json" },
    });
  });

  it("uses a visibly labeled local setup outline only when an older local API lacks the endpoint", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ error: { message: "Not found" } }, 404));

    await expect(
      getProjectDesk({ id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" }),
    ).resolves.toMatchObject({
      projectId: "alpha",
      source: "local_setup",
      phase: "Project setup",
    });
  });

  it("posts the complete project-start brief and returns only the customer-safe desk", async () => {
    const project = { id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" };
    const brief = {
      goal: { title: "Choose a route", detail: "Choose the next route from bounded research evidence." },
      audience: { label: "Product operators", detail: "People deciding which project to prioritize next." },
      successSignal: { label: "Decision-ready brief", detail: "A reviewed next step is ready to hand off." },
      baseline: { status: "not_measured" as const, detail: "No baseline exists yet; it is not estimated." },
      horizon: { label: "Research cycle", detail: "Review after the source set is complete." },
      resources: { detail: "One researcher and limited analytics support are available." },
      doNothingOption: { detail: "Keep the current plan if the evidence does not support a route." },
      nonGoals: ["No publication from research."],
      researchQuestion: "Which evidence should guide the route decision?",
    };
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        brief: {
          project_id: "alpha",
          project_profile_revision: "alpha-profile-v1",
          id: "brief-1",
          revision: "1",
        },
        created: true,
        desk: {
          project_id: "alpha",
          project_profile_revision: "alpha-profile-v1",
          display_name: "Alpha",
          phase: "Research ready",
          goal: brief.goal,
          audience: brief.audience,
          success_signal: brief.successSignal,
          baseline: brief.baseline,
          horizon: brief.horizon,
          resources: brief.resources,
          do_nothing_option: brief.doNothingOption,
          non_goals: brief.nonGoals,
          readiness: {
            completed: 1,
            total: 5,
            steps: [{ id: "brief", label: "Project brief", status: "complete", detail: "Saved." }],
          },
          blockers: [{ title: "Baseline not measured", detail: brief.baseline.detail, owner_role: "Analytics" }],
          next_roles: [{ role: "evidence", label: "Evidence researcher", focus: "Plan sources.", status: "active" }],
          active_research: {
            title: "Research brief",
            stage: "Research plan",
            progress_label: "1 of 5 ready",
            detail: brief.researchQuestion,
            next_step: "Set the source plan.",
          },
          authority: {
            publish_mode: "review",
            publish_configuration: "configured",
            automatic_publish: false,
            human_review_required: true,
            ready_to_queue_is_not_go_live: true,
          },
        },
      }),
    );

    await expect(submitProjectBrief(project, brief)).resolves.toMatchObject({
      projectId: "alpha",
      phase: "Research ready",
      baseline: { status: "not_measured" },
      source: "api",
    });
    expect(fetch).toHaveBeenCalledWith("/api/project-briefs", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: "alpha",
        project_profile_revision: "alpha-profile-v1",
        goal: brief.goal,
        audience: brief.audience,
        success_signal: brief.successSignal,
        baseline: brief.baseline,
        horizon: brief.horizon,
        resources: brief.resources,
        do_nothing_option: brief.doNothingOption,
        non_goals: brief.nonGoals,
        research_question: brief.researchQuestion,
      }),
    });
  });

  it("reads and starts only the narrow local check contract", async () => {
    const project = { id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" };
    const queued = {
      project_id: "alpha",
      project_profile_revision: "alpha-profile-v1",
      job: {
        id: "GOAL-alpha",
        status: "queued",
        request_recorded: true,
        result: null,
      },
    };
    const completed = {
      project_id: "alpha",
      project_profile_revision: "alpha-profile-v1",
      job: {
        id: "GOAL-alpha",
        status: "completed",
        request_recorded: true,
        result: {
          status: "completed",
          outcome: "needs_input",
          title: "Local starting check complete",
          summary: "No web research, model call, or public action was performed.",
          next_step: "Choose a research question.",
          insights: [{ label: "External evidence", state: "not_run", detail: "No source was collected." }],
          sources: [{ label: "Operator project brief", state: "recorded" }],
          limits: ["This is planning context only."],
        },
      },
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(queued, 202))
      .mockResolvedValueOnce(jsonResponse(completed));

    await expect(startGoalLoop(project)).resolves.toEqual({
      id: "GOAL-alpha",
      status: "queued",
      requestRecorded: true,
      result: null,
    });
    await expect(getGoalLoop(project)).resolves.toMatchObject({
      id: "GOAL-alpha",
      status: "completed",
      result: {
        title: "Local starting check complete",
        nextStep: "Choose a research question.",
        insights: [{ label: "External evidence", state: "not_run" }],
      },
    });
    expect(fetch).toHaveBeenNthCalledWith(1, "/api/goal-loop", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: "alpha", project_profile_revision: "alpha-profile-v1" }),
    });
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/goal-loop?project_id=alpha", {
      headers: { Accept: "application/json" },
    });
  });

  it("rejects a local check result with mismatched project identity", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        project_id: "other",
        project_profile_revision: "alpha-profile-v1",
        job: { id: null, status: "not_started", request_recorded: false, result: null },
      }),
    );

    await expect(
      getGoalLoop({ id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" }),
    ).rejects.toThrow("invalid result");
  });

  it("loads only the exact evidence project, ref, and SHA identity", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        project_id: "alpha",
        project_profile_revision: "alpha-profile-v1",
        evidence_ref: "evidence/source packet.txt",
        evidence_sha256: "c".repeat(64),
        byte_length: 18,
        content: "Exact source bytes",
      }),
    );

    await expect(
      getEvidence("alpha", {
        ref: "evidence/source packet.txt",
        sha256: "c".repeat(64),
      }),
    ).resolves.toMatchObject({
      projectId: "alpha",
      ref: "evidence/source packet.txt",
      sha256: "c".repeat(64),
      content: "Exact source bytes",
    });
    expect(fetch).toHaveBeenCalledWith(
      `/api/evidence?project_id=alpha&ref=evidence%2Fsource+packet.txt&sha256=${"c".repeat(64)}`,
      { headers: { Accept: "application/json" } },
    );
  });

  it("rejects evidence returned with a different exact identity", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        project_id: "beta",
        project_profile_revision: "beta-profile-v1",
        evidence_ref: "evidence/source.json",
        evidence_sha256: "c".repeat(64),
        byte_length: 5,
        content: "wrong",
      }),
    );
    await expect(
      getEvidence("alpha", {
        ref: "evidence/source.json",
        sha256: "c".repeat(64),
      }),
    ).rejects.toThrow("different bytes or project identity");
  });

  it("loads only media bound to the exact review packet", async () => {
    const material = { material_id: "m1", record_ref: "records/studio/m1.json",
      record_sha256: "d".repeat(64), name: "frame.png", mime_type: "image/png",
      size_bytes: 3, content_sha256: "e".repeat(64), processing_status: "indexed" };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({
      project_id: "alpha", project_profile_revision: "alpha-profile-v1",
      packet_ref: "evidence/materials.json", packet_sha256: "c".repeat(64),
      ...material, content_base64: "YWJj",
    }));
    await expect(getReviewMaterial("alpha", {
      ref: "evidence/materials.json", sha256: "c".repeat(64),
    }, material)).resolves.toMatchObject({ name: "frame.png", contentBase64: "YWJj" });
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/review-material?"),
      { headers: { Accept: "application/json" } });
  });

  it("posts only the exact action contract and derives rework status", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          action_record: {
            review_action_version: "1.0",
            action_id: "action-1",
            project_id: "alpha",
            project_profile_revision: "alpha-profile-v1",
            artifact_id: "launch-brief",
            artifact_revision: "r1",
            artifact_sha256: "a".repeat(64),
            action: "note",
            note: "Clarify evidence.",
            reason: null,
            rework_requested: true,
            actor_type: "human",
            recorded_at: "2026-08-31T12:00:00Z",
          },
          idempotent_replay: false,
          automation: { status: "not_applicable", release_package: null, delivery_status: "not_applicable" },
        },
        201,
      ),
    );
    const request = {
      action: "note" as const,
      project_id: "alpha",
      project_profile_revision: "alpha-profile-v1",
      artifact_id: "launch-brief",
      artifact_revision: "r1",
      artifact_sha256: "a".repeat(64),
      note: "Clarify evidence.",
    };

    await expect(submitReviewAction(request)).resolves.toEqual({
      actionId: "action-1",
      automationStatus: "not_applicable",
      deliveryStatus: "not_applicable",
      nextStatus: "rework_requested",
      reviewNote: "Clarify evidence.",
    });
    expect(fetch).toHaveBeenCalledWith("/api/review-actions", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  });

  it("rejects a review envelope that crosses project identity", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        project_id: "other-project",
        project_profile_revision: "other-profile-v1",
        reviews: [],
      }),
    );

    await expect(getReviews("alpha")).rejects.toThrow("different project");
  });

  it.each([
    ["review_item_version", undefined],
    ["review_item_version", "review-item-v1"],
    ["artifact_content", undefined],
  ])("rejects a review without required %s", async (field, missingValue) => {
    const review = {
      review_item_version: "1.0",
      project_id: "alpha",
      project_profile_revision: "alpha-profile-v1",
      artifact_id: "launch-brief",
      artifact_revision: "r1",
      artifact_sha256: "a".repeat(64),
      artifact_content: "Exact final product.",
      title: "Launch brief",
      type: "content-package",
      status: "pending",
      preview: "Context only.",
      evidence: [],
      quality_checks: [],
      revision: {
        id: "r1",
        sha256: "a".repeat(64),
        artifact_path: "outbox/artifacts/launch-brief/r1.md",
      },
    } as Record<string, unknown>;
    review[field] = missingValue;
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        project_id: "alpha",
        project_profile_revision: "alpha-profile-v1",
        reviews: [review],
      }),
    );

    await expect(getReviews("alpha")).rejects.toThrow("identity mismatch");
  });

  it.each([
    ["artifact revision", { artifact_revision: "r2" }],
    ["artifact hash", { artifact_sha256: "b".repeat(64) }],
  ])("rejects an action response with a different %s", async (_label, mismatch) => {
    const request: ReviewActionRequest = {
      action: "approve",
      project_id: "alpha",
      project_profile_revision: "alpha-profile-v1",
      artifact_id: "launch-brief",
      artifact_revision: "r1",
      artifact_sha256: "a".repeat(64),
    };
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        action_record: {
          review_action_version: "1.0",
          action_id: "action-mismatch",
          project_id: "alpha",
          project_profile_revision: "alpha-profile-v1",
          artifact_id: "launch-brief",
          artifact_revision: "r1",
          artifact_sha256: "a".repeat(64),
          action: "approve",
          note: null,
          reason: null,
          rework_requested: false,
          actor_type: "human",
          recorded_at: "2026-08-31T12:00:00Z",
          ...mismatch,
        },
        idempotent_replay: false,
      }),
    );

    await expect(submitReviewAction(request)).rejects.toThrow("different action or artifact revision");
  });

  it.each([
    ["project", { project_id: "other-project" }],
    ["revision", { artifact_revision: "missing-revision" }],
    ["hash", { artifact_sha256: "f".repeat(64) }],
  ])("demo adapter rejects a cross-identity %s action", async (_label, mismatch) => {
    const source = demoReviews[0];
    const request: ReviewActionRequest = {
      action: "approve",
      project_id: source.projectId,
      project_profile_revision: source.projectProfileRevision,
      artifact_id: source.id,
      artifact_revision: source.revision,
      artifact_sha256: source.artifactSha256,
      ...mismatch,
    };

    await expect(demoAdapter.submitReviewAction(request)).rejects.toThrow("exact selected artifact revision");
  });
});
