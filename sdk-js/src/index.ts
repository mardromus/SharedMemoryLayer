/**
 * TypeScript / JavaScript client for the Shared Memory Layer.
 * Zero dependencies: uses the platform `fetch` (Node 18+, Deno, Bun, browsers).
 *
 *   const memory = new SharedMemoryClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SML_API_KEY });
 *   const { results } = await memory.search("authentication failures caused by expired tokens");
 */

export type MemoryType =
  | "knowledge" | "decision" | "learning" | "experience" | "context"
  | "task_result" | "incident" | "documentation" | "solution";
export type Visibility = "private" | "team" | "selected_teams" | "selected_agents" | "organization";
export type SearchMode = "hybrid" | "semantic" | "keyword";

export interface Ref { id: string; name: string }

export interface Memory {
  id: string;
  title: string;
  content: string;
  memory_type: MemoryType;
  team: Ref;
  creator_agent: Ref | null;
  creator_user: Ref | null;
  visibility: Visibility;
  tags: string[];
  version: number;
  confidence: number;
  importance: number;
  verification_status: string;
  created_at: string;
  updated_at: string;
  [key: string]: unknown;
}

export interface Warnings {
  message: string | null;
  duplicates: { memory_id: string; title: string; similarity: number; team: string; exact_match: boolean }[];
  conflicts: { memory_id: string; title: string; similarity: number; reason: string; detection_method: string }[];
}

export interface SearchResult {
  memory_id: string;
  title: string;
  content: string;
  similarity: number;
  score: number;
  team: string;
  memory_type: MemoryType;
  confidence: number;
  version: number;
  tags: string[];
  cross_team: boolean;
  match: "semantic" | "keyword" | "semantic+keyword";
  [key: string]: unknown;
}

export interface SearchResponse { query: string; mode: string; total: number; took_ms: number; results: SearchResult[] }

export interface RememberInput {
  title: string;
  content: string;
  memoryType?: MemoryType;
  tags?: string[];
  visibility?: Visibility;
  projectId?: string;
  importance?: number;
  confidence?: number;
  sourceRef?: string;
  metadata?: Record<string, unknown>;
  expiresAt?: string | Date;
  allowedTeamIds?: string[];
  allowedAgentIds?: string[];
  rejectOnDuplicate?: boolean;
}

export interface SearchOptions {
  topK?: number;
  mode?: SearchMode;
  similarityThreshold?: number;
  memoryTypes?: MemoryType[];
  teamIds?: string[];
  projectIds?: string[];
  tags?: string[];
  includeExpired?: boolean;
}

export interface ClientOptions {
  baseUrl: string;
  apiKey?: string;
  accessToken?: string;
  maxRetries?: number;
  timeoutMs?: number;
  fetch?: typeof fetch;
}

export class SharedMemoryError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
    public readonly requestId?: string,
    public readonly details?: unknown,
  ) {
    super(`[${status}] ${message}`);
    this.name = "SharedMemoryError";
  }
  get isPermissionDenied(): boolean { return this.status === 403; }
  get isNotFound(): boolean { return this.status === 404; }
}

const RETRYABLE = new Set([429, 502, 503, 504]);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const clean = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));

