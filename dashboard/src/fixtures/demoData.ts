import type {
  ProjectSummary,
  EvidenceReference,
  ReviewActionRequest,
  ReviewActionResponse,
  ReviewArtifact,
  VerifiedEvidence,
} from "../types";
import { reviewKey } from "../types";

export const demoProjects: ProjectSummary[] = [
  {
    id: "example-project",
    name: "Example Project",
    projectProfileRevision: "example-project-profile-v2",
  },
  {
    id: "project-sandbox",
    name: "Project Sandbox",
    projectProfileRevision: "project-sandbox-profile-v1",
  },
];

const sharedChecks = [
  { id: "schema", label: "Schema valid", status: "pass" as const },
  { id: "sources", label: "Sources resolved", status: "pass" as const },
  { id: "policy", label: "Policy clear", status: "pass" as const },
  { id: "preview", label: "Preview verified", status: "pass" as const },
];

const demoEvidenceContent = "Synthetic demo evidence. Not publishable.\n";
const demoEvidenceSha256 = "833fe1d6dfe96b7f2c11b4f196ba92644b704aa9ad7b1fa47c665e6a9e782e5b";

function demoEvidence(id: string, label: string): EvidenceReference {
  return {
    id,
    label,
    ref: `evidence/demo/${id}.txt`,
    sha256: demoEvidenceSha256,
  };
}

export const demoReviews: ReviewArtifact[] = [
  {
    id: "activation-experiment-charter",
    reviewItemVersion: "1.0",
    projectId: "example-project",
    projectProfileRevision: "example-project-profile-v2",
    artifactPath: "outbox/artifacts/activation-experiment-charter/04.md",
    title: "Activation experiment charter",
    kind: "Experiment",
    status: "pending",
    revision: "04",
    revisionLabel: "Revision 04",
    artifactSha256: "9f3c7b8a2d6e4f1a8c0b7d9e6a1b2c3d4e5f67899f3c7b8a2d6e4f1a8c0b7d9e",
    artifactContent: `Activation experiment charter

Decision
Approve the scope, method, and measurement plan for this activation experiment.

Hypothesis
A targeted activation sequence for Segment A will influence a defined behavior within the first 7 days.

Experiment design
- Audience: Segment A
- Treatment: Activation sequence
- Control: Current default experience
- Allocation: 50/50 random assignment
- Duration: 14 days`,
    updatedLabel: "Updated 8 min ago",
    decision:
      "Approve the scope, method, and measurement plan for this activation experiment to proceed to build and launch.",
    sections: [
      {
        id: "hypothesis",
        title: "Hypothesis",
        paragraphs: [
          "A targeted activation sequence for Segment A will influence a defined behavior within the first 7 days.",
        ],
      },
      {
        id: "experiment-design",
        title: "Experiment design",
        bullets: [
          "Audience: Segment A (defined in audience brief v1.2)",
          "Treatment: Activation sequence (email + in-app)",
          "Control: Current default experience",
          "Allocation: 50/50 random assignment",
          "Duration: 14 days",
          "Primary metric: Engagement with activation step 2",
          "Secondary metrics: Step completion rate, time to step 2",
          "Guardrail metrics: Unsubscribe rate, support contacts",
        ],
      },
      {
        id: "success-criteria",
        title: "Success criteria",
        bullets: [
          "Primary metric achieves the pre-registered decision threshold",
          "Minimum detectable effect (MDE) met or exceeded",
          "No guardrail metric degrades beyond its pre-defined threshold",
          "Data completeness meets the measurement-plan requirement",
          "Results are reproducible with data validation checks",
        ],
      },
    ],
    evidence: [
      demoEvidence("brief", "Brief – Activation experiment v1.2"),
      demoEvidence("audience", "Audience definition v1.2"),
      demoEvidence("measurement", "Measurement plan v1.1"),
      demoEvidence("risk", "Risk assessment v1.0"),
    ],
    qualityChecks: sharedChecks,
  },
  {
    id: "positioning-package",
    reviewItemVersion: "1.0",
    projectId: "example-project",
    projectProfileRevision: "example-project-profile-v2",
    artifactPath: "outbox/artifacts/positioning-package/03.md",
    title: "Positioning package",
    kind: "Positioning",
    status: "pending",
    revision: "03",
    revisionLabel: "Revision 03",
    artifactSha256: "a03f8522a03f8522a03f8522a03f8522a03f8522a03f8522a03f8522a03f8522",
    artifactContent: `Positioning package

Primary audience and market frame
The package defines the selected audience context, competitive alternatives, and evidence-backed message hierarchy.`,
    updatedLabel: "Updated 31 min ago",
    decision: "Approve the selected market frame, audience priority, and evidence-backed message hierarchy.",
    sections: [
      {
        id: "market-frame",
        title: "Market frame",
        paragraphs: ["The package defines the competitive alternatives and the primary audience context."],
      },
      {
        id: "message-hierarchy",
        title: "Message hierarchy",
        bullets: [
          "Primary audience and situation are explicit",
          "Alternatives include the current workflow and doing nothing",
          "Every product claim resolves to approved evidence",
          "Objections have testable response hypotheses",
        ],
      },
    ],
    evidence: [
      demoEvidence("market", "Market alternatives review v1.0"),
      demoEvidence("voice", "Voice-of-customer synthesis v1.1"),
      demoEvidence("claims", "Approved claims register"),
    ],
    qualityChecks: sharedChecks,
  },
  {
    id: "editorial-lesson",
    reviewItemVersion: "1.0",
    projectId: "example-project",
    projectProfileRevision: "example-project-profile-v2",
    artifactPath: "outbox/artifacts/editorial-lesson/02.md",
    title: "Editorial lesson",
    kind: "Editorial",
    status: "pending",
    revision: "02",
    revisionLabel: "Revision 02",
    artifactSha256: "d69c1fddd69c1fddd69c1fddd69c1fddd69c1fddd69c1fddd69c1fddd69c1fdd",
    artifactContent: `Editorial lesson

The complete lesson is delivered on-platform. Claims resolve to their cited sources, examples are labeled, and no promotional request is included.`,
    updatedLabel: "Updated 1 hr ago",
    decision: "Approve the complete lesson for its selected channel and audience context.",
    sections: [
      {
        id: "lesson",
        title: "Lesson",
        paragraphs: [
          "The artifact delivers the full useful lesson on-platform and contains no promotional or engagement request.",
        ],
      },
      {
        id: "editorial-review",
        title: "Editorial review",
        bullets: [
          "Claims match their cited sources",
          "Examples are labeled as illustrative",
          "Voice and locale pass is complete",
          "Links and rendering were checked",
        ],
      },
    ],
    evidence: [
      demoEvidence("sources", "Primary source set v1.0"),
      demoEvidence("editorial", "Editorial review record v1.0"),
    ],
    qualityChecks: sharedChecks,
  },
  {
    id: "sandbox-research-brief",
    reviewItemVersion: "1.0",
    projectId: "project-sandbox",
    projectProfileRevision: "project-sandbox-profile-v1",
    artifactPath: "outbox/artifacts/research-brief/01.md",
    title: "Research brief",
    kind: "Research",
    status: "pending",
    revision: "01",
    revisionLabel: "Revision 01",
    artifactSha256: "8bc091a28bc091a28bc091a28bc091a28bc091a28bc091a28bc091a28bc091a2",
    artifactContent: `Research brief

Question
Which evidence is required before this project chooses a growth route?

Method
- Define source priority before collection
- Retain contradicting evidence
- Apply explicit sampling and stopping rules`,
    updatedLabel: "Updated 12 min ago",
    decision: "Approve the research question, evidence standard, sampling method, and stopping rule.",
    sections: [
      {
        id: "research-question",
        title: "Research question",
        paragraphs: ["Identify which evidence is required before this project chooses a growth route."],
      },
      {
        id: "method",
        title: "Method",
        bullets: [
          "Source priority is defined before collection",
          "Contradicting evidence is retained",
          "Sampling and stopping rules are explicit",
          "Findings remain separate from recommendations",
        ],
      },
    ],
    evidence: [
      demoEvidence("protocol", "Research protocol v1.0"),
      demoEvidence("source-policy", "Source policy v1.0"),
    ],
    qualityChecks: sharedChecks,
  },
];

