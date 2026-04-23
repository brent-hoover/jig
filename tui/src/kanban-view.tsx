// @ts-nocheck
import type { Ticket, TicketStatus, Workflow } from "./types"

const STATUS_ICON: Record<string, string> = {
  open: "○",
  in_progress: "◉",
  blocked: "⊘",
  needs_info: "?",
  failed: "✗",
  merge_conflict: "⚠",
  resolved: "✓",
  closed: "·",
}

const STATUS_COLOR: Record<string, string> = {
  open: "#888888",
  in_progress: "#ffcc00",
  blocked: "#ff8800",
  needs_info: "#ff00ff",
  failed: "#cc0000",
  merge_conflict: "#ff8800",
  resolved: "#00cc00",
  closed: "#444444",
}

interface KanbanViewProps {
  tickets: Record<string, Ticket>
  workflow: Workflow | null
}

interface Column {
  name: string
  tickets: Ticket[]
  color: string
}

function buildColumns(
  tickets: Record<string, Ticket>,
  workflow: Workflow | null,
): Column[] {
  const all = Object.values(tickets)

  // Phase columns from workflow
  const phaseNames = workflow?.phases.map((p) => p.name) ?? []

  const columns: Column[] = []

  // Backlog: open tickets not yet in a phase
  columns.push({
    name: "backlog",
    tickets: all.filter(
      (t) => t.status === "open" && !t.currentPhase,
    ),
    color: "#888888",
  })

  // One column per workflow phase
  for (const phase of phaseNames) {
    columns.push({
      name: phase,
      tickets: all.filter((t) => t.currentPhase === phase),
      color: "#00aaff",
    })
  }

  // Done: resolved/closed, or failed/blocked without a current phase
  columns.push({
    name: "done",
    tickets: all.filter(
      (t) =>
        !t.currentPhase &&
        (t.status === "resolved" || t.status === "closed"),
    ),
    color: "#00cc00",
  })

  columns.push({
    name: "failed",
    tickets: all.filter(
      (t) =>
        !t.currentPhase &&
        (t.status === "failed" ||
          t.status === "blocked" ||
          t.status === "merge_conflict"),
    ),
    color: "#cc0000",
  })

  // Only show columns that have tickets or are phase columns
  return columns.filter(
    (c) => c.tickets.length > 0 || phaseNames.includes(c.name),
  )
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s
  return s.slice(0, max - 1) + "…"
}

function ColumnView({ col }: { col: Column }) {
  return (
    <box
      flexGrow={1}
      flexBasis={0}
      flexDirection="column"
      border
      borderStyle="single"
      borderColor={col.color}
      overflow="hidden"
    >
      <box flexShrink={0} paddingX={1}>
        <text>
          <span style={{ fg: col.color, attributes: 1 }}>
            {col.name}
          </span>
          <span style={{ fg: "#555555" }}>{` (${col.tickets.length})`}</span>
        </text>
      </box>
      <box height={1} flexShrink={0} />
      {col.tickets.length === 0 ? (
        <box flexShrink={0} paddingX={1}>
          <text>
            <span style={{ fg: "#333333", attributes: 2 }}>{"—"}</span>
          </text>
        </box>
      ) : (
        col.tickets.map((t) => (
          <box key={t.id} flexShrink={0} paddingX={1}>
            <text>
              <span style={{ fg: STATUS_COLOR[t.status] ?? "#888888" }}>
                {STATUS_ICON[t.status] ?? "·"}
              </span>
              <span>{" "}</span>
              <span style={{ fg: "#666666" }}>{`[${t.size}] `}</span>
              <span>{truncate(t.title, 14)}</span>
            </text>
          </box>
        ))
      )}
    </box>
  )
}

export function KanbanView({ tickets, workflow }: KanbanViewProps) {
  const columns = buildColumns(tickets, workflow)

  if (columns.length === 0) {
    return (
      <box
        border
        borderStyle="rounded"
        borderColor="#444444"
        paddingX={1}
        flexGrow={1}
      >
        <text>
          <span style={{ fg: "#666666" }}>No workflow loaded</span>
        </text>
      </box>
    )
  }

  return (
    <box flexGrow={1} flexDirection="row" gap={1}>
      {columns.map((col) => (
        <ColumnView key={col.name} col={col} />
      ))}
    </box>
  )
}
