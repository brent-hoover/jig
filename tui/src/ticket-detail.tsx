// @ts-nocheck
import type { Ticket, JigEvent } from "./types"

const STATUS_LABEL: Record<string, string> = {
  open: "open",
  in_progress: "in progress",
  blocked: "BLOCKED",
  needs_info: "needs info",
  failed: "FAILED",
  resolved: "resolved",
  closed: "closed",
}

const STATUS_COLOR: Record<string, string> = {
  open: "#888888",
  in_progress: "#ffcc00",
  blocked: "#ff8800",
  needs_info: "#ff00ff",
  failed: "#cc0000",
  resolved: "#00cc00",
  closed: "#444444",
}

interface CommentItem {
  author: string
  content: string
  kind: string
}

interface CommitItem {
  sha: string
  message: string
}

interface TicketDetailProps {
  ticket: Ticket
  events: JigEvent[]
  detailMode?: boolean
  commentIdx?: number
}

interface FullTicketViewProps {
  ticket: Ticket
  events: JigEvent[]
  scrollOffset: number
  width: number
}

function extractComments(
  events: JigEvent[],
  ticketId: string,
): CommentItem[] {
  const comments: CommentItem[] = []
  for (const e of events) {
    if (e.type !== "comment_posted") continue
    const d = e.data || {}
    if (d.ticket_id !== ticketId) continue
    const content = typeof d.content === "string" ? d.content : ""
    if (content.startsWith("phase ") || content.startsWith("status ")) continue
    comments.push({
      author: (d.author as string) ?? "?",
      content,
      kind: (d.comment_kind as string) ?? "comment",
    })
  }
  return comments
}

function extractCommits(
  events: JigEvent[],
  ticketId: string,
): CommitItem[] {
  const commits: CommitItem[] = []
  for (const e of events) {
    if (e.type !== "commit_recorded") continue
    const d = e.data || {}
    if (d.ticket_id !== ticketId) continue
    commits.push({
      sha: typeof d.sha === "string" ? d.sha.slice(0, 7) : "?",
      message: typeof d.message === "string" ? d.message : "",
    })
  }
  return commits
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s
  return s.slice(0, max - 1) + "…"
}

function commentColor(kind: string): string {
  if (kind === "question") return "#ffcc00"
  if (kind === "answer") return "#00cc88"
  if (kind === "decision") return "#ff8800"
  return "#aa88ff"
}

