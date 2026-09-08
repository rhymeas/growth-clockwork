import { demoAdapter } from "../fixtures/demoData";
import { projectDeskAdapter } from "../fixtures/projectDeskData";
import type {
  ProjectDesk,
  ProjectDeskApiResponse,
  ProjectBriefApiResponse,
  ProjectBriefInput,
  ProjectBriefRequest,
  ProjectSummary,
  ProjectsApiResponse,
  GoalLoop,
  GoalLoopApiResponse,
  EvidenceApiResponse,
  ReviewMaterialApiResponse,
  ReviewMaterialDescriptor,
  VerifiedReviewMaterial,
  VerifiedEvidence,
  ReviewActionRequest,
  ReviewActionApiResponse,
  ReviewActionResponse,
  ReviewArtifact,
  ReviewsApiResponse,
} from "../types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new Error(body?.error?.message ?? `Review API returned ${response.status}.`);
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new Error("Review API did not return JSON.");
  }

  return (await response.json()) as T;
}

export const isDemoMode =
  import.meta.env.VITE_REVIEW_DATA_MODE === "demo" || new URLSearchParams(window.location.search).get("demo") === "1";

export interface BrokerStatusResponse {
  project_id: string;
  state: "connected" | "not_configured";
  counts: Record<string, number>;
  tasks?: Array<{
    task_id: string;
    agent_id: string;
    status: "queued" | "running" | "awaiting_review" | "completed" | "failed" | "cancelled";
    attempt: number;
    max_attempts: number;
    not_before: number;
    created_at: number;
    updated_at: number;
  }>;
  truncated?: boolean;
}

export interface WebsiteAnalyticsResponse {
  project_id: string;
  status: "not_connected" | "verified";
  source: "ga4";
  setup?: { state: "configuration_required" | "disabled" | "authentication_required" | "ready" };
  report: null | {
    fetched_at: string;
    window: { start_date: string; end_date: string; timezone: string; excluded_latest_days: number };
    metrics: { active_users: number | null; engaged_sessions: number | null; download_clicks: number | null };
    report_sha256: string;
  };
}

export interface SetupStatusResponse {
  desk: "running";
  background: "active" | "manual";
  autostart: { state: "unsupported" | "build_required" | "not_installed" | "installed_not_running" | "running" | "needs_attention" };
  research_schedule: { state: "unsupported" | "configuration_required" | "not_installed" | "installed_not_running" | "running" | "needs_attention" };
  remote_access: { state: "local_only" | "configured" | "needs_attention"; provider: "tailscale-serve" };
  publication: { mode: "not_configured" | "off" | "review" | "automatic"; publisher: "disabled" | "enabled" };
  host: "must_be_awake";
}

export async function getSetupStatus(projectId?: string, signal?: AbortSignal): Promise<SetupStatusResponse> {
  if (isDemoMode) return {
    desk: "running", background: "manual", autostart: { state: "not_installed" },
    research_schedule: { state: "not_installed" },
    remote_access: { state: "local_only", provider: "tailscale-serve" },
    publication: { mode: "review", publisher: "disabled" }, host: "must_be_awake",
  };
  const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const value = await readJson<SetupStatusResponse>(await fetch(`/api/setup-status${suffix}`, {
    headers: { Accept: "application/json" }, signal,
  }));
  const autostartStates = ["unsupported", "build_required", "not_installed", "installed_not_running", "running", "needs_attention"];
  const researchStates = ["unsupported", "configuration_required", "not_installed", "installed_not_running", "running", "needs_attention"];
  const accessStates = ["local_only", "configured", "needs_attention"];
  const publicationModes = ["not_configured", "off", "review", "automatic"];
  if (value.desk !== "running" || !["active", "manual"].includes(value.background)
    || !autostartStates.includes(value.autostart?.state)
    || !researchStates.includes(value.research_schedule?.state)
    || value.remote_access?.provider !== "tailscale-serve" || !accessStates.includes(value.remote_access.state)
    || !publicationModes.includes(value.publication?.mode) || !["disabled", "enabled"].includes(value.publication?.publisher)
    || value.host !== "must_be_awake") {
    throw new Error("Invalid setup status");
  }
  return value;
}

