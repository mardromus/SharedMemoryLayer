export type MemoryType =
  | "knowledge"
  | "decision"
  | "learning"
  | "experience"
  | "context"
  | "task_result"
  | "incident"
  | "documentation"
  | "solution";

export type Visibility = "private" | "team" | "selected_teams" | "selected_agents" | "organization";
export type Sensitivity = "public" | "internal" | "confidential" | "restricted";
export type VerificationStatus = "unverified" | "verified" | "rejected";
export type ConflictStatus = "none" | "potential_conflict" | "resolved";

export const MEMORY_TYPES: MemoryType[] = [
  "knowledge", "decision", "learning", "experience", "context", "task_result", "incident", "documentation", "solution",
];
export const VISIBILITIES: Visibility[] = ["private", "team", "selected_teams", "selected_agents", "organization"];
export const SENSITIVITIES: Sensitivity[] = ["public", "internal", "confidential", "restricted"];

export interface Ref {
  id: string;
  name: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Me {
  id: string;
  kind: "user" | "agent";
  name: string;
  team_id: string | null;
  team_name: string | null;
  roles: string[];
  permissions: string[];
  email?: string | null;
  full_name?: string | null;
}

export interface Team {
  id: string;
  name: string;
  slug: string;
  description: string;
  default_visibility: Visibility;
  allow_cross_team_sharing: boolean;
  is_active: boolean;
  created_at: string;
  agent_count: number;
  member_count: number;
  memory_count: number;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  agent_type: string;
  team_id: string;
  team_name: string;
  status: "active" | "suspended" | "deleted";
  capabilities: string[];
  persona: string;
  roles: string[];
  permissions: string[];
  last_active_at: string | null;
  created_at: string;
  memories_created: number;
  api_keys: ApiKey[];
  api_key?: string | null;
}

export interface User {
  id: string;
  username: string;
  email: string | null;
  full_name: string;
  role: string;
  team_id: string | null;
  team_name: string | null;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Role {
  id: string;
  name: string;
  description: string;
  is_system: boolean;
  permissions: string[];
}

export interface Permission {
  id: string;
  code: string;
  description: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  team_id: string | null;
  created_at: string;
  memory_count: number;
}

export interface Memory {
  id: string;
  title: string;
  content: string;
  memory_type: MemoryType;
  team: Ref;
  creator_agent: Ref | null;
  creator_user: Ref | null;
  project: Ref | null;
  visibility: Visibility;
  sensitivity: Sensitivity;
  status: "active" | "archived";
  verification_status: VerificationStatus;
  conflict_status: ConflictStatus;
  tags: string[];
  importance: number;
  confidence: number;
  reliability: number;
  source: string;
  source_ref: string | null;
  metadata: Record<string, unknown> | null;
  expires_at: string | null;
  is_expired: boolean;
  embedding_id: string | null;
  embedding_model: string | null;
  parent_memory_id: string | null;
  version: number;
  access_count: number;
  reuse_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  allowed_teams: Ref[] | null;
  allowed_agents: Ref[] | null;
  can_edit: boolean;
  can_share: boolean;
}

export interface MemoryListItem {
  id: string;
  title: string;
  snippet: string;
  memory_type: MemoryType;
  team: Ref;
  creator: string;
  visibility: Visibility;
  sensitivity: Sensitivity;
  status: "active" | "archived";
  verification_status: VerificationStatus;
  conflict_status: ConflictStatus;
  tags: string[];
  confidence: number;
  importance: number;
  version: number;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DuplicateCandidate {
  memory_id: string;
  title: string;
  similarity: number;
  team: string;
  exact_match: boolean;
}

export interface ConflictCandidate {
  memory_id: string;
  title: string;
  similarity: number;
  reason: string;
  detection_method: string;
  conflict_id: string | null;
}

export interface MemoryWarnings {
  message: string | null;
  duplicates: DuplicateCandidate[];
  conflicts: ConflictCandidate[];
}

export interface MemoryCreateResponse {
  memory: Memory;
  warnings: MemoryWarnings;
}

export interface SearchResult {
  memory_id: string;
  title: string;
  content: string;
  similarity: number;
  score: number;
  team: string;
  team_id: string;
  creator: string;
  memory_type: MemoryType;
  confidence: number;
  importance: number;
  verification_status: VerificationStatus;
  version: number;
  tags: string[];
  visibility: Visibility;
  project: string | null;
  created_at: string;
  updated_at: string;
  cross_team: boolean;
  match?: "semantic" | "keyword" | "semantic+keyword";
}

export interface SearchResponse {
  query: string;
  mode: string;
  total: number;
  took_ms: number;
  threshold: number;
  results: SearchResult[];
}

export interface SearchRequest {
  query: string;
  top_k?: number;
  similarity_threshold?: number;
  memory_types?: MemoryType[];
  team_ids?: string[];
  project_ids?: string[];
  tags?: string[];
  visibility?: Visibility[];
  include_expired?: boolean;
  include_archived?: boolean;
}

export interface VersionAuthor {
  type: string;
  id: string | null;
  name: string;
}

export interface MemoryVersion {
  id: string;
  memory_id: string;
  version_number: number;
  previous_version_id: string | null;
  title: string;
  content: string;
  memory_type: string;
  visibility: string;
  tags: string[];
  importance: number;
  confidence: number;
  change_description: string;
  author: VersionAuthor;
  created_at: string;
  is_current: boolean;
}

export interface AccessEvent {
  timestamp: string;
  actor: string;
  actor_type: string;
  action: string;
  status: string;
  reason: string | null;
}

export interface Provenance {
  memory_id: string;
  title: string;
  created_by: VersionAuthor;
  team: Ref;
  created_at: string;
  source: string;
  source_ref: string | null;
  parent_memory_id: string | null;
  current_version: number;
  versions: MemoryVersion[];
  modified_by: string[];
  access_summary: Record<string, number>;
  access_history: AccessEvent[] | null;
}

export interface AuditLog {
  id: string;
  timestamp: string;
  actor_type: string;
  actor_id: string | null;
  actor_name: string;
  actor_team_id: string | null;
  on_behalf_of: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  status: "success" | "allowed" | "denied" | "failure";
  reason: string | null;
  ip_address: string | null;
  request_id: string | null;
  metadata: Record<string, unknown>;
}

export interface Conflict {
  id: string;
  memory: Ref;
  conflicting_memory: Ref;
  memory_content: string;
  conflicting_content: string;
  memory_team: string;
  conflicting_team: string;
  similarity: number;
  reason: string;
  detection_method: string;
  status: "open" | "resolved" | "dismissed";
  resolution: string | null;
  resolution_note: string;
  resolved_at: string | null;
  created_at: string;
}

export interface AgentStep {
  name: string;
  detail: string;
  took_ms: number;
}

export interface AgentTaskResponse {
  task_id: string;
  agent: string;
  team: string | null;
  task: string;
  answer: string;
  learning: string | null;
  provider: string;
  model: string | null;
  fallback_reason: string | null;
  memories_used: SearchResult[];
  reused_cross_team: number;
  stored_memory_id: string | null;
  store_warnings: MemoryWarnings | null;
  steps: AgentStep[];
  took_ms: number;
}

export interface AccessCheckResult {
  agent: string;
  agent_team: string;
  memory_id: string;
  decision: "ALLOWED" | "DENIED";
  reason: string;
  memory: Memory | null;
  audit_logged: boolean;
}

export interface HealthStatus {
  database: string;
  vector_db: string;
  embedding_model: { provider: string; model: string; loaded: boolean };
  llm: { provider: string; model: string | null; reachable: boolean };
}

export interface DashboardStats {
  generated_at: string;
  counts: Record<string, number>;
  retrieval_success_rate: number | null;
  cross_team_search_rate: number | null;
  avg_search_latency_ms_24h: number | null;
  memories_by_type: { memory_type: string; count: number }[];
  memories_by_team: { team: string; count: number }[];
  memories_by_visibility: { visibility: string; count: number }[];
  activity_7d: { date: string; memories_created: number; searches: number; denials: number }[];
  top_reused_memories: { id: string; title: string; team: string; reuse_count: number; access_count: number }[];
  recent_activity: {
    timestamp: string;
    actor: string;
    actor_type: string;
    action: string;
    resource_type: string | null;
    resource_id: string | null;
    status: string;
    reason: string | null;
  }[];
  health: HealthStatus;
}
