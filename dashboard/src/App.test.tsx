import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({
  submitReviewAction: vi.fn(),
  submitProjectBrief: vi.fn(),
  getReviews: vi.fn(),
  getProjectDesk: vi.fn(),
  getGoalLoop: vi.fn(),
  startGoalLoop: vi.fn(),
  getEvidence: vi.fn(),
  getReviewMaterial: vi.fn(),
  review: {
    id: "launch-brief",
    reviewItemVersion: "1.0",
    projectId: "alpha",
    projectProfileRevision: "alpha-profile-v1",
    artifactPath: "outbox/artifacts/launch-brief/r1.md",
    title: "Launch brief",
    kind: "content-package",
    status: "pending" as const,
    revision: "r1",
    revisionLabel: "Revision r1",
    artifactSha256: "a".repeat(64),
    artifactContent: "First exact final product.\n\nLine two remains separated.",
    updatedLabel: "Ready for review",
    decision: "Approve this exact launch brief revision.",
    sections: [],
    evidence: [{ id: "source", label: "Primary source", ref: "evidence/source.json", sha256: "c".repeat(64) }],
    qualityChecks: [{ id: "claims", label: "claims", status: "pass" as const, detail: "3/3 cited" }],
  },
}));

vi.mock("./api/client", () => ({
  isDemoMode: false,
  getProjects: vi.fn().mockResolvedValue([
    { id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" },
  ]),
  getReviews: mocks.getReviews,
  getProjectDesk: mocks.getProjectDesk,
  getGoalLoop: mocks.getGoalLoop,
  startGoalLoop: mocks.startGoalLoop,
  getResearchFeeds: vi.fn().mockResolvedValue({project_id: "alpha", week: "2026-W36", result: null}),
  getWebsiteAnalytics: vi.fn().mockResolvedValue({project_id: "alpha", status: "not_connected", source: "ga4", report: null}),
  getPlatformAnalytics: vi.fn().mockResolvedValue({project_id: "alpha", status: "not_connected", source: "native_platform_analytics", report: null}),
  getSetupStatus: vi.fn().mockResolvedValue({desk: "running", background: "active", autostart: {state: "not_installed"}, remote_access: {state: "local_only", provider: "tailscale-serve"}, publication: {mode: "review", publisher: "disabled"}, host: "must_be_awake"}),
  getEvidence: mocks.getEvidence,
  getReviewMaterial: mocks.getReviewMaterial,
  submitProjectBrief: mocks.submitProjectBrief,
  submitReviewAction: mocks.submitReviewAction,
}));

import { App } from "./App";

vi.mock("./api/studio", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/studio")>(),
  getStudio: vi.fn().mockResolvedValue({ project_id: "alpha", project_profile_revision: "alpha-profile-v1", audiences: [], proposals: [], slots: [], materials: [], suggestions: [], capabilities: { platforms: ["website", "medium", "youtube", "instagram", "tiktok", "x", "pinterest"], publishing: false, platform_data: false, max_upload_bytes: 2097152 } }),
}));

async function openReview(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Content & review" }));
  return screen.findByRole("heading", { name: "Launch brief" });
}

const workingFrame = {
  baseline: { status: "not_measured" as const, detail: "No baseline is measured yet; do not estimate one." },
  horizon: { label: "First research cycle", detail: "Review the route decision after evidence collection." },
  resources: { detail: "One researcher and limited analytics support are available." },
  doNothingOption: { detail: "Keep the current approach if evidence does not support a route." },
};