export function TicketDetail({
  ticket,
  events,
  detailMode = false,
  commentIdx = 0,
}: TicketDetailProps) {
  const comments = extractComments(events, ticket.id)
  const commits = extractCommits(events, ticket.id)
  const clampedIdx = Math.max(0, Math.min(commentIdx, comments.length - 1))
  const selectedComment = comments[clampedIdx] ?? null

  // Detail mode: show full comment content
  if (detailMode) {
    return (
      <box
        border
        borderStyle="rounded"
        borderColor="#00aaff"
        paddingX={1}
        flexDirection="column"
        flexGrow={1}
        overflow="hidden"
      >
        <box flexShrink={0}>
          <text>
            <span style={{ attributes: 1 }}>{truncate(ticket.title, 34)}</span>
            <span style={{ fg: "#666666" }}>{` (${comments.length})`}</span>
          </text>
        </box>
        <box height={1} flexShrink={0} />

        {comments.length === 0 ? (
          <box flexShrink={0}>
            <text><span style={{ fg: "#666666", attributes: 2 }}>No comments yet</span></text>
          </box>
        ) : (
          <>
            {/* Comment list with selection highlight */}
            {comments.map((c, i) => {
              const selected = i === clampedIdx
              return (
                <box key={i} flexShrink={0}>
                  <text>
                    <span style={{ fg: selected ? "#00aaff" : "#444444" }}>
                      {selected ? ">" : " "}
                    </span>
                    <span style={{ fg: commentColor(c.kind), attributes: selected ? 1 : 0 }}>
                      {`${c.author}: `}
                    </span>
                    <span style={{ fg: selected ? "#ffffff" : "#888888" }}>
                      {truncate(c.content, 30)}
                    </span>
                  </text>
                </box>
              )
            })}

            {/* Full content of selected comment */}
            {selectedComment ? (
              <>
                <box height={1} flexShrink={0} />
                <box
                  border
                  borderStyle="single"
                  borderColor="#333333"
                  paddingX={1}
                  flexGrow={1}
                  flexDirection="column"
                  overflow="hidden"
                >
                  <box flexShrink={0}>
                    <text>
                      <span style={{ fg: commentColor(selectedComment.kind), attributes: 1 }}>
                        {selectedComment.author}
                      </span>
                      <span style={{ fg: "#555555" }}>{` (${selectedComment.kind})`}</span>
                    </text>
                  </box>
                  <box height={1} flexShrink={0} />
                  {selectedComment.content.split("\n").map((line, i) => (
                    <box key={i} flexShrink={0}>
                      <text><span style={{ fg: "#cccccc" }}>{line}</span></text>
                    </box>
                  ))}
                </box>
              </>
            ) : null}
          </>
        )}

        <box flexGrow={0} flexShrink={0} height={1}>
          <text>
            <span style={{ fg: "#555555" }}>{"esc:back  j/k:scroll"}</span>
          </text>
        </box>
      </box>
    )
  }

  // Summary mode (default)
  const recentComments = comments.slice(-6)
  const recentCommits = commits.slice(-3)

  return (
    <box
      border
      borderStyle="rounded"
      borderColor="#444444"
      paddingX={1}
      flexDirection="column"
      flexGrow={1}
      overflow="hidden"
    >
      <box flexShrink={0}>
        <text>
          <span style={{ attributes: 1 }}>{truncate(ticket.title, 36)}</span>
        </text>
      </box>
      <box height={1} flexShrink={0} />
      <box flexShrink={0}>
        <text>
          <span style={{ fg: "#666666" }}>{"type  "}</span>
          <span>{ticket.workType}</span>
          <span style={{ fg: "#666666" }}>{"  size  "}</span>
          <span style={{ fg: "#00cc88" }}>{ticket.size}</span>
        </text>
      </box>
      <box flexShrink={0}>
        <text>
          <span style={{ fg: "#666666" }}>{"state "}</span>
          <span style={{ fg: STATUS_COLOR[ticket.status] ?? "#888888" }}>
            {STATUS_LABEL[ticket.status] ?? ticket.status}
          </span>
          {ticket.currentPhase ? (
            <span style={{ fg: "#00aaff" }}>
              {` · ${ticket.currentPhase} (${(ticket.phaseIndex ?? 0) + 1}/${ticket.totalPhases ?? "?"})`}
            </span>
          ) : null}
        </text>
      </box>
      {ticket.assignee ? (
        <box flexShrink={0}>
          <text>
            <span style={{ fg: "#666666" }}>{"agent "}</span>
            <span>{ticket.assignee}</span>
          </text>
        </box>
      ) : null}

      {ticket.description ? (
        <>
          <box height={1} flexShrink={0} />
          <box flexShrink={0}>
            <text>
              <span style={{ fg: "#cccccc" }}>
                {truncate(ticket.description, 36)}
              </span>
            </text>
          </box>
        </>
      ) : null}

      {ticket.status === "needs_info" ? (
        <>
          <box height={1} flexShrink={0} />
          <box flexShrink={0}>
            <text>
              <span style={{ fg: "#ff00ff", attributes: 1 }}>
                {"a:answer  r:resume"}
              </span>
            </text>
          </box>
        </>
      ) : null}

      {recentCommits.length > 0 ? (
        <>
          <box height={1} flexShrink={0} />
          <box flexShrink={0}>
            <text>
              <span style={{ fg: "#666666", attributes: 1 }}>Commits</span>
            </text>
          </box>
          {recentCommits.map((c, i) => (
            <box key={`c${i}`} flexShrink={0}>
              <text>
                <span style={{ fg: "#00cc88" }}>{c.sha}</span>
                <span style={{ fg: "#888888" }}>{` ${truncate(c.message, 26)}`}</span>
              </text>
            </box>
          ))}
        </>
      ) : null}

      {recentComments.length > 0 ? (
        <>
          <box height={1} flexShrink={0} />
          <box flexShrink={0}>
            <text>
              <span style={{ fg: "#666666", attributes: 1 }}>
                {`Comments (${comments.length})`}
              </span>
              {comments.length > 6 ? (
                <span style={{ fg: "#555555" }}>{" enter:expand"}</span>
              ) : null}
            </text>
          </box>
          {recentComments.map((c, i) => (
              <box key={`m${i}`} flexShrink={0}>
                <text>
                  <span style={{ fg: commentColor(c.kind), attributes: 1 }}>
                    {`${c.author}: `}
                  </span>
                  <span style={{ fg: "#cccccc" }}>
                    {truncate(c.content, 28)}
                  </span>
                </text>
              </box>
          ))}
        </>
      ) : null}

      {recentComments.length === 0 && recentCommits.length === 0 ? (
        <>
          <box height={1} flexShrink={0} />
          <box flexShrink={0}>
            <text>
              <span style={{ fg: "#666666", attributes: 2 }}>
                No activity yet
              </span>
            </text>
          </box>
        </>
      ) : null}
    </box>
  )
}


// --- Full-panel ticket view (the "ticket" tab) ---

function wrapText(text: string, width: number): string[] {
  if (width <= 0) return [text]
  const lines: string[] = []
  for (const raw of text.split("\n")) {
    if (raw.length === 0) {
      lines.push("")
      continue
    }
    const words = raw.split(/\s+/).filter(Boolean)
    let cur = ""
    for (const w of words) {
      if (cur.length === 0) {
        cur = w
      } else if (cur.length + 1 + w.length <= width) {
        cur += " " + w
      } else {
        lines.push(cur)
        cur = w
      }
    }
    if (cur) lines.push(cur)
  }
  return lines
}

interface Section {
  kind: "header" | "field" | "blank" | "subheader" | "text" | "comment" | "commit"
  label?: string
  value?: string
  color?: string
  labelColor?: string
}

