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
    case "merge_completed":
      return `✓ Merge: ${event.data.result}`
    case "merge_failed":
      return `✗ Merge failed: ${event.data.error}`
    case "workflow_paused":
      return `⏸ Paused: ${event.data.reason || "unknown"}`
    case "workflow_failed":
      return `✗ Failed: ${event.data.reason || "unknown"}`
    case "orchestrator_decision": {
      const phase = event.data.phase ? ` → ${event.data.phase}` : ""
      return `  🧠 ${event.data.action}${phase}: ${event.data.reasoning}`
    }
    case "orchestrator_info":
      return `  ℹ ${event.data.message}`
    case "agent_started":
      return `  ▶ Agent ${event.data.agent} starting...`
    case "agent_tool_use": {
      const detail = event.data.detail ? ` ${event.data.detail}` : ""
      return `  ⚙ ${event.data.tool}${detail}`
    }
    case "agent_text":
      return `  ▸ ${event.data.text}`
    case "agent_result": {
      const secs = ((event.data.duration_ms as number) / 1000).toFixed(1)
      const cost = event.data.cost_usd != null ? ` · $${(event.data.cost_usd as number).toFixed(2)}` : ""
      return `  ✓ Done (${event.data.num_turns} turns, ${secs}s${cost})`
    }
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
    case "merge_completed":
      return "#00cc88"
    case "merge_failed":
      return "#cc0000"
    case "workflow_paused":
      return "#ffaa00"
    case "workflow_failed":
      return "#cc0000"
    case "orchestrator_decision":
      return "#ff9900"
    case "orchestrator_info":
      return "#5599cc"
    case "agent_started":
      return "#00aaff"
    case "agent_tool_use":
      return "#aa88ff"
    case "agent_text":
      return "#cccccc"
    case "agent_result":
      return "#00cc88"
    default:
      return "#888888"
  }
}

interface MessageLogProps {
  messages: JigEvent[]
  scrollOffset: number
  maxVisible?: number
}

export function MessageLog({ messages, scrollOffset, maxVisible = 30 }: MessageLogProps) {
  // scrollOffset 0 = bottom (latest), positive = scrolled up
  const total = messages.length
  const endIdx = Math.max(0, total - scrollOffset)
  const startIdx = Math.max(0, endIdx - maxVisible)
  const visible = messages.slice(startIdx, endIdx)

  const atBottom = scrollOffset === 0
  const atTop = startIdx === 0 && endIdx < total

  let scrollHint = ""
  if (total > maxVisible) {
    if (!atBottom) scrollHint = `↑ ${scrollOffset} more below`
    if (startIdx > 0 && !atBottom) scrollHint = `↑↓ scrollable`
    else if (startIdx > 0) scrollHint = `↑ ${startIdx} more above`
  }

  return (
    <box
      border
      borderStyle="rounded"
      borderColor="#444444"
      paddingX={1}
      flexDirection="column"
      flexGrow={1}
    >
      <box flexDirection="row">
        <text>
          <span style={{ attributes: 1 }}>Event Log</span>
          {total > 0 ? (
            <span style={{ fg: "#666666" }}>{` (${total} events)`}</span>
          ) : null}
          {scrollHint ? (
            <span style={{ fg: "#888800" }}>{`  ${scrollHint}`}</span>
          ) : null}
        </text>
      </box>
      <box height={1} />
      {visible.length === 0 ? (
        <text>
          <span style={{ attributes: 2 }}>No events yet...</span>
        </text>
      ) : (
        visible.map((event, i) => (
          <text key={startIdx + i}>
            <span style={{ fg: eventColor(event) }}>
              {formatEvent(event)}
            </span>
          </text>
        ))
      )}
    </box>
  )
}
