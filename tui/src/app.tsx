// @ts-nocheck
import { useState, useEffect, useRef } from "react"
import { TicketList } from "./ticket-list"
import { TicketDetail } from "./ticket-detail"
import { EventLog } from "./event-log"
import { AgentPanel } from "./agent-panel"
import { JigStatusBar } from "./status-bar"
import { NewTicketForm } from "./new-ticket-form"
import { AnswerForm } from "./answer-form"
import { useJigSocket } from "./use-jig-socket"

interface AppProps {
  useKeyboard: any
  wsUrl: string
  onQuit: () => void
}

export function App({ useKeyboard, wsUrl, onQuit }: AppProps) {
  const {
    state,
    sendCommand,
    moveSelection,
    moveAgentSelection,
    toggleViewMode,
    openModal,
    closeModal,
    registerTitle,
  } = useJigSocket(wsUrl)

  const [scrollOffset, setScrollOffset] = useState(0)
  const [focusedPanel, setFocusedPanel] = useState<"left" | "right">("left")
  const prevEventCount = useRef(0)
  const [flashError, setFlashError] = useState<string | null>(null)
  const [detailMode, setDetailMode] = useState(false)
  const [commentIdx, setCommentIdx] = useState(0)
  const [promptScroll, setPromptScroll] = useState(0)

  // Keep scroll anchored at bottom when new events arrive if user is already
  // at the bottom; otherwise hold position relative to the oldest visible.
  useEffect(() => {
    const grew = state.events.length - prevEventCount.current
    if (grew > 0 && scrollOffset > 0) {
      setScrollOffset((s) => s + grew)
    }
    prevEventCount.current = state.events.length
  }, [state.events.length])

  const selectedTicket = state.selectedTicketId
    ? state.tickets[state.selectedTicketId]
    : null

  // Top-level keyboard handler — only fires when no modal is open. The modal
  // components install their own useKeyboard hooks; gridland's event dispatch
  // sends keypresses to every registered handler, so we gate here to avoid
  // double-processing.
  useKeyboard((key: any) => {
    if (state.modal !== null) return

    if (key.name === "q" || (key.name === "c" && key.ctrl)) {
      onQuit()
      return
    }
    if (key.name === "tab") {
      toggleViewMode()
      return
    }
    if (key.name === "h" || key.name === "left") {
      setFocusedPanel("left")
      return
    }
    if (key.name === "l" || key.name === "right") {
      setFocusedPanel("right")
      return
    }
    if (key.name === "return" && focusedPanel === "left" && !detailMode && selectedTicket) {
      setDetailMode(true)
      setCommentIdx(0)
      return
    }
    if (key.name === "escape" && detailMode) {
      setDetailMode(false)
      return
    }
    if (key.name === "k" || key.name === "up") {
      if (state.viewMode === "agents") {
        if (focusedPanel === "right") {
          setPromptScroll((s) => Math.max(0, s - 1))
        } else {
          moveAgentSelection(-1)
          setPromptScroll(0)
        }
      } else if (focusedPanel === "right") {
        setScrollOffset((s) => Math.min(s + 1, Math.max(0, state.events.length - 1)))
      } else if (detailMode) {
        setCommentIdx((i) => Math.max(0, i - 1))
      } else {
        moveSelection(-1)
      }
      return
    }
    if (key.name === "j" || key.name === "down") {
      if (state.viewMode === "agents") {
        if (focusedPanel === "right") {
          setPromptScroll((s) => s + 1) // clamped in component
        } else {
          moveAgentSelection(1)
          setPromptScroll(0)
        }
      } else if (focusedPanel === "right") {
        setScrollOffset((s) => Math.max(0, s - 1))
      } else if (detailMode) {
        setCommentIdx((i) => i + 1) // clamped in component
      } else {
        moveSelection(1)
      }
      return
    }
    if (key.name === "pageup") {
      if (state.viewMode === "agents") {
        setPromptScroll((s) => Math.max(0, s - 20))
      } else {
        setScrollOffset((s) =>
          Math.min(s + 10, Math.max(0, state.events.length - 1)),
        )
      }
      return
    }
    if (key.name === "pagedown") {
      if (state.viewMode === "agents") {
        setPromptScroll((s) => s + 20)
      } else {
        setScrollOffset((s) => Math.max(0, s - 10))
      }
      return
    }
    if (key.name === "g") {
      if (state.viewMode === "agents") {
        setPromptScroll(0)
      } else {
        setScrollOffset(0)
      }
      return
    }
    if (key.name === "n") {
      openModal({ kind: "new_ticket" })
      return
    }
    if (key.name === "a") {
      if (selectedTicket && selectedTicket.status === "needs_info") {
        openModal({ kind: "answer", ticketId: selectedTicket.id })
      }
      return
    }
    if (key.name === "r") {
      if (selectedTicket && selectedTicket.status === "needs_info") {
        sendCommand("update_ticket", {
          ticket_id: selectedTicket.id,
          status: "in_progress",
        })
      }
      return
    }
  })

  const displayState = flashError
    ? { ...state, lastError: flashError }
    : state

  return (
    <box flexDirection="column" flexGrow={1}>
      <box height={1} paddingX={1}>
        <text>
          <span style={{ fg: "#00aaff", attributes: 1 }}>jig</span>
          <span style={{ attributes: 2 }}>{" — ticket control"}</span>
        </text>
      </box>

      <box flexGrow={1} flexDirection="row" gap={1} paddingX={1}>
        {state.viewMode === "agents" ? (
          <AgentPanel
            agents={state.agents}
            workflow={state.workflow}
            selectedIdx={state.selectedAgentIdx}
            selectedTicketId={state.selectedTicketId}
            sendCommand={sendCommand}
            promptScroll={promptScroll}
          />
        ) : (
          <>
            <box width={40} flexShrink={0} flexDirection="column">
              <TicketList
                tickets={state.tickets}
                selectedId={state.selectedTicketId}
                focused={focusedPanel === "left"}
              />
              {selectedTicket ? (
                <TicketDetail
                  ticket={selectedTicket}
                  events={state.events}
                  detailMode={detailMode}
                  commentIdx={commentIdx}
                />
              ) : null}
            </box>
            <box flexGrow={1}>
              <EventLog
                events={state.events}
                scrollOffset={scrollOffset}
                focused={focusedPanel === "right"}
              />
            </box>
          </>
        )}
      </box>

      <JigStatusBar state={displayState} focusedPanel={focusedPanel} />

      {state.modal?.kind === "new_ticket" ? (
        <box
          position="absolute"
          top={3}
          left={4}
          zIndex={10}
        >
          <NewTicketForm
            useKeyboard={useKeyboard}
            sendCommand={sendCommand}
            onDone={(ticketId, title, type) => {
              registerTitle(ticketId, title, type)
              closeModal()
              setFlashError(null)
            }}
            onCancel={(message) => {
              closeModal()
              if (message) {
                setFlashError(message)
                setTimeout(() => setFlashError(null), 4000)
              }
            }}
          />
        </box>
      ) : null}

      {state.modal?.kind === "answer" &&
      state.tickets[state.modal.ticketId] ? (
        <box
          position="absolute"
          top={3}
          left={4}
          zIndex={10}
        >
          <AnswerForm
            useKeyboard={useKeyboard}
            sendCommand={sendCommand}
            ticket={state.tickets[state.modal.ticketId]}
            onDone={() => {
              closeModal()
              setFlashError(null)
            }}
            onCancel={(message) => {
              closeModal()
              if (message) {
                setFlashError(message)
                setTimeout(() => setFlashError(null), 4000)
              }
            }}
          />
        </box>
      ) : null}
    </box>
  )
}
