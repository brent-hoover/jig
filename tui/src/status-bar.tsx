// @ts-nocheck
import { useState, useEffect } from "react"
import type { JigState } from "./types"

interface JigStatusBarProps {
  state: JigState
}

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

export function JigStatusBar({ state }: JigStatusBarProps) {
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (state.status !== "running") return
    const id = setInterval(() => setTick((t) => t + 1), 200)
    return () => clearInterval(id)
  }, [state.status])

  const connColor = state.connected ? "#00cc00" : "#cc0000"
  const connText = state.connected ? "Connected" : "Reconnecting..."

  let workflowText: string
  if (state.status === "running") {
    const spinner = SPINNER[tick % SPINNER.length]
    const phase = state.currentPhase || "starting"
    const agent = state.currentAgent ? ` [${state.currentAgent}]` : ""
    const elapsed = state.runStartedAt
      ? `${Math.floor((Date.now() - state.runStartedAt) / 1000)}s`
      : ""
    workflowText = `${spinner} ${phase}${agent}${elapsed ? ` (${elapsed})` : ""}`
  } else {
    workflowText = state.status
  }

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
        <span style={{ fg: "#444444", attributes: 2 }}>{" │ j/k: scroll │ q: quit"}</span>
      </text>
    </box>
  )
}
