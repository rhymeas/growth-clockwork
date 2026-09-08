import { Check, CheckCircle2, ChevronRight, File, FileCheck2, Filter, RotateCcw, X } from "lucide-react";
import { useMemo } from "react";
import { reviewKey, reviewStatusLabels, type ReviewArtifact, type ReviewStatus } from "../types";

export type QueueFilter = ReviewStatus;

const filters: Array<{ id: QueueFilter; label: string }> = [
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "declined", label: "Declined" },
  { id: "rework_requested", label: "Rework" },
];

function FilterIcon({ filter }: { filter: QueueFilter }) {
  if (filter === "approved") return <Check aria-hidden="true" />;
  if (filter === "declined") return <X aria-hidden="true" />;
  if (filter === "rework_requested") return <RotateCcw aria-hidden="true" />;
  return <CheckCircle2 aria-hidden="true" />;
}

interface QueueRailProps {
  filter: QueueFilter;
  reviews: ReviewArtifact[];
  selectedKey: string | null;
  onFilterChange: (filter: QueueFilter) => void;
  onSelect: (id: string) => void;
}

export function QueueRail({ filter, reviews, selectedKey, onFilterChange, onSelect }: QueueRailProps) {
  const counts = useMemo(
    () =>
      filters.reduce<Record<QueueFilter, number>>(
        (result, item) => ({ ...result, [item.id]: reviews.filter((review) => review.status === item.id).length }),
        { pending: 0, approved: 0, declined: 0, rework_requested: 0 },
      ),
    [reviews],
  );
  const visible = reviews.filter((review) => review.status === filter);

  return (
    <aside className="queue-panel" aria-label="Review queue">
      <div className="panel-title-row">
        <h2>Content & review</h2>
        <Filter aria-hidden="true" />
      </div>

      <nav className="queue-filters" aria-label="Review status">
        {filters.map((item) => (
          <button
            key={item.id}
            type="button"
            className={filter === item.id ? "queue-filter active" : "queue-filter"}
            aria-current={filter === item.id ? "page" : undefined}
            onClick={() => onFilterChange(item.id)}
          >
            <span className="filter-label"><FilterIcon filter={item.id} />{item.label}</span>
            {counts[item.id] > 0 && <span className="filter-count">{counts[item.id]}</span>}
          </button>
        ))}
      </nav>

      <div className="queue-divider" />
      <div className="queue-items">
        {visible.map((review) => (
          <button
            key={reviewKey(review)}
            type="button"
            className={selectedKey === reviewKey(review) ? "queue-item selected" : "queue-item"}
            onClick={() => onSelect(reviewKey(review))}
          >
            <span className="queue-item-icon"><File aria-hidden="true" /></span>
            <span className="queue-item-copy">
              <strong>{review.title}</strong>
              <small>{review.revisionLabel} · {reviewStatusLabels[review.status]}</small>
            </span>
            <ChevronRight aria-hidden="true" className="queue-chevron" />
          </button>
        ))}

        {visible.length === 0 && (
          <div className="queue-empty">
            <FileCheck2 aria-hidden="true" />
            <p>No {filters.find((item) => item.id === filter)?.label.toLowerCase()} revisions.</p>
          </div>
        )}
      </div>
    </aside>
  );
}
