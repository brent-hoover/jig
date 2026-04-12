// Ticket-centric TUI types.
//
// The backend (jig.ticket_mcp) emits events on the internal bus, which the
// orchestrator mirrors to the WebSocket. The TUI sees four event kinds:
//
//   ticket_created   { kind, ticket_id, type?, assignee?, parent_id? }
//   ticket_updated   { kind, ticket_id, status }
//   comment_posted   { kind, ticket_id, comment_id, author, content? }
//   commit_recorded  { kind, ticket_id, sha, message }
//
// ...and sends three commands:
//
//   create_ticket      { type, title, description?, assignee?, parent_id?, labels? }
//   comment_on_ticket  { ticket_id, content, kind? }
//   update_ticket      { ticket_id, status?, assignee? }

export type TicketType =
  | "feature"
  | "bug"
  | "chore"
  | "task"
  | "question"

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
  type: TicketType
  status: TicketStatus
  title: string
  assignee: string | null
  parentId: string | null
  lastActivity: number // epoch ms; bumps on any related event
}

// A jig event arrives on the wire as { type, data } — the backend's JigEvent.
// `type` matches the `kind` field inside `data`.
export interface JigEvent {
  type: string
  data: Record<string, unknown>
}

// Modal overlay state — null means no modal.
export type ModalState =
  | { kind: "new_ticket" }
  | { kind: "answer"; ticketId: string }
  | null

export interface AppState {
  connected: boolean
  // Ticket map keyed by id. Tickets only get a title when the client sees a
  // ticket_created event *and* has been told the title — since ticket_created
  // payloads don't include the title, the TUI uses the title from the form it
  // submitted, falling back to the ticket_id until a smarter fetch exists.
  tickets: Record<string, Ticket>
  events: JigEvent[]
  selectedTicketId: string | null
  modal: ModalState
  lastError: string | null
}

export const INITIAL_STATE: AppState = {
  connected: false,
  tickets: {},
  events: [],
  selectedTicketId: null,
  modal: null,
  lastError: null,
}
