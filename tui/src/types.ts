export interface JigEvent {
  type: string
  data: Record<string, unknown>
}

export interface PhaseStatus {
  name: string
  agentType: string
  status: "pending" | "running" | "completed" | "failed"
}

export interface JigState {
  connected: boolean
  issueId: string | null
  workflow: string | null
  phases: PhaseStatus[]
  currentPhase: string | null
  messages: JigEvent[]
  status: "idle" | "running" | "paused" | "completed" | "failed"
}

export const INITIAL_STATE: JigState = {
  connected: false,
  issueId: null,
  workflow: null,
  phases: [],
  currentPhase: null,
  messages: [],
  status: "idle",
}
