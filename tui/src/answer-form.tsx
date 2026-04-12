// @ts-nocheck
import { useState, useEffect } from "react"
import type { SendCommand, CommentData } from "./use-jig-socket"
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

/** Extract unanswered questions: a question is answered if there's a
 *  kind="answer" comment after it in the array (simple sequential match). */
function findUnanswered(comments: CommentData[]): CommentData[] {
  const pending: CommentData[] = []
  for (const c of comments) {
    if (c.kind === "question") {
      pending.push(c)
    } else if (c.kind === "answer" && pending.length > 0) {
      pending.pop()
    }
  }
  return pending
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s
  return s.slice(0, max - 1) + "…"
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
  const [questions, setQuestions] = useState<CommentData[]>([])
  const [currentIdx, setCurrentIdx] = useState(0)
  const [answers, setAnswers] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [allDone, setAllDone] = useState(false)

  // Fetch comments on mount
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const reply = await sendCommand("get_comments", {
          ticket_id: ticket.id,
        })
        if (cancelled) return
        if (!reply.ok) {
          setErrorText(reply.error)
          setLoading(false)
          return
        }
        const unanswered = findUnanswered(reply.comments ?? [])
        setQuestions(unanswered)
        if (unanswered.length === 0) {
          setAllDone(true)
        }
      } catch (err: any) {
        if (!cancelled) setErrorText(String(err?.message ?? err))
      }
      if (!cancelled) setLoading(false)
    })()
    return () => { cancelled = true }
  }, [])

  const currentQ = questions[currentIdx] ?? null

  useKeyboard(async (key: any) => {
    if (submitting || loading) return
    if (key.name === "escape") {
      onCancel()
      return
    }

    // All questions answered — Enter submits all answers + resumes
    if (allDone) {
      if (key.name === "return") {
        if (answers.length === 0) {
          // No questions to answer — just resume
          const reply = await sendCommand("update_ticket", {
            ticket_id: ticket.id,
            status: "in_progress",
          })
          if (!reply.ok) {
            onCancel(reply.error)
            return
          }
          onDone()
          return
        }
        setSubmitting(true)
        try {
          const reply = await sendCommand("answer_questions", {
            ticket_id: ticket.id,
            answers,
            resume: true,
          })
          if (!reply.ok) {
            onCancel(reply.error)
            return
          }
          onDone()
        } catch (err: any) {
          onCancel(String(err?.message ?? err))
        }
      }
      return
    }

    if (key.name === "return") {
      if (content.trim().length === 0) {
        setErrorText("answer required")
        return
      }
      setErrorText(null)
      const newAnswers = [...answers, content.trim()]
      setAnswers(newAnswers)
      // Advance to next question
      const nextIdx = currentIdx + 1
      if (nextIdx >= questions.length) {
        setAllDone(true)
      } else {
        setCurrentIdx(nextIdx)
      }
      setContent("")
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
      backgroundColor="#1a1a2e"
      paddingX={2}
      paddingY={1}
      flexDirection="column"
      width={70}
    >
      <text>
        <span style={{ fg: "#ff00ff", attributes: 1 }}>Answer Questions</span>
        <span style={{ fg: "#666666", attributes: 2 }}>{"  ·  esc to cancel"}</span>
      </text>
      <box height={1} />
      <text>
        <span style={{ fg: "#888888" }}>Ticket: </span>
        <span>{truncate(ticket.title, 40)}</span>
        <span style={{ fg: "#555555", attributes: 2 }}>{` (${ticket.id.slice(0, 8)})`}</span>
      </text>

      {loading ? (
        <>
          <box height={1} />
          <text>
            <span style={{ fg: "#888888", attributes: 2 }}>Loading questions…</span>
          </text>
        </>
      ) : allDone ? (
        <>
          <box height={1} />
          <text>
            <span style={{ fg: "#00cc00", attributes: 1 }}>
              {answers.length === 0
                ? "No pending questions."
                : `All ${answers.length} answer(s) ready.`}
            </span>
          </text>
          <box height={1} />
          <text>
            <span style={{ fg: "#888888" }}>
              {ticket.status === "needs_info"
                ? "Press Enter to submit answers and resume ticket"
                : "Press Esc to close"}
            </span>
          </text>
        </>
      ) : currentQ ? (
        <>
          <box height={1} />
          <text>
            <span style={{ fg: "#888888" }}>
              {`Question ${currentIdx + 1}/${questions.length}`}
            </span>
            <span style={{ fg: "#555555" }}>{` · from ${currentQ.author}`}</span>
          </text>
          <box height={1} />
          <text>
            <span style={{ fg: "#ffcc00", attributes: 1 }}>Q: </span>
            <span style={{ fg: "#ffffff" }}>{currentQ.content}</span>
          </text>
          <box height={1} />
          <text>
            <span style={{ fg: "#00cc88" }}>A: </span>
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
              {submitting ? "Submitting…" : "Type your answer, Enter to continue"}
            </span>
          </text>
        </>
      ) : null}
    </box>
  )
}
