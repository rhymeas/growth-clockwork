import { BriefcaseBusiness, CheckCircle2, FileText, RotateCcw, Tag, XCircle } from "lucide-react";
import { customerSections, customerText } from "../customerContent";
import { reviewStatusLabels, type ReviewArtifact } from "../types";
import { LessonContent, lessonMarkdown } from "./LessonContent";

export function DocumentPreview({
  review,
  projectName,
}: {
  review: ReviewArtifact;
  projectName: string;
}) {
  const sections = customerSections(review.artifactContent);
  const markdown = review.kind === "agent-draft"
    ? review.artifactContent
    : lessonMarkdown(review.artifactContent);
  const decision = customerText(review.decision);

  return (
    <article className="artifact-preview" aria-labelledby="review-title">
      <header className="artifact-header">
        <div className="artifact-kicker">
          <span>Final review</span>
          <span className={`artifact-status artifact-status-${review.status}`}>
            {review.status === "declined" ? <XCircle aria-hidden="true" /> : review.status === "rework_requested" ? <RotateCcw aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
            {reviewStatusLabels[review.status]}
          </span>
        </div>
        <h1 id="review-title">{review.title}</h1>
        <div className="artifact-meta">
          <span><BriefcaseBusiness aria-hidden="true" />{projectName}</span>
          <span><Tag aria-hidden="true" />{review.kind}</span>
          <span><FileText aria-hidden="true" />{review.revisionLabel}</span>
        </div>
      </header>

      {review.status === "rework_requested" && <section className="recorded-decision" aria-label="Revision notes">
        <span className="section-eyebrow">Notes for the next revision</span>
        <p>{review.reviewNote ?? "The revision request is recorded, but its note is unavailable in this response."}</p>
      </section>}
      {review.status === "declined" && review.declineReason && <section className="recorded-decision" aria-label="Decline reason">
        <span className="section-eyebrow">Reason for declining</span><p>{review.declineReason}</p>
      </section>}
      {decision && (
        <section className="review-focus" aria-label={review.status === "pending" ? "What you are deciding" : "Original review brief"}>
          <span>{review.status === "pending" ? "What you are deciding" : "Original review brief"}</span>
          <p>{decision}</p>
        </section>
      )}

      <div className="document-sheet customer-document" aria-label="Final product">
        {markdown ? <LessonContent markdown={markdown} /> : sections.length ? (
          sections.map((section, index) => (
            <section className={index === 0 ? "document-section first-section" : "document-section"} key={section.id}>
              <h2>{section.title}</h2>
              {section.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
              {section.bullets.length > 0 && <ul>{section.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul>}
            </section>
          ))
        ) : (
          <section className="document-section first-section customer-document-empty">
            <h2>Ready for your decision</h2>
            <p>This version is ready to review. Its technical source format is intentionally kept out of this view.</p>
          </section>
        )}
      </div>
    </article>
  );
}
