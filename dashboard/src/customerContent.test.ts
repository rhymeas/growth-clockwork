import { describe, expect, it } from "vitest";
import { customerSections, customerText } from "./customerContent";

describe("customer content presentation", () => {
  it("turns a final JSON artifact into readable review sections without exposing its technical envelope", () => {
    const sections = customerSections(
      JSON.stringify({
        contract_id: "evidence-packet@1",
        project_id: "example-project",
        artifact_sha256: "a".repeat(64),
        decision_question: "Which option has enough evidence for human review?",
        findings: [
          {
            option: "Example Project fixture",
            assessment: "Documented, but not proven to create a repeatable outcome.",
            confidence: "medium",
            evidence_refs: ["records/private-source.json"],
          },
        ],
        unknowns: ["No measured outcome exists yet."],
        conclusion: "insufficient_evidence",
        proof_boundary: { synthetic: true, publishable: false },
      }),
    );

    expect(sections).toEqual([
      expect.objectContaining({
        title: "Decision question",
        paragraphs: ["Which option has enough evidence for human review?"],
      }),
      expect.objectContaining({
        title: "Findings",
        bullets: ["Example Project fixture — Documented, but not proven to create a repeatable outcome. (medium confidence)"],
      }),
      expect.objectContaining({ title: "Still unknown", bullets: ["No measured outcome exists yet."] }),
      expect.objectContaining({ title: "Conclusion", paragraphs: ["insufficient_evidence"] }),
    ]);

    const rendered = JSON.stringify(sections);
    expect(rendered).not.toContain("contract_id");
    expect(rendered).not.toContain("example-project");
    expect(rendered).not.toContain("a".repeat(64));
    expect(rendered).not.toContain("records/private-source.json");
  });

  it("keeps useful prose while removing code and executable markup", () => {
    const sections = customerSections(`Overview\nA concise explanation.\n\n\`\`\`json\n{ "private": true }\n\`\`\`\n\n- A useful next step\n<script>alert("unsafe")</script>`);

    expect(sections).toEqual([
      expect.objectContaining({
        title: "Overview",
        paragraphs: ["A concise explanation."],
        bullets: ["A useful next step"],
      }),
    ]);
    expect(customerText("<script>alert('unsafe')</script>Clear decision.")).toBe("Clear decision.");
  });
});