export async function getWebsiteAnalytics(projectId: string, signal?: AbortSignal): Promise<WebsiteAnalyticsResponse> {
  if (isDemoMode) return { project_id: projectId, status: "not_connected", source: "ga4", report: null };
  const value = await readJson<WebsiteAnalyticsResponse>(await fetch(`/api/website-analytics?project_id=${encodeURIComponent(projectId)}`, {
    headers: { Accept: "application/json" }, signal,
  }));
  const metrics = value.report?.metrics;
  const setupStates = ["configuration_required", "disabled", "authentication_required", "ready"];
  const validMetric = (metric: unknown) => metric === null || (Number.isSafeInteger(metric) && Number(metric) >= 0);
  if (value.project_id !== projectId || value.source !== "ga4" || !["not_connected", "verified"].includes(value.status)
    || (value.setup !== undefined && (!value.setup || !setupStates.includes(value.setup.state)))
    || (value.status === "not_connected" ? value.report !== null
      : !value.report || !/^[0-9a-f]{64}$/.test(value.report.report_sha256)
        || !metrics || !Object.values(metrics).every(validMetric))) {
    throw new Error("Invalid website analytics report");
  }
  return value;
}

export type PlatformId = "youtube" | "instagram" | "tiktok" | "x" | "pinterest";
export interface PlatformAnalyticsResponse {
  project_id: string;
  status: "not_connected" | "verified";
  source: "native_platform_analytics";
  report: null | {
    fetched_at: string;
    report_sha256: string;
    platforms: Array<{
      platform: PlatformId;
      window: { start_date: string; end_date: string; timezone: string };
      metrics: Array<{ metric_id: string; label: string; value: number | null; unit: "count" | "seconds" | "percent" }>;
    }>;
  };
}

export async function getPlatformAnalytics(projectId: string, signal?: AbortSignal): Promise<PlatformAnalyticsResponse> {
  if (isDemoMode) return { project_id: projectId, status: "not_connected", source: "native_platform_analytics", report: null };
  const value = await readJson<PlatformAnalyticsResponse>(await fetch(`/api/platform-analytics?project_id=${encodeURIComponent(projectId)}`, {
    headers: { Accept: "application/json" }, signal,
  }));
  const platforms = ["youtube", "instagram", "tiktok", "x", "pinterest"];
  const valid = value.report?.platforms.every(entry => platforms.includes(entry.platform)
    && entry.metrics.length > 0 && entry.metrics.length <= 5
    && entry.metrics.every(metric => metric.value === null || (typeof metric.value === "number" && Number.isFinite(metric.value) && metric.value >= 0)));
  if (value.project_id !== projectId || value.source !== "native_platform_analytics"
    || !["not_connected", "verified"].includes(value.status)
    || (value.status === "not_connected" ? value.report !== null
      : !value.report || !/^[0-9a-f]{64}$/.test(value.report.report_sha256) || !valid)) {
    throw new Error("Invalid platform analytics report");
  }
  return value;
}

export async function getBrokerStatus(projectId: string, signal?: AbortSignal): Promise<BrokerStatusResponse> {
  if (isDemoMode) return { project_id: projectId, state: "not_configured", counts: {} };
  const value = await readJson<BrokerStatusResponse>(await fetch(`/api/broker-status?project_id=${encodeURIComponent(projectId)}`, {
    headers: { Accept: "application/json" }, signal,
  }));
  const statuses = ["queued", "running", "awaiting_review", "completed", "failed", "cancelled"];
  const validTasks = value.tasks === undefined || (Array.isArray(value.tasks)
    && value.tasks.length <= 50
    && value.tasks.every(task => typeof task.task_id === "string" && task.task_id.length > 0
      && task.task_id.length <= 2048 && typeof task.agent_id === "string"
      && task.agent_id.length > 0 && task.agent_id.length <= 2048
      && statuses.includes(task.status)
      && [task.attempt, task.max_attempts, task.not_before, task.created_at, task.updated_at]
        .every(number => Number.isSafeInteger(number) && number >= 0)
      && task.attempt <= task.max_attempts && task.updated_at >= task.created_at));
  if (value.project_id !== projectId || !["connected", "not_configured"].includes(value.state)
    || !value.counts || typeof value.counts !== "object" || Array.isArray(value.counts)
    || Object.entries(value.counts).some(([key, count]) => !statuses.includes(key) || !Number.isSafeInteger(count) || count < 0)
    || !validTasks || (value.truncated !== undefined && typeof value.truncated !== "boolean")) {
    throw new Error("Invalid broker status");
  }
  return value;
}

