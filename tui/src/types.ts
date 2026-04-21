// Ticket-centric TUI types.
//
// The backend (jig.ticket_mcp) emits events on the internal bus, which the
// orchestrator mirrors to the WebSocket. The TUI sees four event kinds:
//
//   ticket_created   { kind, ticket_id, work_type, size, assignee?, parent_id? }
//   ticket_updated   { kind, ticket_id, status }
//   comment_posted   { kind, ticket_id, comment_id, author, content? }
//   commit_recorded  { kind, ticket_id, sha, message }
//
// ...and sends three commands:
//
//   create_ticket      { work_type, size, title, description?, assignee?, parent_id?, labels? }
//   comment_on_ticket  { ticket_id, content, kind? }
//   update_ticket      { ticket_id, status?, assignee? }

// Phase 1 schema. Matches jig/ticket.py's WorkType enum. Legacy values
// (bug/chore/task/question) are migrated by the backend before they
// reach the TUI.
export type WorkType =
  | "feature"
  | "bugfix"
  | "refactor"
  | "spike"
  | "perf"
  | "migration"
  | "docs"

// T-shirt sizing per doc 03. "m" is the default for new tickets.
export type Size = "xs" | "s" | "m" | "l" | "xl"

// Transitional alias — older call sites still import `TicketType` by name.
// Remove in the next cleanup pass.
export type TicketType = WorkType

export type TicketStatus =
  | "open"
  | "in_progress"
  | "blocked"
  | "needs_info"
  | "failed"
  | "resolved"
  | "closed"

export interface Ticket {
  id: string
  workType: WorkType
  size: Size
  status: TicketStatus
  title: string
  description: string
  assignee: string | null
  parentId: string | null
  dependsOn: string[] // ticket IDs that must resolve before this starts
  workflow: string // workflow name ("default" or "project")
  lastActivity: number // epoch ms; bumps on any related event
  currentPhase: string | null // active workflow phase name, null when idle
  phaseIndex: number | null // 0-based index of current phase
  totalPhases: number | null // total phases in workflow
}

// A jig event arrives on the wire as { type, data } — the backend's JigEvent.
// `type` matches the `kind` field inside `data`.
export interface JigEvent {
  type: string
  data: Record<string, unknown>
}

export interface AgentConfig {
  role: string
  phase_prompt: string
  response_prompt: string
  allowed_tools: string[]
  default_context: string[]
}

export interface WorkflowPhase {
  name: string
  role: string
  task_template: string
  acceptance_criteria: string
}

export interface Workflow {
  name: string
  phases: WorkflowPhase[]
}

export type ViewMode = "events" | "ticket" | "kanban" | "agents"

// Modal overlay state — null means no modal.
export type ModalState =
  | { kind: "new_ticket" }
  | { kind: "answer"; ticketId: string }
  | null

export interface AppState {
  connected: boolean
  tickets: Record<string, Ticket>
  events: JigEvent[]
  selectedTicketId: string | null
  modal: ModalState
  lastError: string | null
  viewMode: ViewMode
  agents: AgentConfig[]
  workflow: Workflow | null
  selectedAgentIdx: number
}

export const INITIAL_STATE: AppState = {
  connected: false,
  tickets: {},
  events: [],
  selectedTicketId: null,
  modal: null,
  lastError: null,
  viewMode: "events",
  agents: [],
  workflow: null,
  selectedAgentIdx: 0,
}
