// @ts-nocheck
import type { Ticket, TicketStatus } from "./types"

const STATUS_ICON: Record<TicketStatus, string> = {
  open: "○",
  in_progress: "◉",
  blocked: "⊘",
  needs_info: "?",
  failed: "✗",
  resolved: "✓",
  closed: "·",
}

const STATUS_COLOR: Record<TicketStatus, string> = {
  open: "#888888",
  in_progress: "#ffcc00",
  blocked: "#ff8800",
  needs_info: "#ff00ff",
  failed: "#cc0000",
  resolved: "#00cc00",
  closed: "#444444",
}

interface TicketListProps {
  tickets: Record<string, Ticket>
  selectedId: string | null
  focused?: boolean
}

export function TicketList({ tickets, selectedId, focused }: TicketListProps) {
  const sorted = Object.values(tickets).sort(
    (a, b) => b.lastActivity - a.lastActivity,
  )

  return (
    <box
      border
      borderStyle="rounded"
      borderColor={focused ? "#00aaff" : "#444444"}
      paddingX={1}
      flexDirection="column"
      flexShrink={0}
    >
      <text>
        <span style={{ attributes: 1 }}>Tickets</span>
        <span style={{ fg: "#666666" }}>{` (${sorted.length})`}</span>
      </text>
      <box height={1} />
      {sorted.length === 0 ? (
        <text>
          <span style={{ fg: "#666666", attributes: 2 }}>
            No tickets yet. Press n to create one.
          </span>
        </text>
      ) : (
        sorted.map((ticket) => {
          const selected = ticket.id === selectedId
          const needsAnswer =
            ticket.type === "question" && ticket.assignee === "user"
          return (
            <box key={ticket.id}>
              <text>
                <span
                  style={{
                    fg: selected ? "#00aaff" : "#666666",
                    attributes: selected ? 1 : 0,
                  }}
                >
                  {selected ? "▶ " : "  "}
                </span>
                <span style={{ fg: STATUS_COLOR[ticket.status] }}>
                  {STATUS_ICON[ticket.status]}
                </span>
                <span>{" "}</span>
                {needsAnswer ? (
                  <span style={{ fg: "#ff00ff", attributes: 1 }}>{"[?] "}</span>
                ) : null}
                <span
                  style={{
                    attributes: selected ? 1 : ticket.status === "closed" ? 2 : 0,
                  }}
                >
                  {ticket.title}
                </span>
                <span style={{ fg: "#555555", attributes: 2 }}>
                  {` · ${ticket.type}`}
                </span>
                {ticket.currentPhase ? (
                  <span style={{ fg: "#00aaff" }}>
                    {` [${ticket.currentPhase} ${(ticket.phaseIndex ?? 0) + 1}/${ticket.totalPhases ?? "?"}]`}
                  </span>
                ) : null}
              </text>
            </box>
          )
        })
      )}
    </box>
  )
}
