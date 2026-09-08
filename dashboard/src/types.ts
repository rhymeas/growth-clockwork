export type ReviewStatus = "pending" | "approved" | "declined" | "rework_requested";
export type ReviewAction = "approve" | "decline" | "note";

export const reviewStatusLabels: Record<ReviewStatus, string> = {
  pending: "Ready for review",
  approved: "Approved",
  declined: "Declined",
  rework_requested: "Revision requested",
};

export interface ProjectFoundation {
  id: string;
  title: string;
  status: "reference" | "unavailable";
  status_label: string;
  summary: string;
  documents: Array<{ label: string; markdown: string }>;
}

export interface ProjectSummary {
  id: string;
  name: string;
  projectProfileRevision: string;
}

/**
 * Customer-safe, project-level read model. The project engine owns its values;
 * the desk only presents them and never infers progress from a review decision.
 */
export type ReadinessStatus = "complete" | "active" | "blocked" | "waiting";

export interface ProjectReadinessStep {
  id: string;
  label: string;
  status: ReadinessStatus;
  detail: string;
  ownerRole?: string;
}

export interface ProjectReadiness {
  completed: number;
  total: number;
  steps: ProjectReadinessStep[];
}

export interface ProjectBlocker {
  title: string;
  detail: string;
  ownerRole: string;
}

export interface ProjectRole {
  role: string;
  label: string;
  focus: string;
  status: "active" | "next" | "waiting" | "complete";
}

export interface ActiveResearch {
  title: string;
  stage: string;
  progressLabel: string;
  detail: string;
  nextStep: string;
}

export interface ProjectDesk {
  foundation?: ProjectFoundation[];
  automation?: AutomationStatus;
  projectId: string;
  projectProfileRevision: string;
  displayName: string;
  phase: string;
  goal: { title: string; detail: string };
  currentWork?: { title: string; detail: string };
  audience: { label: string; detail: string };
  successSignal: { label: string; detail: string };
  baseline: { status: "measured" | "not_measured"; detail: string };
  horizon: { label: string; detail: string };
  resources: { detail: string };
  doNothingOption: { detail: string };
  nonGoals: string[];
  readiness: ProjectReadiness;
  blockers: ProjectBlocker[];
  nextRoles: ProjectRole[];
  activeResearch: ActiveResearch;
  authority?: {
    publishMode: "off" | "review" | "automatic";
    publishConfiguration: "configured" | "safe_default" | "invalid_fail_closed";
    automaticPublish: boolean;
    humanReviewRequired: boolean;
    readyToQueueIsNotGoLive: boolean;
  };
  /** The API is authoritative. Local setup data is deliberately labeled. */
  source: "api" | "local_setup";
}

export interface ProjectBriefInput {
  goal: { title: string; detail: string };
  audience: { label: string; detail: string };
  successSignal: { label: string; detail: string };
  baseline: { status: "measured" | "not_measured"; detail: string };
  horizon: { label: string; detail: string };
  resources: { detail: string };
  doNothingOption: { detail: string };
  nonGoals: string[];
  researchQuestion: string;
}

export interface ProjectBriefRequest {
  project_id: string;
  project_profile_revision: string;
  goal: { title: string; detail: string };
  audience: { label: string; detail: string };
  success_signal: { label: string; detail: string };
  baseline: { status: "measured" | "not_measured"; detail: string };
  horizon: { label: string; detail: string };
  resources: { detail: string };
  do_nothing_option: { detail: string };
  non_goals: string[];
  research_question: string;
}

export interface EvidenceReference {
  id: string;
  label: string;
  ref: string;
  sha256: string;
}

export interface VerifiedEvidence {
  projectId: string;
  projectProfileRevision: string;
  ref: string;
  sha256: string;
  byteLength: number;
  content: string;
}

export interface ReviewMaterialDescriptor {
  material_id: string;
  record_ref: string;
  record_sha256: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  content_sha256: string;
  processing_status: string;
}

export interface VerifiedReviewMaterial extends ReviewMaterialDescriptor {
  projectId: string;
  projectProfileRevision: string;
  packetRef: string;
  packetSha256: string;
  contentBase64: string;
}

export interface QualityCheck {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "not_run";
  detail?: string;
}

export interface PreviewSection {
  id: string;
  title: string;
  paragraphs?: string[];
  bullets?: string[];
}

export interface ReviewArtifact {
  id: string;
  reviewItemVersion: string;
  projectId: string;
  projectProfileRevision: string;
  artifactPath: string;
  title: string;
  kind: string;
  status: ReviewStatus;
  revision: string;
  revisionLabel: string;
  artifactSha256: string;
  artifactContent: string;
  updatedLabel: string;
  decision: string;
  reviewNote?: string;
  declineReason?: string;
  sections: PreviewSection[];
  evidence: EvidenceReference[];
  qualityChecks: QualityCheck[];
}

export function reviewKey(
  review: Pick<
    ReviewArtifact,
    "projectId" | "projectProfileRevision" | "id" | "revision" | "artifactSha256"
  >,
): string {
  return JSON.stringify([
    review.projectId,
    review.projectProfileRevision,
    review.id,
    review.revision,
    review.artifactSha256,
  ]);
}

export interface ReviewActionRequest {
  action: ReviewAction;
  project_id: string;
  project_profile_revision: string;
  artifact_id: string;
  artifact_revision: string;
  artifact_sha256: string;
  note?: string;
  reason?: string;
}

export interface ReviewActionResponse {
  actionId: string;
  nextStatus: ReviewStatus;
  reviewNote?: string;
  declineReason?: string;
  automationStatus?: "packaged" | "needs_attention" | "not_applicable";
  deliveryStatus?: "not_enabled" | "not_applicable" | "needs_attention" | "queued" | "processing" | "completed" | "drafted" | "scheduled" | "submitted";
}

