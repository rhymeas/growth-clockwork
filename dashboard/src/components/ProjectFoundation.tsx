import type { ProjectFoundation as Foundation } from "../types";
import { LessonContent } from "./LessonContent";

export function ProjectFoundation({ items }: { items: Foundation[] }) {
  if (!items.length) return null;
  return <section className="project-section foundation-section" aria-labelledby="foundation-title">
    <span className="section-eyebrow">One project · shared context</span>
    <h2 id="foundation-title">Project foundation</h2>
    <p>Facts, boundaries, audience hypotheses and sources. These references are not a second approval queue; opening them changes nothing.</p>
    {items.map(item => <details className="foundation-item" key={item.id}>
      <summary><strong>{item.title}</strong><span className={item.status === "unavailable" ? "foundation-unavailable" : ""}>{item.status_label}</span></summary>
      <p>{item.summary}</p>
      {item.documents.map(document => <details className="foundation-document" key={document.label}>
        <summary>{document.label}</summary><LessonContent markdown={document.markdown} />
      </details>)}
    </details>)}
  </section>;
}