export interface ResearchFeedsResponse {
  project_id: string;
  week: string;
  synthesis?: null | {
    status: string;
    model?: string;
    analysis: null | { status: string; artifact: string; uncertainties: string[]; next_decision: string };
    sources?: { id: string; title: string; url: string }[];
  };
  result: null | {
    fetched_at: string;
    status: string;
    item_count: number;
    sources: { publisher: string; feed_url: string; status: string; items: {
      title: string; url: string; published: string | null; excerpt: string;
    }[] }[];
  };
}

export async function getResearchFeeds(projectId: string): Promise<ResearchFeedsResponse> {
  if (isDemoMode) return { project_id: projectId, week: "demo", result: null };
  const result = await readJson<ResearchFeedsResponse>(await fetch(`/api/research-feeds?project_id=${encodeURIComponent(projectId)}`, {
    headers: { Accept: "application/json" },
  }));
  if (result.project_id !== projectId) throw new Error("Research belongs to a different project.");
  return result;
}

export function getProjects(): Promise<ProjectSummary[]> {
  if (isDemoMode) return demoAdapter.getProjects();
  return (async () => {
    const response = await readJson<ProjectsApiResponse>(
      await fetch("/api/projects", { headers: { Accept: "application/json" } }),
    );
    return response.projects.map((project) => ({
      id: project.project_id,
      name: project.display_name,
      projectProfileRevision: project.project_profile_revision,
    }));
  })();
}

export function getReviews(projectId: string): Promise<ReviewArtifact[]> {
  if (isDemoMode) return demoAdapter.getReviews(projectId);
  return (async () => {
    const response = await readJson<ReviewsApiResponse>(
      await fetch(`/api/reviews?project_id=${encodeURIComponent(projectId)}`, {
        headers: { Accept: "application/json" },
      }),
    );
    if (response.project_id !== projectId) {
      throw new Error("Review API returned a different project than requested.");
    }
    for (const review of response.reviews) {
      const identityMatches =
        review.review_item_version === "1.0" &&
        typeof review.artifact_content === "string" &&
        review.project_id === response.project_id &&
        review.project_profile_revision === response.project_profile_revision &&
        review.artifact_revision === review.revision.id &&
        review.artifact_sha256 === review.revision.sha256;
      if (!identityMatches || !/^[0-9a-f]{64}$/.test(review.artifact_sha256)) {
        throw new Error(`Review identity mismatch for artifact ${review.artifact_id}.`);
      }
      const action = review.action;
      if (action && (action.project_id !== review.project_id
        || action.project_profile_revision !== review.project_profile_revision
        || action.artifact_id !== review.artifact_id
        || action.artifact_revision !== review.artifact_revision
        || action.artifact_sha256 !== review.artifact_sha256
        || (action.action === "note" ? review.status !== "rework_requested" || typeof action.note !== "string"
          : action.action === "decline" ? review.status !== "declined" || typeof action.reason !== "string"
            : action.action !== "approve" || review.status !== "approved"))) {
        throw new Error(`Review decision identity mismatch for artifact ${review.artifact_id}.`);
      }
    }
    return response.reviews.map((review) => ({
      id: review.artifact_id,
      reviewItemVersion: review.review_item_version,
      projectId: review.project_id,
      projectProfileRevision: review.project_profile_revision,
      artifactPath: review.revision.artifact_path,
      title: review.title,
      kind: review.type,
      status: review.status,
      revision: review.revision.id,
      revisionLabel: `Revision ${review.revision.id}`,
      artifactSha256: review.revision.sha256,
      artifactContent: review.artifact_content,
      updatedLabel: "Ready for review",
      decision: typeof review.preview === "string" ? review.preview : review.preview.decision,
      reviewNote: review.action?.action === "note" ? review.action.note ?? undefined : undefined,
      declineReason: review.action?.action === "decline" ? review.action.reason ?? undefined : undefined,
      sections: typeof review.preview === "string" ? [] : review.preview.sections,
      evidence: review.evidence.map((item) => {
        if (!/^[0-9a-f]{64}$/.test(item.sha256)) {
          throw new Error(`Evidence identity mismatch for ${item.ref}.`);
        }
        return { id: item.ref, label: item.label, ref: item.ref, sha256: item.sha256 };
      }),
      qualityChecks: review.quality_checks.map((item) => ({
        id: item.name,
        label: item.name.replaceAll("_", " "),
        status: item.status,
        detail: item.detail,
      })),
    }));
  })();
}

