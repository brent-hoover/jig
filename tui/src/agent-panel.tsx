// @ts-nocheck
import { useState, useEffect } from "react"
import type { AgentConfig, Workflow } from "./types"
import type { SendCommand } from "./use-jig-socket"

interface AgentPanelProps {
  agents: AgentConfig[]
  workflow: Workflow | null
  selectedIdx: number
  selectedTicketId: string | null
  sendCommand: SendCommand
  promptScroll: number
}

interface StyledLine {
  text: string
  fg: string
  bold: boolean
  dim: boolean
  blank: boolean
}

/** Strip all inline markdown: **bold**, *italic*, `code`, [text](url) */
function stripMd(s: string): string {
  return s
    .replace(/\*\*(.+?)\*\*/g, "$1")   // **bold**
    .replace(/\*(.+?)\*/g, "$1")        // *italic*
    .replace(/`([^`]+)`/g, "$1")        // `code`
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // [text](url)
}

function wrapText(text: string, width: number): string[] {
  if (width < 10) return [text]
  if (text.length <= width) return [text]
  const words = text.split(/\s+/).filter(Boolean)
  const lines: string[] = []
  let current = ""
  for (const w of words) {
    if (current.length + w.length + 1 > width && current.length > 0) {
      lines.push(current)
      current = w
    } else {
      current = current ? `${current} ${w}` : w
    }
  }
  if (current) lines.push(current)
  return lines.length > 0 ? lines : [""]
}

function parsePrompt(raw: string, wrapWidth: number): StyledLine[] {
  const out: StyledLine[] = []
  const lines = raw.split("\n")
  let inCodeBlock = false

  const push = (text: string, fg: string, bold = false, dim = false) => {
    out.push({ text, fg, bold, dim, blank: false })
  }
  const pushBlank = () => {
    out.push({ text: "", fg: "", bold: false, dim: false, blank: true })
  }
  const pushWrapped = (text: string, fg: string, indent: string, bold = false) => {
    const w = wrapWidth - indent.length
    for (const line of wrapText(text, w)) {
      push(indent + line, fg, bold)
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()

    // Code fence toggle
    if (line.startsWith("```")) {
      inCodeBlock = !inCodeBlock
      continue
    }

    // Inside code block — show dim, no markdown processing
    if (inCodeBlock) {
      push("  " + line, "#666666", false, true)
      continue
    }

    // Blank line
    if (line.trim() === "") {
      pushBlank()
      continue
    }

    // # H1
    const h1 = line.match(/^#\s+(.+)/)
    if (h1) {
      pushBlank()
      push(`═══ ${stripMd(h1[1])} ═══`, "#ff8800", true)
      continue
    }

    // ## H2
    const h2 = line.match(/^##\s+(.+)/)
    if (h2) {
      pushBlank()
      push(`━━ ${stripMd(h2[1])} ━━`, "#ffcc00", true)
      continue
    }

    // ### H3
    const h3 = line.match(/^###\s+(.+)/)
    if (h3) {
      pushBlank()
      push(`── ${stripMd(h3[1])} ──`, "#cc8800", true)
      continue
    }

    // Bullet (possibly nested)
    const bullet = line.match(/^(\s*)-\s+(.+)/)
    if (bullet) {
      const depth = Math.floor(bullet[1].length / 2)
      const prefix = depth > 0 ? "  ".repeat(depth) + "· " : "• "
      pushWrapped(stripMd(bullet[2]), "#cccccc", prefix)
      continue
    }

    // Blockquote
    const bq = line.match(/^>\s*(.*)/)
    if (bq) {
      pushWrapped(stripMd(bq[1]), "#888888", "│ ")
      continue
    }

    // Regular paragraph text
    pushWrapped(stripMd(line), "#cccccc", "")
  }

  return out
}

export function AgentPanel({
  agents,
  workflow,
  selectedIdx,
  selectedTicketId,
  sendCommand,
  promptScroll,
}: AgentPanelProps) {
  const [prompt, setPrompt] = useState<string | null>(null)
  const [promptLoading, setPromptLoading] = useState(false)
  const [promptRole, setPromptRole] = useState<string | null>(null)
  const [promptTicket, setPromptTicket] = useState<string | null>(null)

  const agent = agents[selectedIdx] ?? null
  const phases = workflow?.phases ?? []

  // Fetch prompt when agent or ticket changes
  useEffect(() => {
    if (!agent || !selectedTicketId) {
      setPrompt(null)
      return
    }
    if (agent.role === promptRole && selectedTicketId === promptTicket) return

    let cancelled = false
    setPromptLoading(true)
    setPrompt(null)
    ;(async () => {
      try {
        const reply = await sendCommand("preview_prompt", {
          ticket_id: selectedTicketId,
          role: agent.role,
        })
        if (cancelled) return
        if (reply.ok && reply.prompt) {
          setPrompt(reply.prompt)
          setPromptRole(agent.role)
          setPromptTicket(selectedTicketId)
        } else {
          setPrompt(null)
        }
      } catch {
        if (!cancelled) setPrompt(null)
      }
      if (!cancelled) setPromptLoading(false)
    })()
    return () => { cancelled = true }
  }, [agent?.role, selectedTicketId])

  if (agents.length === 0) {
    return (
      <box border borderStyle="rounded" borderColor="#444444" paddingX={1} flexGrow={1} flexDirection="column">
        <text><span style={{ attributes: 2 }}>No agents loaded. Waiting for data...</span></text>
      </box>
    )
  }

  const parsed = prompt ? parsePrompt(prompt, 76) : []
  const maxScroll = Math.max(0, parsed.length - 1)
  const clampedScroll = Math.max(0, Math.min(promptScroll, maxScroll))
  const visible = parsed.slice(clampedScroll)

  return (
    <box flexDirection="row" flexGrow={1} gap={1}>
      {/* Agent list */}
      <box
        border
        borderStyle="rounded"
        borderColor="#444444"
        paddingX={1}
        width={28}
        flexShrink={0}
        flexDirection="column"
      >
        <text><span style={{ attributes: 1 }}>Agents</span></text>
        <box height={1} />
        {agents.map((a, i) => {
          const selected = i === selectedIdx
          const phase = phases.find((p) => p.role === a.role)
          return (
            <text key={a.role}>
              <span style={{ fg: selected ? "#00aaff" : "#888888", attributes: selected ? 1 : 0 }}>
                {selected ? ">" : " "} {a.role}
              </span>
              {phase ? (
                <span style={{ fg: "#666666" }}>{` (${phase.name})`}</span>
              ) : null}
            </text>
          )
        })}
      </box>

      {/* Prompt preview */}
      <box
        border
        borderStyle="rounded"
        borderColor="#444444"
        paddingX={1}
        flexGrow={1}
        flexDirection="column"
        overflow="hidden"
      >
        {agent ? (
          <>
            <text>
              <span style={{ fg: "#00aaff", attributes: 1 }}>{agent.role}</span>
              <span style={{ fg: "#444444" }}>{" — "}</span>
              <span style={{ fg: "#888888" }}>
                {selectedTicketId
                  ? `prompt for ${selectedTicketId.slice(0, 8)}`
                  : "no ticket selected"}
              </span>
              {prompt ? (
                <span style={{ fg: "#555555" }}>{` (${prompt.length} chars)`}</span>
              ) : null}
            </text>
            {clampedScroll > 0 ? (
              <text>
                <span style={{ fg: "#888800" }}>{`↑ ${clampedScroll} lines above`}</span>
              </text>
            ) : null}
            <box height={1} />
            {promptLoading ? (
              <text><span style={{ fg: "#888888", attributes: 2 }}>Loading prompt…</span></text>
            ) : prompt ? (
              <box flexGrow={1} flexDirection="column" overflow="hidden">
                {visible.map((line, i) => {
                  if (line.blank) return <box key={clampedScroll + i} height={1} flexShrink={0} />
                  const attrs = (line.bold ? 1 : 0) | (line.dim ? 2 : 0)
                  return (
                    <box key={clampedScroll + i} flexShrink={0}>
                      <text>
                        <span style={{ fg: line.fg, attributes: attrs }}>
                          {line.text}
                        </span>
                      </text>
                    </box>
                  )
                })}
              </box>
            ) : !selectedTicketId ? (
              <text><span style={{ fg: "#666666", attributes: 2 }}>Select a ticket to preview the agent prompt</span></text>
            ) : (
              <text><span style={{ fg: "#666666", attributes: 2 }}>No prompt available</span></text>
            )}
            <box flexGrow={0} flexShrink={0} height={1}>
              <text>
                <span style={{ fg: "#555555" }}>{"h/l:panel  j/k:scroll  pgup/dn  g:top  tab:tickets"}</span>
              </text>
            </box>
          </>
        ) : null}
      </box>
    </box>
  )
}
