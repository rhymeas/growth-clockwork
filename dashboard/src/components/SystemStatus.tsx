import { useEffect, useState } from "react";
import { getSetupStatus, type SetupStatusResponse } from "../api/client";

const publicationLabel: Record<SetupStatusResponse["publication"]["mode"], string> = {
  not_configured: "Not configured",
  off: "Off",
  review: "Review",
  automatic: "Automatic",
};

function automationDetail(data: SetupStatusResponse) {
  if (data.autostart.state === "running" && data.research_schedule.state === "running") return "Login start · weekly research";
  if (data.autostart.state === "running") return "Starts after Mac login · research schedule off";
  if (data.autostart.state === "installed_not_running" || data.autostart.state === "needs_attention") return "Startup needs attention";
  if (data.autostart.state === "build_required") return "Build needed before startup";
  if (data.autostart.state === "unsupported") return "Login startup unavailable";
  return "Manual after Mac restart";
}

export function SystemStatus({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<SetupStatusResponse | "error" | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getSetupStatus(projectId, controller.signal).then(value => {
      if (!controller.signal.aborted) setResult(value);
    }).catch(() => {
      if (!controller.signal.aborted) setResult("error");
    });
    return () => controller.abort();
  }, [projectId]);
  if (!result) return <section className="system-status" aria-label="System status" aria-live="polite"><p className="analytics-source">Checking local system…</p></section>;
  if (result === "error") return <section className="system-status" aria-label="System status" aria-live="polite"><p className="analytics-source">System status unavailable.</p></section>;
  const access = result.remote_access.state === "configured" ? "Private devices"
    : result.remote_access.state === "needs_attention" ? "Needs attention" : "This Mac";
  return <section className="system-status" aria-label="System status" aria-live="polite">
    <dl className="analytics-boxes">
      <div className="analytics-box"><dt>Automation<small>{automationDetail(result)}</small></dt><dd>{result.background === "active" ? "Active" : "Manual"}</dd></div>
      <div className="analytics-box"><dt>Access<small>Mac must stay awake</small></dt><dd>{access}</dd></div>
      <div className="analytics-box"><dt>Publishing<small>{result.publication.publisher === "enabled" ? "Connector enabled" : "Connector disabled"}</small></dt><dd>{publicationLabel[result.publication.mode]}</dd></div>
    </dl>
  </section>;
}
