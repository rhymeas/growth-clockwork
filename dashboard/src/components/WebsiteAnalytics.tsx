import { useEffect, useState } from "react";
import { getWebsiteAnalytics, type WebsiteAnalyticsResponse } from "../api/client";

const metricLabels: Array<[keyof NonNullable<WebsiteAnalyticsResponse["report"]>["metrics"], string]> = [
  ["active_users", "Active users"],
  ["engaged_sessions", "Engaged sessions"],
  ["download_clicks", "Download clicks"],
];

const setupMessages: Record<NonNullable<WebsiteAnalyticsResponse["setup"]>["state"], string> = {
  configuration_required: "GA4 setup needed: create the project property and add its private local configuration.",
  disabled: "GA4 configuration found, but its local connector is switched off.",
  authentication_required: "GA4 configuration ready; a read-only OAuth session is still needed.",
  ready: "GA4 connection ready. Run the local report fetch to load current values.",
};

export function WebsiteAnalytics({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<{ projectId: string; data?: WebsiteAnalyticsResponse; error?: boolean }>();
  useEffect(() => {
    const controller = new AbortController();
    setResult(undefined);
    getWebsiteAnalytics(projectId, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ projectId, data });
    }).catch(() => {
      if (!controller.signal.aborted) setResult({ projectId, error: true });
    });
    return () => controller.abort();
  }, [projectId]);
  const current = result?.projectId === projectId ? result : undefined;
  const report = current?.data?.status === "verified" ? current.data.report : null;
  return <section className="website-analytics" aria-label="Website analytics" aria-live="polite">
    <dl className="analytics-boxes">
      {metricLabels.map(([key, label]) => <div className="analytics-box" key={key}>
        <dt>{label}</dt><dd aria-label={report?.metrics[key] == null ? "Not measured" : undefined}>{report?.metrics[key] ?? "—"}</dd>
      </div>)}
    </dl>
    {!current ? <p className="analytics-source">Checking GA4 report…</p>
      : current.error ? <p className="analytics-source">GA4 report unavailable. No values are shown.</p>
      : !report ? <p className="analytics-source">{current.data?.setup ? setupMessages[current.data.setup.state] : "GA4 report not connected. Tracking and download-event verification are still open."}</p>
      : <p className="analytics-source">GA4 · {report.window.start_date}–{report.window.end_date} · {report.window.timezone} · fetched {report.fetched_at}</p>}
  </section>;
}