export class SharedMemoryClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly maxRetries: number;
  private readonly timeoutMs: number;

  constructor(private readonly options: ClientOptions) {
    if (!options.apiKey && !options.accessToken) throw new Error("SharedMemoryClient needs apiKey or accessToken");
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.maxRetries = options.maxRetries ?? 3;
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.options.accessToken) headers.Authorization = `Bearer ${this.options.accessToken}`;
    else if (this.options.apiKey) headers["X-API-Key"] = this.options.apiKey;

    for (let attempt = 0; ; attempt++) {
      let res: Response;
      try {
        res = await this.fetchImpl(this.baseUrl + path, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (err) {
        if (attempt < this.maxRetries) { await sleep(250 * 2 ** attempt); continue; }
        throw new SharedMemoryError(0, `Could not reach Shared Memory API: ${(err as Error).message}`);
      }
      if (RETRYABLE.has(res.status) && attempt < this.maxRetries) {
        const retryAfter = Number(res.headers.get("Retry-After") ?? "0");
        await sleep(res.status === 429 ? Math.min(retryAfter || 1, 30) * 1000 : 250 * 2 ** attempt);
        continue;
      }
      if (!res.ok) {
        let err: { message?: string; code?: string; request_id?: string; details?: unknown } = {};
        try { err = ((await res.json()) as { error?: typeof err }).error ?? {}; } catch { /* non-JSON */ }
        throw new SharedMemoryError(res.status, err.message ?? res.statusText, err.code, err.request_id, err.details);
      }
      if (res.status === 204) return undefined as T;
      return (await res.json()) as T;
    }
  }

  whoami() {
    return this.request<{ id: string; kind: string; name: string; team_name: string | null; roles: string[]; permissions: string[] }>("GET", "/api/auth/me");
  }

  remember(input: RememberInput) {
    return this.request<{ memory: Memory; warnings: Warnings }>("POST", "/api/memories", clean({
      title: input.title,
      content: input.content,
      memory_type: input.memoryType,
      tags: input.tags,
      visibility: input.visibility,
      project_id: input.projectId,
      importance: input.importance,
      confidence: input.confidence,
      source_ref: input.sourceRef,
      metadata: input.metadata,
      expires_at: input.expiresAt instanceof Date ? input.expiresAt.toISOString() : input.expiresAt,
      allowed_team_ids: input.allowedTeamIds,
      allowed_agent_ids: input.allowedAgentIds,
      reject_on_duplicate: input.rejectOnDuplicate,
    }));
  }

  async rememberMany(items: Record<string, unknown>[], opts: { skipDuplicates?: boolean; detectDuplicates?: boolean } = {}) {
    const total = { created: 0, skipped: 0, failed: 0, results: [] as { index: number; status: string; memory_id?: string; error?: unknown }[] };
    for (let start = 0; start < items.length; start += 100) {
      const res = await this.request<typeof total>("POST", "/api/memories/bulk", clean({
        items: items.slice(start, start + 100),
        skip_duplicates: opts.skipDuplicates,
        detect_duplicates: opts.detectDuplicates,
      }));
      total.created += res.created;
      total.skipped += res.skipped;
      total.failed += res.failed;
      total.results.push(...res.results.map((r) => ({ ...r, index: r.index + start })));
    }
    return total;
  }

  search(query: string, opts: SearchOptions = {}) {
    return this.request<SearchResponse>("POST", "/api/memories/search", clean({
      query,
      top_k: opts.topK,
      mode: opts.mode,
      similarity_threshold: opts.similarityThreshold,
      memory_types: opts.memoryTypes,
      team_ids: opts.teamIds,
      project_ids: opts.projectIds,
      tags: opts.tags,
      include_expired: opts.includeExpired,
    }));
  }

  get(memoryId: string) {
    return this.request<Memory>("GET", `/api/memories/${encodeURIComponent(memoryId)}`);
  }

  update(memoryId: string, fields: Partial<Pick<RememberInput, "title" | "content" | "tags" | "visibility" | "importance" | "confidence">> & { memoryType?: MemoryType }, opts: { changeDescription?: string; expectedVersion?: number } = {}) {
    return this.request<{ memory: Memory; warnings: Warnings }>("PATCH", `/api/memories/${encodeURIComponent(memoryId)}`, clean({
      title: fields.title,
      content: fields.content,
      tags: fields.tags,
      visibility: fields.visibility,
      importance: fields.importance,
      confidence: fields.confidence,
      memory_type: fields.memoryType,
      change_description: opts.changeDescription,
      expected_version: opts.expectedVersion,
    }));
  }

  share(memoryId: string, targets: { teamIds?: string[]; agentIds?: string[] }) {
    return this.request<{ memory_id: string; allowed_teams: Ref[]; allowed_agents: Ref[] }>("POST", `/api/memories/${encodeURIComponent(memoryId)}/share`, {
      team_ids: targets.teamIds ?? [],
      agent_ids: targets.agentIds ?? [],
    });
  }

  unshare(memoryId: string, target: string) {
    return this.request<{ memory_id: string }>("DELETE", `/api/memories/${encodeURIComponent(memoryId)}/share/${encodeURIComponent(target)}`);
  }

  versions(memoryId: string) {
    return this.request<{ version_number: number; content: string; change_description: string; created_at: string }[]>("GET", `/api/memories/${encodeURIComponent(memoryId)}/versions`);
  }

  runTask(task: string, opts: { topK?: number; storeResult?: boolean } = {}) {
    return this.request<{ answer: string; provider: string; memories_used: SearchResult[] }>("POST", "/api/agent-runtime/tasks", clean({
      task,
      top_k: opts.topK,
      store_result: opts.storeResult,
    }));
  }
}

export default SharedMemoryClient;
