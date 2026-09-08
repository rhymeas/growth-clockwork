import { useEffect, useState } from "react";
import { getResearchFeeds, type ResearchFeedsResponse } from "../api/client";
import { LessonContent } from "./LessonContent";

export function ResearchFeeds({ projectId }: { projectId: string }) {
  const [data, setData] = useState<ResearchFeedsResponse | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    setData(null);
    setError(false);
    void getResearchFeeds(projectId).then((result) => {
      if (active) setData(result);
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [projectId]);
  const result = data?.result;
  const synthesis = data?.synthesis;
  const analysis = synthesis?.analysis;
  const markdown = analysis?.artifact.replace(/\[source:([^\]]+)\]/g, (marker, id: string) => {
    const source = synthesis?.sources?.find((item) => item.id === id);
    return source && /^https?:\/\//.test(source.url) ? `[Quelle](${source.url})` : marker;
  }).replace(/^(#{2,6} .+)$/gm, "$1\n");
  return <section className="project-section feed-intake" aria-labelledby="feed-intake-title">
    <span className="section-eyebrow">Discovery · not validated evidence</span>
    <h2 id="feed-intake-title">Collected sources</h2>
    {error ? <p role="status">Saved research is unavailable. Reopen research to retry.</p>
      : !data ? <p role="status">Loading saved research…</p>
      : !result ? <p>No feed collection saved for this brief this week.</p>
      : <>
        <section aria-label="AI research analysis">
          <h3>KI-Auswertung</h3>
          {analysis ? <>
            <p>Gespeicherte Analyse · {synthesis?.model} · keine bestätigte Zielgruppen-Nachfrage</p>
            <h4>Nächster sinnvoller Schritt</h4>
            <p>{analysis.next_decision}</p>
            <details><summary>Offene Fragen <span>· {analysis.uncertainties.length}</span></summary>
              <ul>{analysis.uncertainties.map((item) => <li key={item}>{item}</li>)}</ul>
            </details>
            <details><summary>Vollständige Auswertung lesen</summary>
              <LessonContent markdown={markdown ?? ""} />
            </details>
          </> : <p>{!synthesis ? "Noch keine KI-Auswertung für diesen Stand gespeichert."
            : synthesis.status === "claimed" ? "Auswertungsversuch gespeichert; noch kein Ergebnis. Kein automatischer Neustart."
            : "Keine vollständige Auswertung verfügbar. Es wird kein neuer Aufruf gestartet."}</p>}
        </section>
        <p>{result.item_count} items · {data.week} · {result.status === "collected" ? "All feeds reached" : "Some feeds unavailable"}</p>
        <p>Collected {new Date(result.fetched_at).toLocaleString()}. Topics to investigate, not confirmed audience needs or approved product claims.</p>
        {result.sources.map((source) => <details key={source.feed_url}>
          <summary>{source.publisher} <span>· {source.status === "ok" ? `${source.items.length} items` : "Unavailable"}</span></summary>
          {source.status === "ok" && <p>Limited recent sample; not a complete archive.</p>}
          <ul>{source.items.map((item) => <li key={item.url}>
            <a href={/^https?:\/\//i.test(item.url) ? item.url : undefined} target="_blank" rel="noopener noreferrer">{item.title}</a>
            {item.published && <p>{item.published}</p>}
            <p>{item.excerpt}</p>
          </li>)}</ul>
        </details>)}
      </>}
  </section>;
}
