import { useEffect, useState } from "react";
import { getBrokerStatus, type BrokerStatusResponse } from "../api/client";

export function BrokerStatus({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState<{ project: string; data?: BrokerStatusResponse; error?: boolean }>();
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setResult(undefined);
    getBrokerStatus(projectId, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ project: projectId, data });
    }).catch(() => {
      if (!controller.signal.aborted) setResult({ project: projectId, error: true });
    });
    return () => controller.abort();
  }, [projectId, open, refresh]);
  useEffect(() => {
    const update = () => setRefresh(value => value + 1);
    window.addEventListener("growth-broker-changed", update);
    return () => window.removeEventListener("growth-broker-changed", update);
  }, []);
  const current = result?.project === projectId ? result : undefined;
  const visibleTasks = current?.data?.tasks?.filter(task =>
    task.agent_id === "publisher" || !["completed", "cancelled"].includes(task.status),
  ).slice(0, 8) ?? [];
  const taskLabel = (agent: string) => agent === "publisher" ? "Publisher" : agent.replaceAll("-", " ");
  const taskStatus = (status: string) => status === "awaiting_review" ? "Needs review"
    : status === "failed" ? "Needs attention" : status[0].toUpperCase() + status.slice(1);
  return <details className="context-disclosure" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Background tasks</summary>
    {open && <section aria-label="Background task status" aria-live="polite">
      {!current ? <p>Checking task status…</p>
        : current.error ? <p>Task status unavailable. The broker may not be connected.</p>
        : current.data?.state === "not_configured" ? <p>Task broker not connected. Existing project workflows are unchanged.</p>
        : <><dl className="insights-counts">
          {[["Queued", "queued"], ["Running", "running"], ["Needs review", "awaiting_review"], ["Failed", "failed"]].map(([label, key]) =>
            <div key={key}><dt>{label}</dt><dd>{current.data?.counts[key] ?? 0}</dd></div>)}
        </dl>
        {visibleTasks.length > 0 && <div className="broker-task-list" aria-label="Recent actionable tasks">
          {visibleTasks.map(task => <article className={`broker-task broker-task-${task.status}`} key={task.task_id}>
            <header><strong>{taskLabel(task.agent_id)}</strong><span>{taskStatus(task.status)}</span></header>
            {task.agent_id === "publisher" && task.status === "failed"
              ? <p>Reconciliation required. Check Postiz and platform history before any retry; the system will not risk a duplicate post.</p>
              : task.agent_id === "publisher" && task.status === "completed"
                ? <p>Delivery receipt saved. Native-platform live proof remains separate.</p>
                : <p>{task.status === "queued" ? "Waiting for its local worker." : task.status === "running" ? "Worker currently owns this task." : "Open the related content revision for the next decision."}</p>}
            <small>Attempt {task.attempt} of {task.max_attempts}</small>
          </article>)}
        </div>}
        <p className="insights-limitation">Task status at last check. Not audience growth or proof of publication.</p></>}
      <button className="text-button" disabled={!current} onClick={() => setRefresh(value => value + 1)}>Refresh task status</button>
    </section>}
  </details>;
}