function normalizeProjectDesk(response: ProjectDeskApiResponse, project: ProjectSummary): ProjectDesk {
  const correctProject =
    response.project_id === project.id && response.project_profile_revision === project.projectProfileRevision;
  const completeCounts =
    Number.isInteger(response.readiness?.completed) &&
    Number.isInteger(response.readiness?.total) &&
    response.readiness.completed >= 0 &&
    response.readiness.total >= response.readiness.completed;
  const validSteps = response.readiness?.steps?.every(
    (step) =>
      typeof step.id === "string" &&
      typeof step.label === "string" &&
      typeof step.detail === "string" &&
      ["complete", "active", "blocked", "waiting"].includes(step.status),
  );
  const validRoleStatuses = response.next_roles?.every((role) => ["active", "next", "waiting", "complete"].includes(role.status));
  const validWorkingFrame =
    ["measured", "not_measured"].includes(response.baseline?.status) &&
    typeof response.baseline?.detail === "string" &&
    typeof response.horizon?.label === "string" &&
    typeof response.horizon?.detail === "string" &&
    typeof response.resources?.detail === "string" &&
    typeof response.do_nothing_option?.detail === "string";
  const validDirection =
    typeof response.goal?.title === "string" &&
    typeof response.goal?.detail === "string" &&
    (response.current_work === undefined || (
      typeof response.current_work?.title === "string" &&
      typeof response.current_work?.detail === "string"
    ));
  const validAuthority =
    ["off", "review", "automatic"].includes(response.authority?.publish_mode) &&
    ["configured", "safe_default", "invalid_fail_closed"].includes(response.authority?.publish_configuration) &&
    response.authority?.automatic_publish === (response.authority?.publish_mode === "automatic") &&
    response.authority?.human_review_required === (response.authority?.publish_mode !== "automatic") &&
    response.authority?.ready_to_queue_is_not_go_live === true;

  if (!correctProject || !completeCounts || !validSteps || !validRoleStatuses || !validWorkingFrame || !validDirection || !validAuthority) {
    throw new Error("Project Desk API returned an invalid project read model.");
  }

  return {
    projectId: response.project_id,
    automation: response.automation,
    foundation: response.foundation,
    projectProfileRevision: response.project_profile_revision,
    displayName: response.display_name,
    phase: response.phase,
    goal: response.goal,
    currentWork: response.current_work ?? response.goal,
    audience: response.audience,
    successSignal: response.success_signal,
    baseline: response.baseline,
    horizon: response.horizon,
    resources: response.resources,
    doNothingOption: response.do_nothing_option,
    nonGoals: response.non_goals,
    readiness: {
      completed: response.readiness.completed,
      total: response.readiness.total,
      steps: response.readiness.steps.map((step) => ({
        id: step.id,
        label: step.label,
        status: step.status,
        detail: step.detail,
        ownerRole: step.owner_role,
      })),
    },
    blockers: response.blockers.map((blocker) => ({
      title: blocker.title,
      detail: blocker.detail,
      ownerRole: blocker.owner_role,
    })),
    nextRoles: response.next_roles.map((role) => ({
      role: role.role,
      label: role.label,
      focus: role.focus,
      status: role.status,
    })),
    activeResearch: {
      title: response.active_research.title,
      stage: response.active_research.stage,
      progressLabel: response.active_research.progress_label,
      detail: response.active_research.detail,
      nextStep: response.active_research.next_step,
    },
    authority: {
      publishMode: response.authority.publish_mode,
      publishConfiguration: response.authority.publish_configuration,
      automaticPublish: response.authority.automatic_publish,
      humanReviewRequired: response.authority.human_review_required,
      readyToQueueIsNotGoLive: response.authority.ready_to_queue_is_not_go_live,
    },
    source: "api",
  };
}

/**
 * A project-level overview is intentionally separate from the exact-revision
 * review APIs. A 404 means this older service has not exposed the read model
 * yet, so the UI renders an explicitly labeled local setup outline. Other
 * service failures remain visible instead of being hidden by a fallback.
 */
