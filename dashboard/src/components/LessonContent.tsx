import type { ReactNode } from "react";

/** Editorial Markdown only. React escapes text; HTML and code are not rendered. */
function inline(text: string): ReactNode[] {
  return text.split(/(\[[^\]]+\]\(https?:\/\/[^\s)]+\)|\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, index) => {
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noopener noreferrer">{link[1]}</a>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*")) return <em key={index}>{part.slice(1, -1)}</em>;
    return part;
  });
}

export function lessonMarkdown(content: string): string | null {
  try {
    const value = JSON.parse(content);
    const markdown = value?.payload?.complete_content?.markdown;
    return value?.contract_id === "channel-product@1" && typeof markdown === "string" && markdown.trim()
      ? markdown : null;
  } catch { return null; }
}

export function LessonContent({ markdown }: { markdown: string }) {
  const clean = markdown.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, "")
    .replace(/```[\s\S]*?```|`[^`]*`/g, "").replace(/<[^>]*>/g, "");
  const blocks = clean.trim().split(/\n\s*\n/);
  return <div className="lesson-content">{blocks.map((block, index) => {
    const lines = block.trim().split("\n");
    // The reviewed artifact title is already the page's single h1.
    if (/^# /.test(block)) return null;
    if (/^#{2,6} /.test(block)) return <h2 key={index}>{inline(block.replace(/^#+ /, ""))}</h2>;
    if (lines.length > 2 && /^\|?\s*:?-{3}/.test(lines[1])) {
      const cells = (line: string) => line.replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
      return <div className="lesson-table" key={index} tabIndex={0} role="region" aria-label="Claim comparison">
        <table><thead><tr>{cells(lines[0]).map((cell, i) => <th scope="col" key={i}>{inline(cell)}</th>)}</tr></thead>
          <tbody>{lines.slice(2).map((line, row) => <tr key={row}>{cells(line).map((cell, i) => <td key={i}>{inline(cell)}</td>)}</tr>)}</tbody></table>
      </div>;
    }
    if (lines.every(line => /^\d+\. /.test(line))) return <ol key={index}>{lines.map((line, i) => <li key={i}>{inline(line.replace(/^\d+\. /, ""))}</li>)}</ol>;
    if (lines.every(line => /^[-*] /.test(line))) return <ul key={index}>{lines.map((line, i) => <li key={i}>{inline(line.slice(2))}</li>)}</ul>;
    if (/^> /.test(block)) return <blockquote key={index}>{inline(block.replace(/^> ?/gm, ""))}</blockquote>;
    return <p key={index}>{inline(block)}</p>;
  })}</div>;
}