export interface ProjectsApiResponse {
  projects: Array<{
    project_id: string;
    display_name: string;
    project_profile_revision: string;
  }>;
}

/**
 * Deliberately small public boundary for the Project Start engine.
 * It carries operational language only — never artifact paths, hashes, raw
 * source material, or internal execution records.
 */
export interface ProjectDeskApiResponse {
  foundation?: ProjectFoundation[];
  automation?: AutomationStatus;
  project_id: string;
  project_profile_revision: string;
  display_name: string;
  phase: string;
  goal: { title: string; detail: string };
  current_work?: { title: string; detail: string };
  audience: { label: string; detail: string };
  success_signal: { label: string; detail: string };
  baseline: { status: "measured" | "not_measured"; detail: string };
  horizon: { label: string; detail: string };
  resources: { detail: string };
  do_nothing_option: { detail: string };
  non_goals: string[];
  readiness: {
    completed: number;
    total: number;
    steps: Array<{
      id: string;
      label: string;
      status: ReadinessStatus;
      detail: string;
      owner_role?: string;
    }>;
  };
  blockers: Array<{ title: string; detail: string; owner_role: string }>;
  next_roles: Array<{
    role: string;
    label: string;
    focus: string;
    status: ProjectRole["status"];
  }>;
  active_research: {
    title: string;
    stage: string;
    progress_label: string;
    detail: string;
    next_step: string;
  };
  authority: {
    publish_mode: "off" | "review" | "automatic";
    publish_configuration: "configured" | "safe_default" | "invalid_fail_closed";
    automatic_publish: boolean;
    human_review_required: boolean;
    ready_to_queue_is_not_go_live: boolean;
  };
}

export interface AutomationStatus {
  action: "dispatch" | "ready_to_start" | "prepare_rework" | "awaiting_review" | "settled" | "blocked" | "brief_required" | "unconfigured";
  run_id: string | null;
  route_id: string | null;
  active_roles: Array<{role_id: string; label: string; step_id: string}>;
  final_title: string | null;
  reason: string | null;
  progress: {completed: number; total: number} | null;
}

export interface ProjectBriefApiResponse {
  brief: {
    project_id: string;
    project_profile_revision: string;
    id: string;
    revision: string;
  };
  created: boolean;
  desk: ProjectDeskApiResponse;
}

/**
 * A deliberately narrow result from the durable local starting check. It is
 * planning context only: never research, a model output, or a public action.
 */
export type GoalLoopStatus = "not_started" | "queued" | "completed";

export interface GoalLoopInsight {
  label: string;
  state: string;
  detail: string;
}

export interface GoalLoopResult {
  status: "completed";
  outcome: "needs_input";
  title: string;
  summary: string;
  nextStep: string;
  insights: GoalLoopInsight[];
  sources: Array<{ label: string; state: string }>;
  limits: string[];
}

export interface GoalLoop {
  id: string | null;
  status: GoalLoopStatus;
  requestRecorded: boolean;
  result: GoalLoopResult | null;
}

export interface GoalLoopApiResponse {
  project_id: string;
  project_profile_revision: string;
  job: {
    id: string | null;
    status: GoalLoopStatus;
    request_recorded: boolean;
    result: {
      status: "completed";
      outcome: "needs_input";
      title: string;
      summary: string;
      next_step: string;
      insights: Array<{ label: string; state: string; detail: string }>;
      sources: Array<{ label: string; state: string }>;
      limits: string[];
    } | null;
  };
}

export interface ReviewsApiResponse {
  project_id: string;
  project_profile_revision: string;
  reviews: Array<{
    review_item_version: string;
    project_id: string;
    project_profile_revision: string;
    artifact_id: string;
    artifact_revision: string;
    artifact_sha256: string;
    artifact_content: string;
    title: string;
    type: string;
    status: ReviewStatus;
    action?: ReviewActionApiResponse["action_record"] | null;
    preview: string | {
      decision: string;
      sections: PreviewSection[];
    };
    evidence: Array<{ label: string; ref: string; sha256: string }>;
    quality_checks: Array<{
      name: string;
      status: QualityCheck["status"];
      detail?: string;
    }>;
    revision: {
      id: string;
      sha256: string;
      artifact_path: string;
    };
  }>;
}

export interface ReviewActionApiResponse {
  action_record: {
    review_action_version: string;
    action_id: string;
    project_id: string;
    project_profile_revision: string;
    artifact_id: string;
    artifact_revision: string;
    artifact_sha256: string;
    action: ReviewAction;
    actor_type: "human";
    recorded_at: string;
    note: string | null;
    reason: string | null;
    rework_requested: boolean;
  };
  idempotent_replay: boolean;
  automation?: {
    status: "packaged" | "needs_attention" | "not_applicable";
    delivery_status: "not_enabled" | "not_applicable" | "needs_attention" | "queued" | "processing" | "completed" | "drafted" | "scheduled" | "submitted";
    publisher_task_id?: string;
    release_package: {
      artifact_ref: string;
      artifact_revision: string;
      artifact_sha256: string;
    } | null;
  };
}

export interface EvidenceApiResponse {
  project_id: string;
  project_profile_revision: string;
  evidence_ref: string;
  evidence_sha256: string;
  byte_length: number;
  content: string;
}

export interface ReviewMaterialApiResponse extends ReviewMaterialDescriptor {
  project_id: string;
  project_profile_revision: string;
  packet_ref: string;
  packet_sha256: string;
  content_base64: string;
}
