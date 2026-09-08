import { describe, expect, it } from "vitest";
import { getContextualFlow } from "./contextualFlow";
import type { AutomationStatus, ProjectDesk, ReviewArtifact } from "./types";

function desk(overrides: Partial<ProjectDesk> = {}): ProjectDesk {
  return {
    projectId: "alpha", projectProfileRevision: "alpha-v2", displayName: "Alpha", phase: "research_ready",
    goal: { title: "Useful work", detail: "Choose a useful lesson." },
    audience: { label: "Operators", detail: "A hypothesis, not a measured audience." },
    successSignal: { label: "Useful feedback", detail: "Record what was learned." },
    baseline: { status: "not_measured", detail: "No outcome data." },
    horizon: { label: "One cycle", detail: "A bounded investigation." },
    resources: { detail: "Existing resources." }, doNothingOption: { detail: "Keep the current approach." }, nonGoals: [],
    readiness: { completed: 1, total: 3, steps: [
      { id: "context", label: "Context", status: "complete", detail: "Recorded." },
      { id: "audience", label: "Audience", status: "active", detail: "Unvalidated." },
      { id: "baseline", label: "Baseline", status: "blocked", detail: "Unavailable." },
    ] },
    blockers: [], nextRoles: [],
    activeResearch: { title: "Research", stage: "evidence", progressLabel: "In progress", detail: "Saved work.", nextStep: "Inspect it." },
    source: "api", ...overrides,
  };
}

function automation(action: AutomationStatus["action"], overrides: Partial<AutomationStatus> = {}): AutomationStatus {
  return { action, run_id: "run-1", route_id: "route-1", active_roles: [], final_title: null, reason: null, progress: null, ...overrides };
}

function review(status: ReviewArtifact["status"], overrides: Partial<ReviewArtifact> = {}): ReviewArtifact {
  return {
    id: "lesson", reviewItemVersion: "1.0", projectId: "alpha", projectProfileRevision: "alpha-v2",
    artifactPath: "lesson/r1.md", title: "A useful lesson", kind: "lesson", status, revision: "r1", revisionLabel: "Revision r1",
    artifactSha256: "a".repeat(64), artifactContent: "Exact lesson.", updatedLabel: "Recorded", decision: "Review exact bytes.",
    sections: [], evidence: [], qualityChecks: [], ...overrides,
  };
}

