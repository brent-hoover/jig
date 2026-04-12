// @ts-nocheck
import type { AppState, Ticket } from "./types"

interface StatusBarProps {
  state: AppState
}

function countByStatus(tickets: Record<string, Ticket>) {
  let open = 0
  let active = 0
  let needsAnswer = 0
  let done = 0
  for (const t of Object.values(tickets)) {
    if (t.status === "resolved" || t.status === "closed") done++
    else if (t.status === "in_progress") active++
    else open++
    if (t.type === "question" && t.assignee === "user" && t.status !== "resolved") {
      needsAnswer++
    }
  }
  return { open, active, done, needsAnswer }
}

export function JigStatusBar({ state }: StatusBarProps) {
  const connColor = state.connected ? "#00cc00" : "#cc0000"
  const connText = state.connected ? "connected" : "reconnecting..."
  const counts = countByStatus(state.tickets)

  return (
    <box height={1} flexDirection="row" paddingX={1}>
      <text>
        <span style={{ fg: connColor, attributes: 1 }}>{connText}</span>
        <span style={{ fg: "#444444", attributes: 2 }}>{" │ "}</span>
        <span style={{ fg: "#ffcc00" }}>{`${counts.active} active`}</span>
        <span style={{ fg: "#444444", attributes: 2 }}>{" · "}</span>
        <span>{`${counts.open} open`}</span>
        <span style={{ fg: "#444444", attributes: 2 }}>{" · "}</span>
        <span style={{ fg: "#00cc88" }}>{`${counts.done} done`}</span>
        {counts.needsAnswer > 0 ? (
          <>
            <span style={{ fg: "#444444", attributes: 2 }}>{" │ "}</span>
            <span style={{ fg: "#ff00ff", attributes: 1 }}>
              {`${counts.needsAnswer} awaiting answer`}
            </span>
          </>
        ) : null}
        {state.lastError ? (
          <>
            <span style={{ fg: "#444444", attributes: 2 }}>{" │ "}</span>
            <span style={{ fg: "#cc0000" }}>{`err: ${state.lastError}`}</span>
          </>
        ) : null}
        <span style={{ fg: "#444444", attributes: 2 }}>
          {"  │  n:new  a:answer  j/k:move  q:quit"}
        </span>
      </text>
    </box>
  )
}
