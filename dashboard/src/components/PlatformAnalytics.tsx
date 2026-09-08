import { useEffect, useState } from "react";
import { getPlatformAnalytics, type PlatformAnalyticsResponse, type PlatformId } from "../api/client";

const labels: Record<PlatformId, string> = {
  youtube: "YouTube", instagram: "Instagram", tiktok: "TikTok", x: "X", pinterest: "Pinterest",
};

function valueLabel(value: number | null, unit: "count" | "seconds" | "percent") {
  if (value === null) return "—";
  if (unit === "percent") return `${value}%`;
  if (unit === "seconds") return `${value}s`;
  return value.toLocaleString();
}

export function PlatformAnalytics({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<{ projectId: string; data?: PlatformAnalyticsResponse; error?: boolean }>();
  useEffect(() => {
    const controller = new AbortController();
    setResult(undefined);
    getPlatformAnalytics(projectId, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({projectId, data});
    }).catch(() => {
      if (!controller.signal.aborted) setResult({projectId, error: true});
    });
    return () => controller.abort();
  }, [projectId]);
  const current = result?.projectId === projectId ? result : undefined;
  const report = current?.data?.status === "verified" ? current.data.report : null;
  return <details className="context-disclosure platform-analytics">
    <summary>Native channel measurements</summary>
    {!current ? <p>Checking native reports…</p>
      : current.error ? <p>Native reports unavailable. No values are shown.</p>
      : !report ? <p>YouTube, Instagram, TikTok, X and Pinterest reports are not connected.</p>
      : <div className="platform-report-list">{report.platforms.map(entry => <section key={entry.platform} aria-label={`${labels[entry.platform]} analytics`}>
        <header><strong>{labels[entry.platform]}</strong><small>{entry.window.start_date}–{entry.window.end_date}</small></header>
        <dl>{entry.metrics.map(metric => <div key={metric.metric_id}><dt>{metric.label}</dt><dd aria-label={metric.value === null ? "Not measured" : undefined}>{valueLabel(metric.value, metric.unit)}</dd></div>)}</dl>
      </section>)}</div>}
    <p className="insights-limitation">Each platform keeps its own definitions and window. Values are not compared across platforms.</p>
  </details>;
}