export function getProjectDesk(project: ProjectSummary): Promise<ProjectDesk> {
  if (isDemoMode) return projectDeskAdapter.getProjectDesk(project);
  return (async () => {
    const response = await fetch(`/api/project-desk?project_id=${encodeURIComponent(project.id)}`, {
      headers: { Accept: "application/json" },
    });
    if (response.status === 404) return projectDeskAdapter.createSetupDesk(project);
    return normalizeProjectDesk(await readJson<ProjectDeskApiResponse>(response), project);
  })();
}

/**
 * Creates only a project brief. It never creates a route, draft, approval, or
 * public action; the server derives its own record identifiers and returns the
 * refreshed customer-safe desk model.
 */
export function submitProjectBrief(project: ProjectSummary, input: ProjectBriefInput): Promise<ProjectDesk> {
  if (isDemoMode) return projectDeskAdapter.submitProjectBrief(project, input);
  const request: ProjectBriefRequest = {
    project_id: project.id,
    project_profile_revision: project.projectProfileRevision,
    goal: input.goal,
    audience: input.audience,
    success_signal: input.successSignal,
    baseline: input.baseline,
    horizon: input.horizon,
    resources: input.resources,
    do_nothing_option: input.doNothingOption,
    non_goals: input.nonGoals,
    research_question: input.researchQuestion,
  };

  return fetch("/api/project-briefs", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  })
    .then(readJson<ProjectBriefApiResponse>)
    .then((response) => {
      const matchesProject =
        response.brief?.project_id === project.id && response.brief?.project_profile_revision === project.projectProfileRevision;
      if (!matchesProject || typeof response.brief?.id !== "string" || typeof response.brief?.revision !== "string") {
        throw new Error("Project brief API returned a different project or invalid brief.");
      }
      return normalizeProjectDesk(response.desk, project);
    });
}

function normalizeGoalLoop(response: GoalLoopApiResponse, project: ProjectSummary): GoalLoop {
  const job = response.job;
  const identityMatches =
    response.project_id === project.id && response.project_profile_revision === project.projectProfileRevision;
  const validStatus = ["not_started", "queued", "completed"].includes(job?.status);
  const validPending =
    job?.status !== "completed" &&
    job?.result === null &&
    typeof job?.request_recorded === "boolean" &&
    (job.id === null || typeof job.id === "string");
  const result = job?.result;
  const validResult =
    job?.status === "completed" &&
    typeof job?.id === "string" &&
    job.request_recorded === true &&
    result !== null &&
    result.status === "completed" &&
    result.outcome === "needs_input" &&
    typeof result.title === "string" &&
    typeof result.summary === "string" &&
    typeof result.next_step === "string" &&
    Array.isArray(result.insights) &&
    result.insights.every(
      (item) => typeof item.label === "string" && typeof item.state === "string" && typeof item.detail === "string",
    ) &&
    Array.isArray(result.sources) &&
    result.sources.every((item) => typeof item.label === "string" && typeof item.state === "string") &&
    Array.isArray(result.limits) &&
    result.limits.every((item) => typeof item === "string");

  if (!identityMatches || !validStatus || (!validPending && !validResult)) {
    throw new Error("Local check API returned an invalid result.");
  }

  return {
    id: job.id,
    status: job.status,
    requestRecorded: job.request_recorded,
    result: result
      ? {
          status: result.status,
          outcome: result.outcome,
          title: result.title,
          summary: result.summary,
          nextStep: result.next_step,
          insights: result.insights,
          sources: result.sources,
          limits: result.limits,
        }
      : null,
  };
}

/** Reads the durable, local-only starting check for one exact project profile. */
export function getGoalLoop(project: ProjectSummary): Promise<GoalLoop> {
  if (isDemoMode) return projectDeskAdapter.getGoalLoop(project);
  return fetch(`/api/goal-loop?project_id=${encodeURIComponent(project.id)}`, {
    headers: { Accept: "application/json" },
  })
    .then(readJson<GoalLoopApiResponse>)
    .then((response) => normalizeGoalLoop(response, project));
}

/** Starts only the local preflight. It cannot research, call a model, or publish. */
export function startGoalLoop(project: ProjectSummary): Promise<GoalLoop> {
  if (isDemoMode) return projectDeskAdapter.startGoalLoop(project);
  return fetch("/api/goal-loop", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      project_id: project.id,
      project_profile_revision: project.projectProfileRevision,
    }),
  })
    .then(readJson<GoalLoopApiResponse>)
    .then((response) => normalizeGoalLoop(response, project));
}

