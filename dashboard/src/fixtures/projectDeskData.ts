import type { GoalLoop, ProjectBriefInput, ProjectDesk, ProjectSummary } from "../types";

function setupDesk(project: ProjectSummary): ProjectDesk {
  return {
    projectId: project.id,
    projectProfileRevision: project.projectProfileRevision,
    displayName: project.name,
    phase: "Project setup",
    goal: {
      title: "Choose one useful outcome",
      detail: "Define the decision this project should make before research begins.",
    },
    audience: {
      label: "Audience not set",
      detail: "Name the people, situation, and problem the work should investigate.",
    },
    successSignal: {
      label: "Success signal not set",
      detail: "Choose one observable signal and a review window before production starts.",
    },
    baseline: {
      status: "not_measured",
      detail: "No baseline is recorded yet. Do not estimate one from assumptions.",
    },
    horizon: {
      label: "Horizon not set",
      detail: "Set the decision window before research moves into a route.",
    },
    resources: {
      detail: "Available people, time, and evidence access still need to be defined.",
    },
    doNothingOption: {
      detail: "Keep the current workflow and record why no route should be started yet.",
    },
    nonGoals: [
      "Do not start public work before the brief is clear.",
      "Do not treat assumptions as evidence.",
    ],
    readiness: {
      completed: 0,
      total: 4,
      steps: [
        {
          id: "goal",
          label: "Project goal",
          status: "active",
          detail: "Turn the request into one bounded decision.",
          ownerRole: "Growth coordinator",
        },
        {
          id: "audience",
          label: "Audience brief",
          status: "waiting",
          detail: "Describe the audience and their current situation.",
          ownerRole: "Product marketing",
        },
        {
          id: "evidence",
          label: "Research plan",
          status: "waiting",
          detail: "Set the question, sources, and stopping rule.",
          ownerRole: "Evidence researcher",
        },
        {
          id: "route",
          label: "First route",
          status: "waiting",
          detail: "Choose the next professional work route from evidence.",
          ownerRole: "Growth coordinator",
        },
      ],
    },
    blockers: [
      {
        title: "Project brief is incomplete",
        detail: "A clear goal, audience, and success signal are required to begin research.",
        ownerRole: "Growth coordinator",
      },
    ],
    nextRoles: [
      {
        role: "growth_coordinator",
        label: "Growth coordinator",
        focus: "Qualify the objective and choose the first route.",
        status: "active",
      },
      {
        role: "product_marketing",
        label: "Product marketing",
        focus: "Turn the audience context into a useful market brief.",
        status: "next",
      },
      {
        role: "evidence_researcher",
        label: "Evidence researcher",
        focus: "Prepare the research method once the brief is ready.",
        status: "waiting",
      },
    ],
    activeResearch: {
      title: "Research brief not started",
      stage: "Brief",
      progressLabel: "Waiting for a clear project brief",
      detail: "The engine will start with a bounded evidence question, not a channel or campaign.",
      nextStep: "Define the project goal and audience.",
    },
    source: "local_setup",
  };
}

const demoDeskOverrides: Record<string, ProjectDesk> = {
  "example-project": {
    projectId: "example-project",
    projectProfileRevision: "example-project-profile-v2",
    displayName: "Example Project",
    phase: "Research in progress",
    goal: {
      title: "Choose the next evidence-backed growth route",
      detail: "Decide which customer problem deserves the next bounded investigation.",
    },
    audience: {
      label: "Independent product teams",
      detail: "Operators who need a repeatable way to turn customer signals into one decision.",
    },
    successSignal: {
      label: "A decision-ready research brief",
      detail: "One evidence-backed route with its assumptions, limits, and next owner made explicit.",
    },
    baseline: {
      status: "not_measured",
      detail: "A measurable outcome baseline is still required before this route can claim impact.",
    },
    horizon: {
      label: "Current research cycle",
      detail: "Choose a decision point after the evidence review is complete.",
    },
    resources: {
      detail: "One evidence researcher, one audience synthesis pass, and analytics support are available next.",
    },
    doNothingOption: {
      detail: "Keep the existing plan unchanged if evidence does not justify a route decision.",
    },
    nonGoals: [
      "Do not publish or start a campaign from this research.",
      "Do not call a finding proven without a measured outcome.",
    ],
    readiness: {
      completed: 2,
      total: 5,
      steps: [
        {
          id: "goal",
          label: "Project goal",
          status: "complete",
          detail: "A bounded decision and scope are recorded.",
          ownerRole: "Growth coordinator",
        },
        {
          id: "audience",
          label: "Audience frame",
          status: "complete",
          detail: "The target situation and research question are defined.",
          ownerRole: "Product marketing",
        },
        {
          id: "evidence",
          label: "Evidence collection",
          status: "active",
          detail: "Collect and preserve supporting and contradicting sources.",
          ownerRole: "Evidence researcher",
        },
        {
          id: "synthesis",
          label: "Research synthesis",
          status: "waiting",
          detail: "Compare findings against the original question and stop rule.",
          ownerRole: "Audience researcher",
        },
        {
          id: "route",
          label: "Route decision",
          status: "waiting",
          detail: "Choose the next work route only after the evidence check.",
          ownerRole: "Growth coordinator",
        },
      ],
    },
    blockers: [
      {
        title: "Outcome baseline is still missing",
        detail: "The next route needs a measurable starting point before it can claim impact.",
        ownerRole: "Analytics lead",
      },
    ],
    nextRoles: [
      {
        role: "evidence_researcher",
        label: "Evidence researcher",
        focus: "Finish the source set and retain contradictions.",
        status: "active",
      },
      {
        role: "audience_researcher",
        label: "Audience researcher",
        focus: "Synthesize observed problems without inflating a sample.",
        status: "next",
      },
      {
        role: "analytics_lead",
        label: "Analytics lead",
        focus: "Bind a baseline and decision window to the selected route.",
        status: "waiting",
      },
    ],
    activeResearch: {
      title: "Audience evidence brief",
      stage: "Evidence collection",
      progressLabel: "2 of 5 research steps ready",
      detail: "The current question is whether the observed problem has enough evidence to justify a route decision.",
      nextStep: "Review the source set for coverage and contradictions.",
    },
    source: "api",
  },
};

