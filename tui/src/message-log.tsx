// @ts-nocheck
import type { JigEvent } from "./types"

function formatEvent(event: JigEvent): string {
  switch (event.type) {
    case "workflow_started":
      return `▶ Workflow started for ${event.data.issue_id}`
    case "phase_started":
      return `→ Phase: ${event.data.phase} (agent: ${event.data.agent_type})`
    case "phase_completed":
      return `✓ Phase completed: ${event.data.phase}`
    case "workflow_completed":
      return `✓ Workflow completed`
    case "workflow_paused":
      return `⏸ Paused: ${event.data.reason || "unknown"}`
    case "workflow_failed":
      return `✗ Failed: ${event.data.reason || "unknown"}`
    default:
      return `  ${event.type}: ${JSON.stringify(event.data)}`
  }
}

function eventColor(event: JigEvent): string {
  switch (event.type) {
    case "workflow_started":
      return "#00aaff"
    case "phase_started":
      return "#ffcc00"
    case "phase_completed":
      return "#00cc00"
    case "workflow_completed":
      return "#00ff00"
    case "workflow_paused":
      return "#ffaa00"
    case "workflow_failed":
      return "#cc0000"
    default:
      return "#888888"
  }
}

interface MessageLogProps {
  messages: JigEvent[]
  maxMessages?: number
}

export function MessageLog({ messages, maxMessages = 20 }: MessageLogProps) {
  const visible = messages.slice(-maxMessages)

  return (
    <box
      border
      borderStyle="rounded"
      borderColor="#444444"
      paddingX={1}
      flexDirection="column"
      flexGrow={1}
    >
      <text>
        <span style={{ attributes: 1 }}>Event Log</span>
      </text>
      <box height={1} />
      {visible.length === 0 ? (
        <text>
          <span style={{ attributes: 2 }}>No events yet...</span>
        </text>
      ) : (
        visible.map((event, i) => (
          <text key={i}>
            <span style={{ fg: eventColor(event) }}>
              {formatEvent(event)}
            </span>
          </text>
        ))
      )}
    </box>
  )
}