export function getEvidence(
  projectId: string,
  reference: { ref: string; sha256: string },
): Promise<VerifiedEvidence> {
  if (isDemoMode) return demoAdapter.getEvidence(projectId, reference);
  return (async () => {
    const query = new URLSearchParams({
      project_id: projectId,
      ref: reference.ref,
      sha256: reference.sha256,
    });
    const response = await readJson<EvidenceApiResponse>(
      await fetch(`/api/evidence?${query.toString()}`, {
        headers: { Accept: "application/json" },
      }),
    );
    const exactIdentity =
      response.project_id === projectId &&
      response.evidence_ref === reference.ref &&
      response.evidence_sha256 === reference.sha256 &&
      /^[0-9a-f]{64}$/.test(response.evidence_sha256) &&
      Number.isInteger(response.byte_length) &&
      response.byte_length >= 0 &&
      typeof response.content === "string" &&
      new TextEncoder().encode(response.content).byteLength === response.byte_length;
    if (!exactIdentity) {
      throw new Error("Evidence API returned different bytes or project identity.");
    }
    return {
      projectId: response.project_id,
      projectProfileRevision: response.project_profile_revision,
      ref: response.evidence_ref,
      sha256: response.evidence_sha256,
      byteLength: response.byte_length,
      content: response.content,
    };
  })();
}

export function getReviewMaterial(
  projectId: string,
  packet: { ref: string; sha256: string },
  material: ReviewMaterialDescriptor,
): Promise<VerifiedReviewMaterial> {
  if (isDemoMode) return Promise.reject(new Error("Demo material preview is unavailable."));
  return (async () => {
    const query = new URLSearchParams({
      project_id: projectId,
      packet_ref: packet.ref,
      packet_sha256: packet.sha256,
      material_id: material.material_id,
      content_sha256: material.content_sha256,
    });
    const response = await readJson<ReviewMaterialApiResponse>(
      await fetch(`/api/review-material?${query.toString()}`, {
        headers: { Accept: "application/json" },
      }),
    );
    const descriptorKeys: Array<keyof ReviewMaterialDescriptor> = [
      "material_id", "record_ref", "record_sha256", "name", "mime_type",
      "size_bytes", "content_sha256", "processing_status",
    ];
    const exact = response.project_id === projectId
      && response.packet_ref === packet.ref
      && response.packet_sha256 === packet.sha256
      && descriptorKeys.every((key) => response[key] === material[key])
      && typeof response.content_base64 === "string"
      && /^[A-Za-z0-9+/]*={0,2}$/.test(response.content_base64);
    if (!exact) throw new Error("Material API returned a different review asset.");
    return {
      ...material,
      projectId: response.project_id,
      projectProfileRevision: response.project_profile_revision,
      packetRef: response.packet_ref,
      packetSha256: response.packet_sha256,
      contentBase64: response.content_base64,
    };
  })();
}

export function submitReviewAction(request: ReviewActionRequest): Promise<ReviewActionResponse> {
  if (isDemoMode) return demoAdapter.submitReviewAction(request);
  return fetch("/api/review-actions", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  })
    .then(readJson<ReviewActionApiResponse>)
    .then(({ action_record: record, automation }) => {
      const exactResponse =
        record.action === request.action &&
        record.project_id === request.project_id &&
        record.project_profile_revision === request.project_profile_revision &&
        record.artifact_id === request.artifact_id &&
        record.artifact_revision === request.artifact_revision &&
        record.artifact_sha256 === request.artifact_sha256;
      if (!exactResponse) {
        throw new Error("Review API returned a different action or artifact revision.");
      }
      return {
        actionId: record.action_id,
        ...(automation ? { automationStatus: automation.status } : {}),
        ...(automation ? { deliveryStatus: automation.delivery_status } : {}),
        reviewNote: record.action === "note" ? record.note ?? undefined : undefined,
        declineReason: record.action === "decline" ? record.reason ?? undefined : undefined,
        nextStatus:
          record.action === "approve"
            ? "approved"
            : record.action === "decline"
              ? "declined"
              : "rework_requested",
      };
    });
}
