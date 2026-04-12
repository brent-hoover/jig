// @ts-nocheck
import type { JigEvent } from "./types"

function shortId(id: unknown): string {
  if (typeof id !== "string") return "?"
  return id.length > 8 ? id.slice(0, 8) : id
}

function renderEvent(event: JigEvent) {
  const d = event.data || {}

  switch (event.type) {
    case "agent_tool": {
      const role = d.role ?? "?"
      const tool = d.tool ?? "?"
      const detail = typeof d.detail === "string" ? d.detail : ""
      return (
        <text>
          <span style={{ fg: "#666666" }}>{`${role} `}</span>
          <span style={{ fg: "#cc8800", attributes: 1 }}>{`[${tool}]`}</span>
          <span style={{ fg: "#aa7700" }}>{` ${detail}`}</span>
        </text>
      )
    }
    case "agent_text": {
      const role = d.role ?? "?"
      const text = typeof d.text === "string" ? d.text : ""
      return (
        <text>
          <span style={{ fg: "#00aaff", attributes: 1 }}>{`${role}:`}</span>
          <span style={{ fg: "#dddddd" }}>{` ${text}`}</span>
        </text>
      )
    }
    case "ticket_created": {
      const type = d.type ? ` ${d.type}` : ""
      const assignee = d.assignee ? ` → ${d.assignee}` : ""
      return (
        <text>
          <span style={{ fg: "#00aaff" }}>{`+ ticket${type} ${shortId(d.ticket_id)}${assignee}`}</span>
        </text>
      )
    }
    case "ticket_updated":
      return (
        <text>
          <span style={{ fg: "#ffcc00" }}>{`~ ticket ${shortId(d.ticket_id)} → ${d.status}`}</span>
        </text>
      )
    case "comment_posted": {
      const author = d.author ?? "?"
      const content = typeof d.content === "string" ? d.content : ""
      return (
        <text>
          <span style={{ fg: "#aa88ff", attributes: 1 }}>{`${author}`}</span>
          <span style={{ fg: "#aa88ff" }}>{` on ${shortId(d.ticket_id)}: ${content}`}</span>
        </text>
      )
    }
    case "commit_recorded": {
      const sha = typeof d.sha === "string" ? d.sha.slice(0, 7) : "?"
      const msg = typeof d.message === "string" ? d.message : ""
      return (
        <text>
          <span style={{ fg: "#00cc88", attributes: 1 }}>{`${sha}`}</span>
          <span style={{ fg: "#00cc88" }}>{` ${msg}`}</span>
        </text>
      )
    }
    case "ticket_completed": {
      const title = typeof d.title === "string" ? d.title : shortId(d.ticket_id)
      const merge = typeof d.merge === "string" ? d.merge : ""
      const branch = typeof d.branch === "string" ? d.branch : ""
      return (
        <text>
          <span style={{ fg: "#00cc00", attributes: 1 }}>{"✓ DONE "}</span>
          <span style={{ fg: "#00cc00" }}>{title}</span>
          <span style={{ fg: "#888888" }}>{` — ${merge || `branch: ${branch}`}`}</span>
        </text>
      )
    }
    case "ticket_failed": {
      const title = typeof d.title === "string" ? d.title : shortId(d.ticket_id)
      return (
        <text>
          <span style={{ fg: "#cc0000", attributes: 1 }}>{"✗ FAILED "}</span>
          <span style={{ fg: "#cc0000" }}>{title}</span>
        </text>
      )
    }
    case "phase_started": {
      const phaseName = d.phase_name ?? "?"
      const role = d.phase_role ?? "?"
      const idx = typeof d.phase_index === "number" ? d.phase_index + 1 : "?"
      const total = d.total_phases ?? "?"
      return (
        <text>
          <span style={{ fg: "#00aaff", attributes: 1 }}>{"▸ "}</span>
          <span style={{ fg: "#00aaff" }}>{`phase ${idx}/${total}: ${phaseName}`}</span>
          <span style={{ fg: "#666666" }}>{` (${role}) on ${shortId(d.ticket_id)}`}</span>
        </text>
      )
    }
    case "phase_complete": {
      const phaseName = d.phase_name ?? "?"
      const result = d.result ?? "?"
      const resultColor = result === "success" ? "#00cc00" : result === "needs_info" ? "#ff00ff" : "#cc0000"
      return (
        <text>
          <span style={{ fg: resultColor, attributes: 1 }}>{"▪ "}</span>
          <span style={{ fg: resultColor }}>{`${phaseName}: ${result}`}</span>
          <span style={{ fg: "#666666" }}>{` on ${shortId(d.ticket_id)}`}</span>
        </text>
      )
    }
    default:
      return (
        <text>
          <span style={{ fg: "#888888" }}>{`${event.type}: ${JSON.stringify(d)}`}</span>
        </text>
      )
  }
}

interface EventLogProps {
  events: JigEvent[]
  scrollOffset: number
  maxVisible?: number
  focused?: boolean
}

export function EventLog({ events, scrollOffset, maxVisible = 30, focused }: EventLogProps) {
  // scrollOffset 0 = bottom (latest), positive = scrolled up
  const total = events.length
  const endIdx = Math.max(0, total - scrollOffset)
  const startIdx = Math.max(0, endIdx - maxVisible)
  const visible = events.slice(startIdx, endIdx)

  let scrollHint = ""
  if (total > maxVisible) {
    if (scrollOffset > 0) scrollHint = `↓ ${scrollOffset} newer`
    if (startIdx > 0) {
      scrollHint = scrollHint
        ? `↕ ${startIdx} older · ${scrollOffset} newer`
        : `↑ ${startIdx} older`
    }
  }

  return (
    <box
      border
      borderStyle="rounded"
      borderColor={focused ? "#00aaff" : "#444444"}
      paddingX={1}
      flexDirection="column"
      flexGrow={1}
    >
      <box flexDirection="row">
        <text>
          <span style={{ attributes: 1 }}>Event Log</span>
          {total > 0 ? (
            <span style={{ fg: "#666666" }}>{` (${total})`}</span>
          ) : null}
          {scrollHint ? (
            <span style={{ fg: "#888800" }}>{`  ${scrollHint}`}</span>
          ) : null}
        </text>
      </box>
      <box height={1} />
      <box flexGrow={1} flexDirection="column" overflow="hidden">
        {visible.length === 0 ? (
          <text>
            <span style={{ attributes: 2 }}>No events yet...</span>
          </text>
        ) : (
          visible.map((event, i) => (
            <box key={startIdx + i} flexShrink={0}>
              {renderEvent(event)}
            </box>
          ))
        )}
      </box>
    </box>
  )
}
