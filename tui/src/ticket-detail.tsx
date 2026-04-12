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

function commentPrefix(kind: string): { label: string; color: string } {
  if (kind === "question") return { label: "Q: ", color: "#ffcc00" }
  if (kind === "answer") return { label: "A: ", color: "#00cc88" }
  if (kind === "decision") return { label: "D: ", color: "#ff8800" }
  return { label: "", color: "#aa88ff" }
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
              const p = commentPrefix(c.kind)
              return (
                <box key={i} flexShrink={0}>
                  <text>
                    <span style={{ fg: selected ? "#00aaff" : "#444444" }}>
                      {selected ? ">" : " "}
                    </span>
                    <span style={{ fg: p.color, attributes: selected ? 1 : 0 }}>
                      {p.label || `${c.author}: `}
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
                      <span style={{ fg: commentPrefix(selectedComment.kind).color, attributes: 1 }}>
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
          <span>{ticket.type}</span>
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
          {recentComments.map((c, i) => {
            const p = commentPrefix(c.kind)
            return (
              <box key={`m${i}`} flexShrink={0}>
                <text>
                  <span style={{ fg: p.color, attributes: 1 }}>
                    {p.label || `${c.author}: `}
                  </span>
                  <span style={{ fg: "#cccccc" }}>
                    {truncate(c.content, 28)}
                  </span>
                </text>
              </box>
            )
          })}
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