const submittedDemoDesks = new Map<string, ProjectDesk>();
const demoGoalLoops = new Map<string, GoalLoop>();

function deskFromBrief(project: ProjectSummary, brief: ProjectBriefInput): ProjectDesk {
  return {
    projectId: project.id,
    projectProfileRevision: project.projectProfileRevision,
    displayName: project.name,
    phase: "Research ready",
    goal: brief.goal,
    audience: brief.audience,
    successSignal: brief.successSignal,
    baseline: brief.baseline,
    horizon: brief.horizon,
    resources: brief.resources,
    doNothingOption: brief.doNothingOption,
    nonGoals: brief.nonGoals,
    readiness: {
      completed: 1,
      total: 4,
      steps: [
        {
          id: "goal",
          label: "Project brief",
          status: "complete",
          detail: "The goal, audience, and success signal are recorded.",
          ownerRole: "Growth coordinator",
        },
        {
          id: "research",
          label: "Research plan",
          status: "active",
          detail: "The evidence question is ready to be scoped.",
          ownerRole: "Evidence researcher",
        },
        {
          id: "synthesis",
          label: "Research synthesis",
          status: "waiting",
          detail: "Compare evidence against the original question.",
          ownerRole: "Audience researcher",
        },
        {
          id: "route",
          label: "Route decision",
          status: "waiting",
          detail: "Choose the next work route from the reviewed evidence.",
          ownerRole: "Growth coordinator",
        },
      ],
    },
    blockers: [],
    nextRoles: [
      {
        role: "evidence_researcher",
        label: "Evidence researcher",
        focus: "Turn the research question into a bounded source plan.",
        status: "active",
      },
      {
        role: "audience_researcher",
        label: "Audience researcher",
        focus: "Review supporting and contradicting audience signals.",
        status: "next",
      },
    ],
    activeResearch: {
      title: "Research brief",
      stage: "Research plan",
      progressLabel: "1 of 4 research steps ready",
      detail: brief.researchQuestion,
      nextStep: "Set the sources, inclusion criteria, and stopping rule.",
    },
    source: "api",
  };
}

export const projectDeskAdapter = {
  async getProjectDesk(project: ProjectSummary): Promise<ProjectDesk> {
    return structuredClone(submittedDemoDesks.get(project.id) ?? demoDeskOverrides[project.id] ?? setupDesk(project));
  },
  createSetupDesk(project: ProjectSummary): ProjectDesk {
    return setupDesk(project);
  },
  async submitProjectBrief(project: ProjectSummary, brief: ProjectBriefInput): Promise<ProjectDesk> {
    const desk = deskFromBrief(project, brief);
    submittedDemoDesks.set(project.id, desk);
    return structuredClone(desk);
  },
  async getGoalLoop(project: ProjectSummary): Promise<GoalLoop> {
    return structuredClone(
      demoGoalLoops.get(project.id) ?? {
        id: null,
        status: "not_started",
        requestRecorded: false,
        result: null,
      },
    );
  },
  async startGoalLoop(project: ProjectSummary): Promise<GoalLoop> {
    const desk = submittedDemoDesks.get(project.id) ?? demoDeskOverrides[project.id];
    if (!desk) throw new Error("Set a project brief before starting the local check.");
    const loop: GoalLoop = {
      id: `DEMO-${project.id}-LOCAL-CHECK`,
      status: "completed",
      requestRecorded: true,
      result: {
        status: "completed",
        outcome: "needs_input",
        title: "Demo local starting check complete",
        summary: "This demo confirms the visible workflow only. It did not research the web, call a model, create content, or take a public action.",
        nextStep: desk.activeResearch.nextStep,
        insights: [
          {
            label: "Demo context",
            state: "demo",
            detail: "A local sample is shown so the interaction can be checked without a service.",
          },
          {
            label: "External evidence",
            state: "not_run",
            detail: "No external source was collected.",
          },
          {
            label: "Public actions",
            state: "protected",
            detail: "This check cannot publish, post, upload, merge, or change an account.",
          },
        ],
        sources: [
          { label: "Demo project brief", state: "sample" },
          { label: "Demo context", state: "sample" },
        ],
        limits: [
          "This is a demo of the local workflow, not audience research or a market result.",
          "It uses no provider, analytics property, or publishing connection.",
        ],
      },
    };
    demoGoalLoops.set(project.id, loop);
    return structuredClone(loop);
  },
};
