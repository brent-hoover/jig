// @ts-nocheck
import { useState } from "react"
import type { SendCommand } from "./use-jig-socket"
import type { Size, WorkType } from "./types"

type Stage =
  | "workType"
  | "size"
  | "workflow"
  | "title"
  | "description"
  | "submitting"

interface NewTicketFormProps {
  useKeyboard: any
  sendCommand: SendCommand
  onDone: (
    ticketId: string,
    title: string,
    workType: WorkType,
    size: Size,
  ) => void
  onCancel: (message?: string) => void
}

// Phase 1 work-type catalog — matches jig.ticket.WorkType. Keys are the
// first letter of each value except `refactor` (r) and `migration` (m)
// which collide and get disambiguated below.
const WORK_TYPE_CHOICES: {
  key: string
  value: WorkType
  label: string
}[] = [
  { key: "f", value: "feature", label: "(f)eature" },
  { key: "b", value: "bugfix", label: "(b)ugfix" },
  { key: "r", value: "refactor", label: "(r)efactor" },
  { key: "s", value: "spike", label: "(s)pike" },
  { key: "p", value: "perf", label: "(p)erf" },
  { key: "m", value: "migration", label: "(m)igration" },
  { key: "d", value: "docs", label: "(d)ocs" },
]

// Size choices — number keys so they don't collide with work-type letters.
const SIZE_CHOICES: { key: string; value: Size; label: string }[] = [
  { key: "1", value: "xs", label: "(1) xs" },
  { key: "2", value: "s", label: "(2) s" },
  { key: "3", value: "m", label: "(3) m" },
  { key: "4", value: "l", label: "(4) l" },
  { key: "5", value: "xl", label: "(5) xl" },
]

// Types that still have the "default vs project pipeline" prompt. Phase 2
// replaces this with workflows.by_type resolution from config.yaml.
const WORKFLOW_TYPES: Set<WorkType> = new Set([
  "feature",
  "bugfix",
  "refactor",
])

const WORKFLOW_CHOICES: { key: string; value: string; label: string }[] = [
  { key: "d", value: "default", label: "(d)efault — single ticket, standard pipeline" },
  { key: "p", value: "project", label: "(p)roject — PM breaks requirements into tickets" },
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
  const [stage, setStage] = useState<Stage>("workType")
  const [workType, setWorkType] = useState<WorkType | null>(null)
  const [size, setSize] = useState<Size>("m")
  const [workflow, setWorkflow] = useState("default")
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [errorText, setErrorText] = useState<string | null>(null)

  useKeyboard(async (key: any) => {
    if (stage === "submitting") return

    if (key.name === "escape") {
      onCancel()
      return
    }

    if (stage === "workType") {
      const choice = WORK_TYPE_CHOICES.find((c) => c.key === key.name)
      if (choice) {
        setWorkType(choice.value)
        setStage("size")
      }
      return
    }

    if (stage === "size") {
      const choice = SIZE_CHOICES.find((c) => c.key === key.name)
      if (choice) {
        setSize(choice.value)
        if (workType && WORKFLOW_TYPES.has(workType)) {
          setStage("workflow")
        } else {
          setStage("title")
        }
      }
      return
    }

    if (stage === "workflow") {
      const choice = WORKFLOW_CHOICES.find((c) => c.key === key.name)
      if (choice) {
        setWorkflow(choice.value)
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
            work_type: workType,
            size,
            title,
            description,
            workflow,
          })
          if (reply.ok && reply.ticket_id && workType) {
            onDone(reply.ticket_id, title, workType, size)
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
      backgroundColor="#1a1a2e"
      paddingX={2}
      paddingY={1}
      flexDirection="column"
      width={72}
    >
      <text>
        <span style={{ fg: "#00aaff", attributes: 1 }}>New Ticket</span>
        <span style={{ fg: "#666666", attributes: 2 }}>{"  ·  esc to cancel"}</span>
      </text>
      <box height={1} />

      <text>
        <span style={{ fg: "#888888" }}>Type: </span>
        {workType ? (
          <span style={{ fg: "#ffcc00", attributes: 1 }}>{workType}</span>
        ) : (
          <span style={{ fg: "#666666" }}>
            {WORK_TYPE_CHOICES.map((c) => c.label).join("  ")}
          </span>
        )}
      </text>

      <text>
        <span style={{ fg: "#888888" }}>Size: </span>
        {stage === "size" ? (
          <span style={{ fg: "#666666" }}>
            {SIZE_CHOICES.map((c) => c.label).join("  ")}
          </span>
        ) : stage === "workType" ? (
          <span style={{ fg: "#444444" }}>—</span>
        ) : (
          <span style={{ fg: "#00cc88", attributes: 1 }}>{size}</span>
        )}
      </text>

      {workType && WORKFLOW_TYPES.has(workType) ? (
        <text>
          <span style={{ fg: "#888888" }}>Workflow: </span>
          {stage === "workflow" ? (
            <span style={{ fg: "#666666" }}>
              {WORKFLOW_CHOICES.map((c) => c.label).join("  ")}
            </span>
          ) : stage === "workType" || stage === "size" ? (
            <span style={{ fg: "#444444" }}>—</span>
          ) : (
            <span style={{ fg: "#00cc88", attributes: 1 }}>{workflow}</span>
          )}
        </text>
      ) : null}

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
          {stage === "workType"
            ? "Pick a work type (single letter)"
            : stage === "size"
              ? "Pick a size (1–5)"
              : stage === "workflow"
                ? "Pick workflow: (d)efault or (p)roject"
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