describe("contextual pipeline navigation", () => {
  it("puts a required brief before a pending review", () => {
    expect(getContextualFlow(desk({ automation: automation("brief_required") }), [review("pending")]))
      .toMatchObject({ focus: "understand", target: "brief" });
    expect(getContextualFlow(desk({ phase: "brief_required" }), []).target).toBe("brief");
    expect(getContextualFlow(desk({ source: "local_setup" }), []).target).toBe("brief");
  });

  it("prioritizes an exact project review over rework and a blocked route", () => {
    const flow = getContextualFlow(desk({ automation: automation("blocked") }), [review("rework_requested"), review("pending")]);
    expect(flow).toMatchObject({ focus: "create", target: "review", reviewFilter: "pending" });
    expect(flow.detail).toBe("A useful lesson");
  });

  it.each(["prepare_rework", "blocked", "dispatch"] as const)("keeps notes accessible while runtime is %s", (action) => {
    expect(getContextualFlow(desk({ automation: automation(action) }), [review("rework_requested")]))
      .toMatchObject({ focus: "create", target: "review", reviewFilter: "rework_requested" });
  });

  it("isolates both project IDs and profile revisions", () => {
    const otherReviews = [review("pending", { projectId: "beta" }), review("rework_requested", { projectProfileRevision: "alpha-v1" }), review("approved", { projectId: "beta" })];
    expect(getContextualFlow(desk(), otherReviews)).toEqual(getContextualFlow(desk(), []));
  });

  it("shows runtime blockers without presenting a run trigger", () => {
    const flow = getContextualFlow(desk({ automation: automation("blocked", { reason: "Source context is missing." }) }), []);
    expect(flow).toMatchObject({ target: "research", detail: "Source context is missing." });
    expect(flow.stages.find((stage) => stage.id === "create")?.state).toBe("blocked");
  });

  it.each(["dispatch", "ready_to_start"] as const)("links %s to existing work context, not a new run", (action) => {
    const flow = getContextualFlow(desk({ automation: automation(action) }), []);
    expect(flow).toMatchObject({ focus: "create", target: "research", actionLabel: "View work context" });
    expect(flow.detail).toContain("does not start another run");
  });

  it("does not pretend dispatchable roles are verified live workers", () => {
    const flow = getContextualFlow(desk({ automation: automation("dispatch", { active_roles: [{ role_id: "writer", label: "Writer", step_id: "write" }] }) }), []);
    expect(flow.detail).toContain("Writer");
    expect(flow.detail).toContain("not a live-worker confirmation");
  });

  it("shows a queue mismatch honestly instead of inventing a review", () => {
    const flow = getContextualFlow(desk({ automation: automation("awaiting_review") }), [review("pending", { projectProfileRevision: "alpha-v1" })]);
    expect(flow).toMatchObject({ target: "review", reviewFilter: "pending" });
    expect(flow.detail).toContain("no matching item");
    expect(flow.stages.find((stage) => stage.id === "create")?.state).toBe("waiting");
  });

  it("keeps a settled decline available without calling it completed content", () => {
    const flow = getContextualFlow(desk({ automation: automation("settled") }), [review("declined")]);
    expect(flow).toMatchObject({ target: "review", reviewFilter: "declined" });
    expect(flow.stages.find((stage) => stage.id === "create")?.state).toBe("waiting");
  });

  it("never turns approved work or 6/6 processing into measured growth", () => {
    const flow = getContextualFlow(desk({ automation: automation("settled", { progress: { completed: 6, total: 6 } }) }), [review("approved")]);
    expect(flow).toMatchObject({ focus: "learn", target: "insights" });
    expect(flow.detail).toContain("not publication or measured impact");
    expect(flow.stages.find((stage) => stage.id === "learn")?.state).toBe("waiting");
  });

  it("does not mark Learn done even with a recorded starting baseline", () => {
    const flow = getContextualFlow(desk({ automation: automation("settled"), baseline: { status: "measured", detail: "A starting measurement only." } }), []);
    expect(flow.stages.find((stage) => stage.id === "learn")?.state).toBe("active");
    expect(flow.stages.find((stage) => stage.id === "create")?.state).toBe("waiting");
  });

  it("uses readiness steps rather than counts or approval to describe understanding", () => {
    const unresolved = desk({ readiness: { completed: 6, total: 6, steps: [{ id: "evidence", label: "Evidence", status: "blocked", detail: "Missing sources." }] } });
    expect(getContextualFlow(unresolved, [review("approved")]).stages[0].state).toBe("blocked");
    const ready = desk({ readiness: { completed: 0, total: 6, steps: [{ id: "evidence", label: "Evidence", status: "complete", detail: "Verified." }] } });
    expect(getContextualFlow(ready, []).stages[0].state).toBe("done");
    expect(getContextualFlow(desk({ readiness: { completed: 0, total: 0, steps: [] } }), []).stages[0].state).toBe("active");
  });

  it.each([undefined, automation("unconfigured")])("keeps unavailable automation benign", (runtime) => {
    const flow = getContextualFlow(desk({ automation: runtime }), []);
    expect(flow).toMatchObject({ focus: "understand", target: "research" });
    expect(flow.reviewFilter).toBeUndefined();
  });

  it("is deterministic without mutating its inputs", () => {
    const source = desk();
    const queue = [review("pending")];
    const before = JSON.stringify({ source, queue });
    expect(getContextualFlow(source, queue)).toEqual(getContextualFlow(source, queue));
    expect(JSON.stringify({ source, queue })).toBe(before);
  });
});
