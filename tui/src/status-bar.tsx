// @ts-nocheck
import type { JigState } from "./types"

interface JigStatusBarProps {
  state: JigState
}

export function JigStatusBar({ state }: JigStatusBarProps) {
  const connColor = state.connected ? "#00cc00" : "#cc0000"
  const connText = state.connected ? "Connected" : "Disconnected"
  const workflowText =
    state.status === "running"
      ? `Running: ${state.currentPhase || "..."}`
      : state.status

  return (
    <box height={1} flexDirection="row" paddingX={1}>
      <text>
        <span style={{ fg: connColor, attributes: 1 }}>{connText}</span>
        <span style={{ fg: "#444444", attributes: 2 }}>{" │ "}</span>
        <span style={{ attributes: state.status === "running" ? 1 : 0 }}>
          {workflowText}
        </span>
        {state.issueId ? (
          <>
            <span style={{ fg: "#444444", attributes: 2 }}>{" │ "}</span>
            <span>{state.issueId}</span>
          </>
        ) : null}
        <span style={{ fg: "#444444", attributes: 2 }}>{" │ q: quit"}</span>
      </text>
    </box>
  )
}
