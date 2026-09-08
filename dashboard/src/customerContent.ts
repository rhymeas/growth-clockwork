export interface CustomerSection {
  id: string;
  title: string;
  paragraphs: string[];
  bullets: string[];
}

const TECHNICAL_KEYS = new Set([
  "action",
  "action_id",
  "artifact_id",
  "artifact_path",
  "artifact_ref",
  "artifact_revision",
  "artifact_sha256",
  "contract_id",
  "created_at",
  "evidence",
  "evidence_refs",
  "hash",
  "id",
  "project_id",
  "project_profile_revision",
  "quality_checks",
  "recorded_at",
  "review_item_version",
  "route_id",
  "run_id",
  "schema",
  "schema_version",
  "sha256",
  "source_id",
  "source_pointers",
  "status",
  "task_id",
  "updated_at",
  "work_item_id",
]);

const LABELS: Record<string, string> = {
  audience: "Audience",
  conclusion: "Conclusion",
  contradictions: "What conflicts",
  decision: "Decision",
  decision_question: "Decision question",
  findings: "Findings",
  hypothesis: "Hypothesis",
  insights: "Insights",
  limitations: "Limits",
  method: "Method",
  next_steps: "Next steps",
  open_questions: "Open questions",
  options: "Options considered",
  outcome: "Outcome",
  recommendation: "Recommendation",
  recommendations: "Recommendations",
  scope: "Scope",
  strategy: "Strategy",
  summary: "Summary",
  unknowns: "Still unknown",
};

function titleCase(value: string) {
  return value
    .replaceAll(/[_-]+/g, " ")
    .replaceAll(/\b\w/g, (letter) => letter.toUpperCase());
}

function isTechnicalString(value: string) {
  const trimmed = value.trim();
  return (
    !trimmed ||
    /^[{\[}\]]/.test(trimmed) ||
    /^"?[a-z][\w-]*"?\s*:\s*/i.test(trimmed) ||
    /^[-/\w.]+\.(?:json|yaml|yml|ts|tsx|js|py|md)$/i.test(trimmed) ||
    /^[0-9a-f]{32,}$/i.test(trimmed) ||
    /\b(?:sha(?:-?256)?|contract[_ -]?id|project[_ -]?id|artifact[_ -]?(?:id|path|ref|revision)|run[_ -]?id|schema)\b/i.test(trimmed) ||
    /^(?:const|let|var|function|import|export|class|interface|type)\b/.test(trimmed)
  );
}

