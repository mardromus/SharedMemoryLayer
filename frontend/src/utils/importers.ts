/** Client-side twins of shared_memory.importers: turn existing docs into memory items. */

export interface ImportItem {
  title: string;
  content: string;
  memory_type: string;
  tags: string[];
  source: string;
  source_ref: string;
  visibility?: string;
  team_id?: string;
  project_id?: string;
}

const MAX_CONTENT = 19000;
const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "section";
export const normaliseTag = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50);

function chunks(text: string): string[] {
  if (text.length <= MAX_CONTENT) return [text];
  const parts: string[] = [];
  let current = "";
  for (const para of text.split(/\n\s*\n/)) {
    if (current.length + para.length + 2 > MAX_CONTENT && current) {
      parts.push(current.trim());
      current = "";
    }
    let p = para;
    while (p.length > MAX_CONTENT) {
      parts.push(p.slice(0, MAX_CONTENT));
      p = p.slice(MAX_CONTENT);
    }
    current += p + "\n\n";
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

export function fromMarkdown(text: string, fileName: string, opts: { split: "heading" | "file"; memoryType: string; tags: string[] }): ImportItem[] {
  const clean = text.replace(/\r\n/g, "\n").replace(/^---\n[\s\S]*?\n---\n/, "").trim();
  const lines = clean.split("\n");
  let docTitle = fileName.replace(/\.[^.]+$/, "").replace(/[-_]/g, " ").trim();
  docTitle = docTitle.charAt(0).toUpperCase() + docTitle.slice(1);
  const h1 = lines.map((l) => HEADING.exec(l)).find((m) => m && m[1].length === 1);
  if (h1) docTitle = h1[2].trim();

  const sections: [string, string][] = [];
  if (opts.split === "file") {
    sections.push([docTitle, clean]);
  } else {
    let heading: string | null = null;
    let buf: string[] = [];
    for (const line of lines) {
      const m = HEADING.exec(line);
      if (m && m[1].length === 1) continue;
      if (m && m[1].length === 2) {
        if (buf.length) sections.push([heading ?? docTitle, buf.join("\n").trim()]);
        heading = m[2].trim();
        buf = [];
      } else buf.push(line);
    }
    if (buf.length) sections.push([heading ?? docTitle, buf.join("\n").trim()]);
  }

  const items: ImportItem[] = [];
  for (const [heading, body] of sections) {
    if (body.length < 40) continue;
    const base = heading === docTitle ? heading : `${docTitle} — ${heading}`;
    const parts = chunks(body);
    parts.forEach((part, i) =>
      items.push({
        title: (parts.length > 1 ? `${base} (part ${i + 1})` : base).slice(0, 200),
        content: part,
        memory_type: opts.memoryType,
        tags: opts.tags,
        source: "import",
        source_ref: `${fileName}#${slug(heading)}`.slice(0, 255),
      }),
    );
  }
  return items;
}

function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((v) => v !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.some((v) => v !== "")) rows.push(row);
  const [header, ...body] = rows;
  if (!header) return [];
  return body.map((r) => Object.fromEntries(header.map((h, i) => [h.trim(), r[i] ?? ""])));
}

function fromRecords(records: unknown[], fileName: string, opts: { memoryType: string; tags: string[] }): ImportItem[] {
  const items: ImportItem[] = [];
  records.forEach((raw, i) => {
    if (!raw || typeof raw !== "object") return;
    const rec = raw as Record<string, unknown>;
    if (!rec.title || !rec.content) return;
    const tags = Array.isArray(rec.tags) ? (rec.tags as string[]) : typeof rec.tags === "string" ? rec.tags.split(/[;,]/) : [];
    items.push({
      ...(rec as object),
      title: String(rec.title).slice(0, 200),
      content: String(rec.content),
      memory_type: (rec.memory_type as string) || opts.memoryType,
      tags: [...new Set([...tags.map(normaliseTag), ...opts.tags].filter(Boolean))],
      source: "import",
      source_ref: `${fileName}#${i + 1}`,
    } as ImportItem);
  });
  return items;
}

export async function parseFile(file: File, opts: { split: "heading" | "file"; memoryType: string; tags: string[] }): Promise<ImportItem[]> {
  const text = await file.text();
  const name = file.name.toLowerCase();
  if (name.endsWith(".json")) {
    const data = JSON.parse(text);
    const list = Array.isArray(data) ? data : data.memories ?? data.items ?? [data];
    return fromRecords(list, file.name, opts);
  }
  if (name.endsWith(".csv")) return fromRecords(parseCsv(text), file.name, opts);
  return fromMarkdown(text, file.name, opts);
}
