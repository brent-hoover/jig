// @ts-nocheck
import type { AppState, Ticket } from "./types"

interface StatusBarProps {
  state: AppState
  focusedPanel?: "left" | "right"
}

function countByStatus(tickets: Record<string, Ticket>) {
  let open = 0
  let active = 0
  let needsInfo = 0
  let failed = 0
  let done = 0
  for (const t of Object.values(tickets)) {
    if (t.status === "resolved" || t.status === "closed") done++
    else if (t.status === "needs_info") needsInfo++
    else if (t.status === "failed") failed++
    else if (t.status === "in_progress") active++
    else open++
  }
  return { open, active, done, needsInfo, failed }
}

export function JigStatusBar({ state, focusedPanel }: StatusBarProps) {
  const connColor = state.connected ? "#00cc00" : "#cc0000"
  const connText = state.connected ? "connected" : "reconnecting..."
  const counts = countByStatus(state.tickets)

  const hints =
    state.viewMode === "agents"
      ? `h/l:panel  j/k:${focusedPanel === "right" ? "scroll" : "select"}  pgup/dn  tab/S-tab:view  q:quit`
      : state.viewMode === "ticket"
        ? `j/k:scroll  pgup/dn  g:top  tab/S-tab:view  q:quit`
        : state.viewMode === "kanban"
          ? `tab/S-tab:view  n:new  q:quit`
          : `n:new  h/l:panel  j/k:${focusedPanel === "right" ? "scroll" : "move"}  g:bottom  tab/S-tab:view  q:quit`

  return (
    <box height={2} flexDirection="column" flexShrink={0}>
      <box height={1} paddingX={1}>
        <text>
          <span style={{ fg: connColor, attributes: 1 }}>{connText}</span>
          <span style={{ fg: "#444444" }}>{" │ "}</span>
          <span style={{ fg: "#ffcc00" }}>{`${counts.active} active`}</span>
          <span style={{ fg: "#444444" }}>{" · "}</span>
          <span>{`${counts.open} open`}</span>
          <span style={{ fg: "#444444" }}>{" · "}</span>
          <span style={{ fg: "#00cc88" }}>{`${counts.done} done`}</span>
          {counts.needsInfo > 0 ? (
            <>
              <span style={{ fg: "#444444" }}>{" │ "}</span>
              <span style={{ fg: "#ff00ff", attributes: 1 }}>
                {`${counts.needsInfo} needs info`}
              </span>
            </>
          ) : null}
          {counts.failed > 0 ? (
            <>
              <span style={{ fg: "#444444" }}>{" │ "}</span>
              <span style={{ fg: "#cc0000", attributes: 1 }}>
                {`${counts.failed} failed (r:retry)`}
              </span>
            </>
          ) : null}
          {state.lastError ? (
            <>
              <span style={{ fg: "#444444" }}>{" │ "}</span>
              <span style={{ fg: "#cc0000" }}>{`err: ${state.lastError}`}</span>
            </>
          ) : null}
        </text>
      </box>
      <box height={1} paddingX={1}>
        <text>
          <span style={{ fg: "#00aaff", attributes: 1 }}>
            {state.viewMode === "agents"
              ? "[agents]"
              : state.viewMode === "ticket"
                ? "[ticket]"
                : state.viewMode === "kanban"
                  ? "[kanban]"
                  : "[live]"}
          </span>
          <span style={{ fg: "#666666" }}>{`  ${hints}`}</span>
        </text>
      </box>
    </box>
  )
}