function buildSections(
  ticket: Ticket,
  events: JigEvent[],
  width: number,
): Section[] {
  const sections: Section[] = []
  const w = Math.max(10, width - 4) // padding

  // Title
  for (const line of wrapText(ticket.title, w)) {
    sections.push({ kind: "header", value: line })
  }
  sections.push({ kind: "blank" })

  // Metadata fields
  sections.push({ kind: "field", label: "ID    ", value: ticket.id })
  sections.push({ kind: "field", label: "Type  ", value: ticket.workType })
  sections.push({ kind: "field", label: "Size  ", value: ticket.size })
  const statusLabel = STATUS_LABEL[ticket.status] ?? ticket.status
  let statusLine = statusLabel
  if (ticket.currentPhase) {
    statusLine += ` · ${ticket.currentPhase} (${(ticket.phaseIndex ?? 0) + 1}/${ticket.totalPhases ?? "?"})`
  }
  sections.push({
    kind: "field",
    label: "Status",
    value: statusLine,
    color: STATUS_COLOR[ticket.status] ?? "#888888",
  })
  if (ticket.assignee) {
    sections.push({ kind: "field", label: "Agent ", value: ticket.assignee })
  }
  if (ticket.parentId) {
    sections.push({ kind: "field", label: "Parent", value: ticket.parentId })
  }
  if (ticket.dependsOn && ticket.dependsOn.length > 0) {
    sections.push({ kind: "field", label: "Deps  ", value: ticket.dependsOn.join(", ") })
  }

  // Description
  if (ticket.description) {
    sections.push({ kind: "blank" })
    sections.push({ kind: "subheader", value: "Description" })
    for (const line of wrapText(ticket.description, w)) {
      sections.push({ kind: "text", value: line })
    }
  }

  // Commits
  const commits = extractCommits(events, ticket.id)
  if (commits.length > 0) {
    sections.push({ kind: "blank" })
    sections.push({ kind: "subheader", value: `Commits (${commits.length})` })
    for (const c of commits) {
      sections.push({ kind: "commit", label: c.sha, value: c.message })
    }
  }

  // Comments
  const comments = extractComments(events, ticket.id)
  if (comments.length > 0) {
    sections.push({ kind: "blank" })
    sections.push({ kind: "subheader", value: `Comments (${comments.length})` })
    for (const c of comments) {
      sections.push({
        kind: "comment",
        label: `${c.author}: `,
        labelColor: commentColor(c.kind),
      })
      for (const line of wrapText(c.content, w - 2)) {
        sections.push({ kind: "text", value: "  " + line })
      }
      sections.push({ kind: "blank" })
    }
  }

  if (commits.length === 0 && comments.length === 0) {
    sections.push({ kind: "blank" })
    sections.push({ kind: "text", value: "No activity yet", color: "#666666" })
  }

  return sections
}

function renderSection(section: Section, key: number) {
  switch (section.kind) {
    case "blank":
      return <box key={key} height={1} flexShrink={0} />
    case "header":
      return (
        <box key={key} flexShrink={0}>
          <text>
            <span style={{ attributes: 1 }}>{section.value}</span>
          </text>
        </box>
      )
    case "subheader":
      return (
        <box key={key} flexShrink={0}>
          <text>
            <span style={{ fg: "#666666", attributes: 1 }}>
              {section.value}
            </span>
          </text>
        </box>
      )
    case "field":
      return (
        <box key={key} flexShrink={0}>
          <text>
            <span style={{ fg: "#666666" }}>{section.label} </span>
            <span style={{ fg: section.color ?? "#cccccc" }}>
              {section.value}
            </span>
          </text>
        </box>
      )
    case "text":
      return (
        <box key={key} flexShrink={0}>
          <text>
            <span style={{ fg: section.color ?? "#cccccc" }}>
              {section.value}
            </span>
          </text>
        </box>
      )
    case "comment":
      return (
        <box key={key} flexShrink={0}>
          <text>
            <span style={{ fg: section.labelColor ?? "#aa88ff", attributes: 1 }}>
              {section.label}
            </span>
          </text>
        </box>
      )
    case "commit":
      return (
        <box key={key} flexShrink={0}>
          <text>
            <span style={{ fg: "#00cc88" }}>{section.label}</span>
            <span style={{ fg: "#888888" }}>{` ${section.value}`}</span>
          </text>
        </box>
      )
    default:
      return null
  }
}

export function FullTicketView({
  ticket,
  events,
  scrollOffset,
  width,
}: FullTicketViewProps) {
  const sections = buildSections(ticket, events, width)
  const visible = sections.slice(scrollOffset)

  return (
    <box
      border
      borderStyle="rounded"
      borderColor="#00aaff"
      paddingX={1}
      flexDirection="column"
      flexGrow={1}
      overflow="hidden"
    >
      {visible.map((s, i) => renderSection(s, i))}
      <box flexGrow={1} />
      <box flexShrink={0} height={1}>
        <text>
          <span style={{ fg: "#555555" }}>
            {scrollOffset > 0
              ? `line ${scrollOffset + 1}/${sections.length}  j/k:scroll  g:top`
              : `${sections.length} lines  j/k:scroll`}
          </span>
        </text>
      </box>
    </box>
  )
}
