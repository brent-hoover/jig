// @ts-nocheck
import { useEffect, useState } from "react"
import type { SendCommand } from "./use-jig-socket"
import type { TicketType } from "./types"

type Stage = "type" | "title" | "description" | "submitting"

interface NewTicketFormProps {
  useKeyboard: any
  sendCommand: SendCommand
  onDone: (ticketId: string, title: string, type: TicketType) => void
  onCancel: (message?: string) => void
}

const TYPE_CHOICES: { key: string; value: TicketType; label: string }[] = [
  { key: "f", value: "feature", label: "(f)eature" },
  { key: "b", value: "bug", label: "(b)ug" },
  { key: "c", value: "chore", label: "(c)hore" },
  { key: "t", value: "task", label: "(t)ask" },
  { key: "q", value: "question", label: "(q)uestion" },
]

// Append a printable keypress to the running text buffer. Returns the new
// buffer, or null if the key wasn't a printable character (caller should then
// fall through to its own handling for Enter/Escape/Backspace).
function applyPrintable(buf: string, key: any): string | null {
  if (key.ctrl || key.meta || key.super) return null
  if (key.name === "space") return buf + " "
  // `key.sequence` carries the shifted character (accounts for capitalization);
  // for plain alphanumerics `key.name` is the raw char.
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

export function NewTicketForm({
  useKeyboard,
  sendCommand,
  onDone,
  onCancel,
}: NewTicketFormProps) {
  const [stage, setStage] = useState<Stage>("type")
  const [type, setType] = useState<TicketType | null>(null)
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [errorText, setErrorText] = useState<string | null>(null)

  useKeyboard(async (key: any) => {
    if (stage === "submitting") return

    if (key.name === "escape") {
      onCancel()
      return
    }

    if (stage === "type") {
      const choice = TYPE_CHOICES.find((c) => c.key === key.name)
      if (choice) {
        setType(choice.value)
        setStage("title")
      }
      return
    }

    if (stage === "title") {
      if (key.name === "return") {
        if (title.trim().length === 0) {
          setErrorText("title required")
          return
        }
        setErrorText(null)
        setStage("description")
        return
      }
      if (key.name === "backspace") {
        setTitle((t) => t.slice(0, -1))
        return
      }
      const next = applyPrintable(title, key)
      if (next !== null) setTitle(next)
      return
    }

    if (stage === "description") {
      if (key.name === "return") {
        setStage("submitting")
        try {
          const reply = await sendCommand("create_ticket", {
            type,
            title,
            description,
          })
          if (reply.ok && reply.ticket_id && type) {
            onDone(reply.ticket_id, title, type)
          } else {
            onCancel(reply.ok ? "no ticket_id in reply" : reply.error)
          }
        } catch (err: any) {
          onCancel(String(err?.message ?? err))
        }
        return
      }
      if (key.name === "backspace") {
        setDescription((d) => d.slice(0, -1))
        return
      }
      const next = applyPrintable(description, key)
      if (next !== null) setDescription(next)
      return
    }
  })

  return (
    <box
      border
      borderStyle="rounded"
      borderColor="#00aaff"
      paddingX={2}
      paddingY={1}
      flexDirection="column"
      width={70}
    >
      <text>
        <span style={{ fg: "#00aaff", attributes: 1 }}>New Ticket</span>
        <span style={{ fg: "#666666", attributes: 2 }}>{"  ·  esc to cancel"}</span>
      </text>
      <box height={1} />

      <text>
        <span style={{ fg: "#888888" }}>Type: </span>
        {type ? (
          <span style={{ fg: "#ffcc00", attributes: 1 }}>{type}</span>
        ) : (
          <span style={{ fg: "#666666" }}>
            {TYPE_CHOICES.map((c) => c.label).join("  ")}
          </span>
        )}
      </text>

      <text>
        <span style={{ fg: "#888888" }}>Title: </span>
        <span style={{ fg: "#ffffff" }}>{title}</span>
        {stage === "title" ? (
          <span style={{ fg: "#00aaff", attributes: 1 }}>{"▊"}</span>
        ) : null}
      </text>

      <text>
        <span style={{ fg: "#888888" }}>Description: </span>
        <span style={{ fg: "#ffffff" }}>{description}</span>
        {stage === "description" ? (
          <span style={{ fg: "#00aaff", attributes: 1 }}>{"▊"}</span>
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
          {stage === "type"
            ? "Pick a type (single letter)"
            : stage === "title"
              ? "Type title, Enter to continue"
              : stage === "description"
                ? "Type description, Enter to submit"
                : "Submitting..."}
        </span>
      </text>
    </box>
  )
}