const updatedReviews = new Map<string, ReviewArtifact>();

export const demoAdapter = {
  async getProjects(): Promise<ProjectSummary[]> {
    return structuredClone(demoProjects);
  },

  async getReviews(projectId: string): Promise<ReviewArtifact[]> {
    return demoReviews
      .filter((review) => review.projectId === projectId)
      .map((review) => structuredClone(updatedReviews.get(reviewKey(review)) ?? review));
  },

  async getEvidence(
    projectId: string,
    reference: { ref: string; sha256: string },
  ): Promise<VerifiedEvidence> {
    const review = demoReviews.find(
      (item) =>
        item.projectId === projectId &&
        item.evidence.some(
          (candidate) =>
            candidate.ref === reference.ref && candidate.sha256 === reference.sha256,
        ),
    );
    if (!review || reference.sha256 !== demoEvidenceSha256) {
      throw new Error("The exact demo evidence bytes no longer exist.");
    }
    return {
      projectId,
      projectProfileRevision: review.projectProfileRevision,
      ref: reference.ref,
      sha256: reference.sha256,
      byteLength: new TextEncoder().encode(demoEvidenceContent).byteLength,
      content: demoEvidenceContent,
    };
  },

  async submitReviewAction(request: ReviewActionRequest): Promise<ReviewActionResponse> {
    const source = demoReviews.find(
      (item) =>
        item.projectId === request.project_id &&
        item.projectProfileRevision === request.project_profile_revision &&
        item.id === request.artifact_id &&
        item.revision === request.artifact_revision &&
        item.artifactSha256 === request.artifact_sha256,
    );

    if (!source) {
      throw new Error("The exact selected artifact revision no longer exists.");
    }

    const nextStatus =
      request.action === "approve" ? "approved" : request.action === "decline" ? "declined" : "rework_requested";
    updatedReviews.set(reviewKey(source), { ...source, status: nextStatus });

    return {
      actionId: crypto.randomUUID(),
      nextStatus,
    };
  },
};
