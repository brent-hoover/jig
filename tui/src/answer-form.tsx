// @ts-nocheck
import { useState } from "react"
import type { SendCommand } from "./use-jig-socket"
import type { Ticket } from "./types"

interface AnswerFormProps {
  useKeyboard: any
  sendCommand: SendCommand
  ticket: Ticket
  onDone: () => void
  onCancel: (message?: string) => void
}

function applyPrintable(buf: string, key: any): string | null {
  if (key.ctrl || key.meta || key.super) return null
  if (key.name === "space") return buf + " "
  const ch =
    typeof key.sequence === "string" && key.sequence.length === 1
      ? key.sequence
      : typeof key.name === "string" && key.name.length === 1
        ? key.name
        : null
  if (!ch) return null
  const code = ch.charCodeAt(0)
  if (code < 32 || code === 127) return null
  return buf + ch
}

export function AnswerForm({
  useKeyboard,
  sendCommand,
  ticket,
  onDone,
  onCancel,
}: AnswerFormProps) {
  const [content, setContent] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [errorText, setErrorText] = useState<string | null>(null)

  useKeyboard(async (key: any) => {
    if (submitting) return
    if (key.name === "escape") {
      onCancel()
      return
    }
    if (key.name === "return") {
      if (content.trim().length === 0) {
        setErrorText("answer required")
        return
      }
      setSubmitting(true)
      try {
        // Post the answer as a comment, then flip the ticket to resolved so
        // the agent loop picks it back up. Two commands, sequential (pending
        // queue is FIFO — this matters).
        const cReply = await sendCommand("comment_on_ticket", {
          ticket_id: ticket.id,
          content,
          kind: "comment",
        })
        if (!cReply.ok) {
          onCancel(cReply.error)
          return
        }
        const uReply = await sendCommand("update_ticket", {
          ticket_id: ticket.id,
          status: "resolved",
        })
        if (!uReply.ok) {
          onCancel(uReply.error)
          return
        }
        onDone()
      } catch (err: any) {
        onCancel(String(err?.message ?? err))
      }
      return
    }
    if (key.name === "backspace") {
      setContent((c) => c.slice(0, -1))
      return
    }
    const next = applyPrintable(content, key)
    if (next !== null) setContent(next)
  })

  return (
    <box
      border
      borderStyle="rounded"
      borderColor="#ff00ff"
      paddingX={2}
      paddingY={1}
      flexDirection="column"
      width={70}
    >
      <text>
        <span style={{ fg: "#ff00ff", attributes: 1 }}>Answer Question</span>
        <span style={{ fg: "#666666", attributes: 2 }}>{"  ·  esc to cancel"}</span>
      </text>
      <box height={1} />
      <text>
        <span style={{ fg: "#888888" }}>Ticket: </span>
        <span>{ticket.title}</span>
        <span style={{ fg: "#555555", attributes: 2 }}>{` (${ticket.id.slice(0, 8)})`}</span>
      </text>
      <box height={1} />
      <text>
        <span style={{ fg: "#888888" }}>Answer: </span>
        <span style={{ fg: "#ffffff" }}>{content}</span>
        {!submitting ? (
          <span style={{ fg: "#ff00ff", attributes: 1 }}>{"▊"}</span>
        ) : null}
      </text>
      <box height={1} />
      {errorText ? (
        <text>
          <span style={{ fg: "#cc0000" }}>{errorText}</span>
        </text>
      ) : null}
      <text>
        <span style={{ fg: "#666666", attributes: 2 }}>
          {submitting
            ? "Submitting..."
            : "Type your answer, Enter to submit + resolve"}
        </span>
      </text>
    </box>
  )
}