describe("Review Desk", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
    mocks.submitReviewAction.mockReset();
    mocks.submitReviewAction.mockResolvedValue({ actionId: "action-1", nextStatus: "rework_requested" });
    mocks.submitProjectBrief.mockReset();
    mocks.getReviews.mockReset();
    mocks.getReviews.mockResolvedValue([mocks.review]);
    mocks.getReviewMaterial.mockReset();
    mocks.getProjectDesk.mockReset();
    mocks.getProjectDesk.mockResolvedValue({
      projectId: "alpha",
      projectProfileRevision: "alpha-profile-v1",
      displayName: "Alpha",
      phase: "Project setup",
      goal: { title: "Choose one useful outcome", detail: "Define the project decision before research begins." },
      audience: { label: "Audience not set", detail: "Name the audience and current situation." },
      successSignal: { label: "Success signal not set", detail: "Choose one observable signal." },
      ...workingFrame,
      nonGoals: ["Do not start public work before the brief is clear."],
      readiness: {
        completed: 0,
        total: 4,
        steps: [
          { id: "goal", label: "Project goal", status: "active", detail: "Turn the request into one decision." },
          { id: "audience", label: "Audience brief", status: "waiting", detail: "Describe the audience." },
        ],
      },
      blockers: [{ title: "Project brief is incomplete", detail: "Goal and audience are required.", ownerRole: "Growth coordinator" }],
      nextRoles: [{ role: "growth", label: "Growth coordinator", focus: "Qualify the objective.", status: "active" }],
      activeResearch: {
        title: "Research brief not started",
        stage: "Brief",
        progressLabel: "Waiting for a clear project brief",
        detail: "Start with a bounded evidence question.",
        nextStep: "Define the project goal and audience.",
      },
      source: "api",
    });
    const completedGoalLoop = {
      id: "GOAL-alpha",
      status: "completed" as const,
      requestRecorded: true,
      result: {
        status: "completed" as const,
        outcome: "needs_input" as const,
        title: "Local starting check complete",
        summary: "The goal and its pinned context were checked locally.",
        nextStep: "Set sources and a stopping rule.",
        insights: [],
        sources: [],
        limits: ["No web research was run."],
      },
    };
    mocks.getGoalLoop.mockReset();
    mocks.getGoalLoop.mockResolvedValue(completedGoalLoop);
    mocks.startGoalLoop.mockReset();
    mocks.startGoalLoop.mockResolvedValue(completedGoalLoop);
    mocks.submitProjectBrief.mockResolvedValue({
      projectId: "alpha",
      projectProfileRevision: "alpha-profile-v1",
      displayName: "Alpha",
      phase: "Research ready",
      goal: { title: "Choose one useful outcome", detail: "Define the project decision before research begins." },
      audience: { label: "Product operators", detail: "People deciding how to start a project." },
      successSignal: { label: "Decision-ready brief", detail: "The project can now enter a research plan." },
      ...workingFrame,
      nonGoals: ["Do not publish from the brief."],
      readiness: { completed: 1, total: 4, steps: [] },
      blockers: [],
      nextRoles: [],
      activeResearch: {
        title: "Research brief",
        stage: "Research plan",
        progressLabel: "1 of 4 research steps ready",
        detail: "Which evidence should guide the route?",
        nextStep: "Set sources and a stopping rule.",
      },
      source: "api",
    });
    mocks.getEvidence.mockReset();
    mocks.getEvidence.mockResolvedValue({
      projectId: "alpha",
      projectProfileRevision: "alpha-profile-v1",
      ref: "evidence/source.json",
      sha256: "c".repeat(64),
      byteLength: 48,
      content: "Exact source packet.\n<script>alert('unsafe')</script>",
    });
  });

  it("uses one content queue across all four views and preserves the selected theme", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("review-desk-theme", "light");
    render(<App />);
    await screen.findByRole("heading", { name: "Your working world" });
    const nav = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(nav).getAllByRole("button")).toHaveLength(4);
    for (const theme of ["dark", "light"]) {
      await user.click(screen.getByRole("button", { name: `Switch to ${theme} mode` }));
      for (const name of ["Research", "Content & review", "Insights", "Project"]) {
        await user.click(within(nav).getByRole("button", { name }));
        expect(document.documentElement.dataset.theme).toBe(theme);
        expect(window.localStorage.getItem("review-desk-theme")).toBe(theme);
      }
    }
    await user.click(within(nav).getByRole("button", { name: "Insights" }));
    expect(screen.getByRole("heading", { name: "No measured baseline yet" })).toBeVisible();
    expect(screen.getByText(/Approval is not publication/)).toBeVisible();
    expect(new URL(window.location.href).searchParams.get("view")).toBe("insights");
    await user.click(screen.getByRole("button", { name: "View content" }));
    expect(screen.getAllByRole("complementary", { name: "Review queue" })).toHaveLength(1);
    expect(mocks.getReviews).toHaveBeenCalledTimes(1);
    expect(mocks.submitReviewAction).not.toHaveBeenCalled();
  });

  it("opens Insights directly and does not turn an unavailable queue into zero counts", async () => {
    window.history.replaceState(null, "", "/?project=alpha&view=insights");
    mocks.getReviews.mockRejectedValue(new Error("unavailable"));
    render(<App />);
    expect(await screen.findByRole("heading", { name: "What we know so far" })).toBeVisible();
    expect(screen.getByText("Content queue unavailable. Revision counts cannot be shown.")).toBeVisible();
    expect(document.querySelector(".insights-counts")).toBeNull();
    expect(mocks.submitReviewAction).not.toHaveBeenCalled();
  });

  it("shows verified foundation references without introducing another approval queue", async () => {
    const user = userEvent.setup();
    const desk = await mocks.getProjectDesk();
    mocks.getProjectDesk.mockResolvedValue({ ...desk, foundation: [{
      id: "facts", title: "Product facts", status: "reference", status_label: "Candidates — not approved",
      summary: "Read-only source references.", documents: [{ label: "Approved facts", markdown: "No facts are approved yet." }],
    }, {
      id: "audiences", title: "Audiences", status: "unavailable", status_label: "Reference refresh needed",
      summary: "The reference changed; refresh it before displaying it.", documents: [],
    }] });
    render(<App />);
    await user.click(await screen.findByText("Project context", { exact: true }));
    const heading = await screen.findByRole("heading", { name: "Project foundation" });
    const foundation = heading.closest("section")!;
    await user.click(within(foundation).getByText("Product facts", { exact: true }));
    await user.click(within(foundation).getByText("Approved facts", { exact: true }));
    expect(within(foundation).getByText("No facts are approved yet.")).toBeVisible();
    expect(within(foundation).getByText("Reference refresh needed")).toBeVisible();
    expect(within(foundation).queryByRole("button")).not.toBeInTheDocument();
    expect(mocks.submitReviewAction).not.toHaveBeenCalled();
  });

  it("labels archived decisions from their real status instead of calling them ready", async () => {
    const user = userEvent.setup();
    mocks.getReviews.mockResolvedValue([{ ...mocks.review, status: "declined" }]);
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Content & review" }));
    await user.click(await screen.findByRole("button", { name: "Declined 1" }));
    expect(await screen.findByRole("heading", { name: "Launch brief" })).toBeVisible();
    expect(document.querySelector(".artifact-status")).toHaveTextContent("Declined");
    expect(screen.queryByText("Ready for review", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("opens the selected project's review directly without taking a decision", async () => {
    window.history.replaceState(null, "", "/?project=alpha&view=review");
    render(<App />);
    expect(await screen.findByRole("heading", {name:"Launch brief"})).toBeVisible();
    expect(screen.getByLabelText("Select project")).toHaveValue("alpha");
    expect(mocks.submitReviewAction).not.toHaveBeenCalled();
  });

  it("prioritizes a finished product over incomplete product knowledge", async () => {
    const user = userEvent.setup();
    const desk = await mocks.getProjectDesk();
    mocks.getProjectDesk.mockResolvedValue({...desk, phase: "context_blocked", automation: {
      action: "awaiting_review", run_id: "RUN-example", route_id: "content-channel", active_roles: [],
      final_title: "Launch brief", reason: null, progress: {completed: 6, total: 6},
    }});
    render(<App />);
    expect(await screen.findByRole("button", {name:"Review the draft"})).toBeVisible();
    expect(screen.getByText("A draft is ready for you", {exact: false})).toBeVisible();
    await user.click(screen.getByText("Project context", {exact:true}));
    expect(screen.getByRole("heading", {name:"Ready for your review"})).toBeVisible();
    expect(screen.getByLabelText("Marketing production")).toHaveTextContent("6/6");
    expect(screen.getByRole("heading", {name:"Outcomes"})).toBeVisible();
    expect(screen.getByRole("region", {name:"Website analytics"})).toHaveTextContent("GA4 report not connected");
    await user.click(screen.getByRole("button", {name:"Review the draft"}));
    expect(await screen.findByRole("heading", {name:"Launch brief"})).toBeVisible();
    expect(mocks.submitReviewAction).not.toHaveBeenCalled();
  });

  it("starts on a project landing view and moves into the active research progression", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Your working world" })).toBeInTheDocument();
    expect(screen.getByText("Choose one useful outcome")).not.toBeVisible();
    await user.click(screen.getByText("Project context", {exact:true}));
    expect(screen.getByRole("heading", { name: "Choose one useful outcome" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Audience not set" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Research progression" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Blockers" })).toBeInTheDocument();
    expect(screen.getAllByText("Growth coordinator").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Open research" }));
    expect(await screen.findByRole("heading", { name: "Research brief not started" })).toBeInTheDocument();
    await user.click(screen.getByText("Research progression", {exact:true}));
    expect(screen.getByText("Waiting for a clear project brief")).toBeVisible();
    expect(screen.getByText("Define the project goal and audience.")).toBeInTheDocument();

    await openReview(user);
    expect(screen.getByText("First exact final product.", { exact: false })).toBeInTheDocument();
  });

  it("opens the requested revision and its recorded notes from both project and research", async () => {
    const user = userEvent.setup();
    mocks.getReviews.mockResolvedValue([{ ...mocks.review, status: "rework_requested", reviewNote: "Preserve the limits.\n<script>Text, not markup.</script>" }]);
    const desk = await mocks.getProjectDesk();
    mocks.getProjectDesk.mockResolvedValue({ ...desk, automation: {
      action: "prepare_rework", run_id: "RUN-alpha", route_id: "content-channel", active_roles: [], final_title: "Launch brief", reason: null, progress: null,
    } });
    render(<App />);
    const action = await screen.findByRole("button", {name: "View revision notes"});
    expect(action.closest("section")).toHaveAccessibleName("Next step");
    await user.click(action);
    expect(screen.getByRole("button", {name: "Rework 1"})).toHaveAttribute("aria-current", "page");
    expect(within(screen.getByRole("region", {name: "Revision notes"})).getByText(/Preserve the limits/)).toBeVisible();
    expect(screen.getByRole("region", {name: "Revision notes"}).querySelector("script")).toBeNull();
    expect(screen.queryByRole("button", {name: "Approve"})).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "Research"}));
    await user.click(await screen.findByRole("button", {name: "View revision notes"}));
    expect(screen.getByRole("region", {name: "Revision notes"})).toBeVisible();
    expect(mocks.submitReviewAction).not.toHaveBeenCalled();
    expect(mocks.startGoalLoop).not.toHaveBeenCalled();
  });

  it("puts the current work first in the narrow-screen reading and keyboard order", async () => {
    const media = window.matchMedia("(max-width: 900px)");
    vi.spyOn(window, "matchMedia").mockImplementation(query => ({ ...media, media: query, matches: query === "(max-width: 900px)" }));
    render(<App />);
    await screen.findByRole("button", {name:"Review the draft"});
    const worlds = screen.getAllByRole("article");
    expect(worlds[0]).toHaveAccessibleName("Audience");
    expect(worlds[1]).toHaveAccessibleName("Content");
    expect(worlds[2]).toHaveAccessibleName("Planning");
    const next = screen.getByRole("region", { name: "Next step" });
    expect(next.compareDocumentPosition(worlds[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("creates a customer-safe project brief before research begins", async () => {
    const user = userEvent.setup();
    mocks.getProjectDesk.mockReset();
    mocks.getProjectDesk
      .mockResolvedValueOnce({
        projectId: "alpha",
        projectProfileRevision: "alpha-profile-v1",
        displayName: "Alpha",
        phase: "brief_required",
        goal: { title: "Choose one useful outcome", detail: "Define the project decision before research begins." },
        audience: { label: "Audience not set", detail: "Name the audience and situation." },
        successSignal: { label: "Success signal not set", detail: "Choose one observable signal." },
        ...workingFrame,
        nonGoals: ["Do not begin public work."],
        readiness: { completed: 0, total: 4, steps: [] },
        blockers: [],
        nextRoles: [],
        activeResearch: {
          title: "Research brief not started",
          stage: "Brief",
          progressLabel: "Waiting for a clear project brief",
          detail: "Start with a bounded evidence question.",
          nextStep: "Define the project goal and audience.",
        },
        source: "api",
      })
      .mockResolvedValueOnce({
        projectId: "alpha",
        projectProfileRevision: "alpha-profile-v1",
        displayName: "Alpha",
        phase: "Research ready",
        goal: { title: "Clarify a customer problem", detail: "Choose the problem with enough evidence for a route decision." },
        audience: { label: "Product operators", detail: "People deciding which project to run next." },
        successSignal: { label: "Decision-ready brief", detail: "A bounded next step is ready to review." },
        ...workingFrame,
        nonGoals: ["Do not publish from research."],
        readiness: { completed: 1, total: 4, steps: [] },
        blockers: [],
        nextRoles: [],
        activeResearch: {
          title: "Research brief",
          stage: "Research plan",
          progressLabel: "1 of 4 research steps ready",
          detail: "Which evidence should guide the route?",
          nextStep: "Set sources and a stopping rule.",
        },
        source: "api",
      });
    mocks.submitProjectBrief.mockResolvedValue({
      projectId: "alpha",
      projectProfileRevision: "alpha-profile-v1",
      displayName: "Alpha",
      phase: "Research ready",
      goal: { title: "Clarify a customer problem", detail: "Choose the problem with enough evidence for a route decision." },
      audience: { label: "Product operators", detail: "People deciding which project to run next." },
      successSignal: { label: "Decision-ready brief", detail: "A bounded next step is ready to review." },
      ...workingFrame,
      nonGoals: ["Do not publish from research."],
      readiness: { completed: 1, total: 4, steps: [] },
      blockers: [],
      nextRoles: [],
      activeResearch: {
        title: "Research brief",
        stage: "Research plan",
        progressLabel: "1 of 4 research steps ready",
        detail: "Which evidence should guide the route?",
        nextStep: "Set sources and a stopping rule.",
      },
      source: "api",
    });
    render(<App />);

    await screen.findByRole("heading", { name: "Your working world" });
    await user.click(screen.getByRole("button", { name: "Set project direction" }));
    const dialog = await screen.findByRole("dialog", { name: "Set the project brief" });
    await user.type(within(dialog).getByLabelText("Goal title"), "Clarify a customer problem");
    await user.type(within(dialog).getByLabelText("Goal detail"), "Choose the problem with enough evidence for a route decision.");
    await user.type(within(dialog).getByLabelText("Audience name"), "Product operators");
    await user.type(within(dialog).getByLabelText("Audience situation"), "People deciding which project to run next.");
    await user.type(within(dialog).getByLabelText("Signal name"), "Decision-ready brief");
    await user.type(within(dialog).getByLabelText("How it will be checked"), "A bounded next step is ready to review.");
    await user.type(within(dialog).getByLabelText("Current baseline"), "No baseline is measured yet; do not estimate one.");
    await user.type(within(dialog).getByLabelText("Decision horizon"), "First research cycle");
    await user.type(within(dialog).getByLabelText("Horizon detail"), "Review the route decision after evidence collection.");
    await user.type(within(dialog).getByLabelText("Resources"), "One researcher and limited analytics support are available.");
    await user.type(within(dialog).getByLabelText("Do nothing option"), "Keep the current approach if evidence does not support a route.");
    await user.type(within(dialog).getByLabelText("Research question"), "Which evidence should guide the route?");
    await user.type(within(dialog).getByLabelText(/Not in scope/), "Do not publish from research.");
    await user.click(within(dialog).getByRole("button", { name: "Start project" }));

    await waitFor(() =>
      expect(mocks.submitProjectBrief).toHaveBeenCalledWith(
        { id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" },
        {
          goal: {
            title: "Clarify a customer problem",
            detail: "Choose the problem with enough evidence for a route decision.",
          },
          audience: {
            label: "Product operators",
            detail: "People deciding which project to run next.",
          },
          successSignal: {
            label: "Decision-ready brief",
            detail: "A bounded next step is ready to review.",
          },
          baseline: { status: "not_measured", detail: "No baseline is measured yet; do not estimate one." },
          horizon: { label: "First research cycle", detail: "Review the route decision after evidence collection." },
          resources: { detail: "One researcher and limited analytics support are available." },
          doNothingOption: { detail: "Keep the current approach if evidence does not support a route." },
          nonGoals: ["Do not publish from research."],
          researchQuestion: "Which evidence should guide the route?",
        },
      ),
    );
    expect(await screen.findByText("Project brief saved. Local check complete; research still needs real evidence.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Set the project brief" })).not.toBeInTheDocument();
    expect(mocks.getProjectDesk).toHaveBeenCalledTimes(2);
    expect(mocks.startGoalLoop).toHaveBeenCalledWith({ id: "alpha", name: "Alpha", projectProfileRevision: "alpha-profile-v1" });
  }, 15_000);

  it("sends an exact-revision note request from the accessible dialog", async () => {
    const user = userEvent.setup();
    render(<App />);

    await openReview(user);
    await user.click(screen.getByRole("button", { name: "Add note" }));

    const dialog = screen.getByRole("dialog", { name: "Add a note" });
    expect(dialog).toBeInTheDocument();
    const note = screen.getByLabelText("What should change?");
    expect(note).toHaveFocus();
    await user.type(note, "Clarify the evidence threshold.");
    await user.click(screen.getByRole("button", { name: "Request new revision" }));

    await waitFor(() =>
      expect(mocks.submitReviewAction).toHaveBeenCalledWith({
        action: "note",
        project_id: "alpha",
        project_profile_revision: "alpha-profile-v1",
        artifact_id: "launch-brief",
        artifact_revision: "r1",
        artifact_sha256: "a".repeat(64),
        note: "Clarify the evidence threshold.",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("new revision was requested");
    const reworkFilter = screen.getByRole("button", { name: "Rework 1" });
    expect(reworkFilter).toBeInTheDocument();
    await user.click(reworkFilter);
    expect(screen.getByText("Rework requested for this immutable revision.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve revision" })).not.toBeInTheDocument();
  });

  it("closes the note dialog with Escape and restores focus", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openReview(user);
    const trigger = screen.getByRole("button", { name: "Add note" });
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("requires and sends a reason when declining", async () => {
    const user = userEvent.setup();
    mocks.submitReviewAction.mockResolvedValue({ actionId: "action-2", nextStatus: "declined" });
    render(<App />);
    await openReview(user);

    await user.click(screen.getByRole("button", { name: "Decline" }));
    const reason = screen.getByLabelText("Why should this revision be declined?");
    await user.type(reason, "The primary evidence is missing.");
    await user.click(screen.getByRole("button", { name: "Decline revision" }));

    await waitFor(() =>
      expect(mocks.submitReviewAction).toHaveBeenCalledWith({
        action: "decline",
        project_id: "alpha",
        project_profile_revision: "alpha-profile-v1",
        artifact_id: "launch-brief",
        artifact_revision: "r1",
        artifact_sha256: "a".repeat(64),
        reason: "The primary evidence is missing.",
      }),
    );
  });

  it("keeps two revisions with the same artifact id distinct and acts on the selected hash", async () => {
    const user = userEvent.setup();
    const secondRevision = {
      ...mocks.review,
      revision: "r2",
      revisionLabel: "Revision r2",
      artifactSha256: "b".repeat(64),
      artifactContent: "<script>alert('unsafe')</script>\nSecond exact final product.",
    };
    mocks.getReviews.mockResolvedValue([mocks.review, secondRevision]);
    mocks.submitReviewAction.mockResolvedValue({
      actionId: "action-r2",
      nextStatus: "approved",
      automationStatus: "packaged",
      deliveryStatus: "not_enabled",
    });
    render(<App />);

    await openReview(user);
    await screen.findByText("First exact final product.", { exact: false });
    await user.click(screen.getByRole("button", { name: /Launch brief Revision r2/ }));
    expect(screen.getByText("Second exact final product.")).toBeInTheDocument();
    expect(screen.queryByText("<script>alert('unsafe')</script>", { exact: false })).not.toBeInTheDocument();
    expect(document.querySelector(".customer-document script")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(mocks.submitReviewAction).toHaveBeenCalledWith({
        action: "approve",
        project_id: "alpha",
        project_profile_revision: "alpha-profile-v1",
        artifact_id: "launch-brief",
        artifact_revision: "r2",
        artifact_sha256: "b".repeat(64),
      }),
    );
    expect(screen.getByText("Revision r2 approved. Release package ready; nothing was published.")).toBeVisible();
  });

  it("renders agent-draft Markdown as a readable review document", async () => {
    const user = userEvent.setup();
    mocks.getReviews.mockResolvedValue([{
      ...mocks.review,
      kind: "agent-draft",
      artifactContent: "# Hidden duplicate title\n\nThe **practical rule** stays visible.\n\n| Item | State |\n|---|---|\n| Owner | Missing |",
    }]);
    render(<App />);

    await openReview(user);
    expect(screen.queryByRole("heading", { name: "Hidden duplicate title" })).toBeNull();
    expect(screen.getByText("practical rule", { selector: "strong" })).toBeVisible();
    expect(screen.getByRole("table")).toHaveTextContent("OwnerMissing");
  });

  it("reports a queued publisher task without claiming delivery is live", async () => {
    const user = userEvent.setup();
    mocks.submitReviewAction.mockResolvedValue({
      actionId: "action-r1",
      nextStatus: "approved",
      automationStatus: "packaged",
      deliveryStatus: "queued",
    });
    render(<App />);

    await openReview(user);
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Revision r1 approved. Publisher task queued.")).toBeVisible();
  });

  it("opens a checked source without exposing technical source bytes and restores focus on Escape", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openReview(user);

    const trigger = screen.getByRole("button", { name: "Primary source" });
    await user.click(trigger);
    expect(await screen.findByRole("dialog", { name: "Primary source" })).toBeInTheDocument();
    await waitFor(() =>
      expect(mocks.getEvidence).toHaveBeenCalledWith("alpha", {
        id: "source",
        label: "Primary source",
        ref: "evidence/source.json",
        sha256: "c".repeat(64),
      }),
    );
    expect(await screen.findByText("Source checked")).toBeInTheDocument();
    expect(screen.getByText("Exact source packet.")).toBeInTheDocument();
    expect(screen.queryByText("<script>alert('unsafe')</script>", { exact: false })).not.toBeInTheDocument();
    expect(document.querySelector(".evidence-viewer script")).toBeNull();
    expect(document.querySelector(".evidence-viewer pre")).toBeNull();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Primary source" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("shows an evidence verification error without changing review actions", async () => {
    const user = userEvent.setup();
    mocks.getEvidence.mockRejectedValueOnce(new Error("Evidence bytes changed after the queue loaded."));
    render(<App />);
    await openReview(user);
    await user.click(screen.getByRole("button", { name: "Primary source" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This source could not be shown");
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close evidence viewer" }));
    expect(screen.queryByRole("dialog", { name: "Primary source" })).not.toBeInTheDocument();
  });

  it("shows exact media bound by a verified material packet", async () => {
    const user = userEvent.setup();
    const packet = {
      material_packet_version: "1.0", project_id: "alpha",
      project_profile_revision: "alpha-profile-v1", channel: "instagram",
      materials: [{ material_id: "m1", record_ref: "records/studio/m1.json",
        record_sha256: "d".repeat(64), name: "frame.png", mime_type: "image/png",
        size_bytes: 3, content_sha256: "e".repeat(64), processing_status: "indexed" }],
    };
    mocks.getEvidence.mockResolvedValueOnce({ projectId: "alpha", projectProfileRevision: "alpha-profile-v1",
      ref: "evidence/materials.json", sha256: "c".repeat(64), byteLength: 1,
      content: JSON.stringify(packet) });
    mocks.getReviewMaterial.mockResolvedValueOnce({ ...packet.materials[0], projectId: "alpha",
      projectProfileRevision: "alpha-profile-v1", packetRef: "evidence/materials.json",
      packetSha256: "c".repeat(64), contentBase64: "YWJj" });
    const review = { ...mocks.review, evidence: [{ id: "materials", label: "Exact source and publication material",
      ref: "evidence/materials.json", sha256: "c".repeat(64) }] };
    mocks.getReviews.mockResolvedValueOnce([review]);
    render(<App />);
    await openReview(user);
    await user.click(await screen.findByRole("button", { name: "Exact source and publication material" }));
    expect(await screen.findByAltText("Reviewed material: frame.png")).toHaveAttribute("src", "data:image/png;base64,YWJj");
    expect(screen.getByText("image/png · 0.0 KiB")).toBeVisible();
  });
});