function cleanText(value: string) {
  const withoutExecutableBlocks = value
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, "");
  const withoutFencedCode = withoutExecutableBlocks.replace(/```[\s\S]*?```/g, "");
  const withoutInlineCode = withoutFencedCode.replace(/`[^`]*`/g, "");
  const cleaned = withoutInlineCode
    .replace(/<\/?[a-z][^>]*>/gi, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  return isTechnicalString(cleaned) ? "" : cleaned;
}

function valueToText(value: unknown): string {
  if (typeof value === "string") return cleanText(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function objectToText(value: Record<string, unknown>) {
  const pieces: string[] = [];
  const identity = valueToText(value.option ?? value.title ?? value.name);
  const detail = valueToText(
    value.assessment ??
      value.summary ??
      value.description ??
      value.finding ??
      value.recommendation ??
      value.conclusion ??
      value.detail ??
      value.reason ??
      value.value ??
      value.text,
  );

  if (identity && detail && identity !== detail) pieces.push(`${identity} — ${detail}`);
  else if (identity || detail) pieces.push(identity || detail);

  const confidence = valueToText(value.confidence);
  if (confidence && pieces.length) pieces[0] = `${pieces[0]} (${confidence} confidence)`;
  return pieces[0] ?? "";
}

function unique(values: string[]) {
  return [...new Set(values.filter(Boolean))];
}

function section(id: string, title: string, paragraphs: string[] = [], bullets: string[] = []): CustomerSection | null {
  const cleanedParagraphs = unique(paragraphs.map(cleanText));
  const cleanedBullets = unique(bullets.map(cleanText));
  if (!cleanedParagraphs.length && !cleanedBullets.length) return null;
  return { id, title, paragraphs: cleanedParagraphs, bullets: cleanedBullets };
}

function sectionsFromJson(value: unknown): CustomerSection[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const object = value as Record<string, unknown>;
  if (object.deliverable_version === "1.0" && object.payload && typeof object.payload === "object") {
    return sectionsFromJson(object.payload);
  }
  const output: CustomerSection[] = [];

  for (const [key, raw] of Object.entries(object)) {
    if (TECHNICAL_KEYS.has(key) || key === "proof_boundary") continue;
    const title = LABELS[key] ?? titleCase(key);

    if (typeof raw === "string" || typeof raw === "number" || typeof raw === "boolean") {
      const item = section(key, title, [valueToText(raw)]);
      if (item) output.push(item);
      continue;
    }

    if (Array.isArray(raw)) {
      const bullets = raw.flatMap((item) => {
        const simple = valueToText(item);
        if (simple) return [simple];
        if (item && typeof item === "object") {
          const line = objectToText(item as Record<string, unknown>);
          return line ? [line] : [];
        }
        return [];
      });
      const item = section(key, title, [], bullets);
      if (item) output.push(item);
      continue;
    }

    if (raw && typeof raw === "object") {
      const nested = raw as Record<string, unknown>;
      if (Array.isArray(nested.sections)) {
        for (const nestedSection of nested.sections) {
          if (!nestedSection || typeof nestedSection !== "object") continue;
          const value = nestedSection as Record<string, unknown>;
          const nestedTitle = valueToText(value.title) || title;
          const paragraphs = Array.isArray(value.paragraphs)
            ? value.paragraphs.map(valueToText)
            : [valueToText(value.summary ?? value.description ?? value.text)];
          const bullets = Array.isArray(value.bullets) ? value.bullets.map(valueToText) : [];
          const item = section(`${key}-${output.length}`, nestedTitle, paragraphs, bullets);
          if (item) output.push(item);
        }
      } else {
        const line = objectToText(nested);
        const item = section(key, title, line ? [line] : []);
        if (item) output.push(item);
      }
    }
  }

  return output;
}

function sectionFromLines(title: string, lines: string[], index: number): CustomerSection | null {
  const paragraphs: string[] = [];
  const bullets: string[] = [];
  let paragraphLines: string[] = [];

  const flushParagraph = () => {
    const paragraph = cleanText(paragraphLines.join(" "));
    if (paragraph) paragraphs.push(paragraph);
    paragraphLines = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      continue;
    }
    const bullet = trimmed.match(/^[-*•]\s+(.+)/);
    if (bullet) {
      flushParagraph();
      const copy = cleanText(bullet[1]);
      if (copy) bullets.push(copy);
      continue;
    }
    if (!isTechnicalString(trimmed)) paragraphLines.push(trimmed.replace(/^>\s?/, ""));
  }
  flushParagraph();
  return section(`text-${index}`, title, paragraphs, bullets);
}

function sectionsFromText(content: string): CustomerSection[] {
  const prepared = content
    .replace(/^---[\s\S]*?---\s*/m, "")
    .replace(/```[\s\S]*?```/g, "")
    .replace(/\r\n/g, "\n");
  const lines = prepared.split("\n");
  const output: CustomerSection[] = [];
  let currentTitle = "Overview";
  let currentLines: string[] = [];

  const flush = () => {
    const item = sectionFromLines(currentTitle, currentLines, output.length);
    if (item) output.push(item);
    currentLines = [];
  };

  for (const [index, line] of lines.entries()) {
    const nextLine = lines[index + 1]?.trim() ?? "";
    const standaloneHeading =
      /^(?:overview|decision|hypothesis|method|findings|recommendations?|conclusion|limits?|next steps?|unknowns?|open questions?|experiment design|success criteria)$/i.test(
        line.trim(),
      ) && nextLine.length > 0;
    const heading =
      line.match(/^#{1,6}\s+(.+)$/) ??
      line.match(/^([A-Za-z][A-Za-z0-9 /&'’-]{1,70}):\s*$/) ??
      (standaloneHeading ? [line, line.trim()] : null);
    if (heading) {
      flush();
      currentTitle = cleanText(heading[1]) || "Overview";
      continue;
    }
    currentLines.push(line);
  }
  flush();
  return output;
}

/**
 * Derives a safe, customer-readable view from immutable UTF-8 artifact bytes.
 * Raw JSON, source paths, hashes, and code are deliberately never returned.
 */
export function customerSections(content: string): CustomerSection[] {
  const trimmed = content.trim();
  if (!trimmed) return [];

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    const fromJson = sectionsFromJson(parsed);
    if (fromJson.length) return fromJson;
    return [];
  } catch {
    return sectionsFromText(trimmed);
  }
}

export function customerText(value: string) {
  return cleanText(value);
}
