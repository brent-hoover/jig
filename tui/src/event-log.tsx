// @ts-nocheck
import type { JigEvent } from "./types"

function shortId(id: unknown): string {
  if (typeof id !== "string") return "?"
  return id.length > 8 ? id.slice(0, 8) : id
}

function formatEvent(event: JigEvent): string {
  const d = event.data || {}
  switch (event.type) {
    case "ticket_created": {
      const type = d.type ? ` ${d.type}` : ""
      const assignee = d.assignee ? ` → ${d.assignee}` : ""
      return `+ ticket${type} ${shortId(d.ticket_id)}${assignee}`
    }
    case "ticket_updated":
      return `~ ticket ${shortId(d.ticket_id)} → ${d.status}`
    case "comment_posted": {
      const author = d.author ? `${d.author}` : "?"
      const snippet =
        typeof d.content === "string" && d.content.length > 0
          ? `: ${d.content.length > 60 ? d.content.slice(0, 60) + "…" : d.content}`
          : ""
      return `💬 ${author} on ${shortId(d.ticket_id)}${snippet}`
    }
    case "commit_recorded": {
      const sha = typeof d.sha === "string" ? d.sha.slice(0, 7) : "?"
      const msg = typeof d.message === "string" ? d.message : ""
      return `● ${sha} ${shortId(d.ticket_id)} — ${msg}`
    }
    default:
      return `${event.type}: ${JSON.stringify(d)}`
  }
}

function eventColor(event: JigEvent): string {
  switch (event.type) {
    case "ticket_created":
      return "#00aaff"
    case "ticket_updated":
      return "#ffcc00"
    case "comment_posted":
      return "#aa88ff"
    case "commit_recorded":
      return "#00cc88"
    default:
      return "#888888"
  }
}

interface EventLogProps {
  events: JigEvent[]
  scrollOffset: number
  maxVisible?: number
}

export function EventLog({ events, scrollOffset, maxVisible = 30 }: EventLogProps) {
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
      borderColor="#444444"
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
