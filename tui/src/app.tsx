// @ts-nocheck
import { useState, useEffect, useRef } from "react"
import { TicketList } from "./ticket-list"
import { EventLog } from "./event-log"
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
    openModal,
    closeModal,
    registerTitle,
  } = useJigSocket(wsUrl)

  const [scrollOffset, setScrollOffset] = useState(0)
  const prevEventCount = useRef(0)
  const [flashError, setFlashError] = useState<string | null>(null)

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
    if (key.name === "k" || key.name === "up") {
      moveSelection(-1)
      return
    }
    if (key.name === "j" || key.name === "down") {
      moveSelection(1)
      return
    }
    if (key.name === "pageup") {
      setScrollOffset((s) =>
        Math.min(s + 10, Math.max(0, state.events.length - 1)),
      )
      return
    }
    if (key.name === "pagedown") {
      setScrollOffset((s) => Math.max(0, s - 10))
      return
    }
    if (key.name === "g") {
      // Jump to bottom of event log.
      setScrollOffset(0)
      return
    }
    if (key.name === "n") {
      openModal({ kind: "new_ticket" })
      return
    }
    if (key.name === "a") {
      // Only meaningful if the selected ticket is a user-facing question.
      if (
        selectedTicket &&
        selectedTicket.type === "question" &&
        selectedTicket.assignee === "user" &&
        selectedTicket.status !== "resolved"
      ) {
        openModal({ kind: "answer", ticketId: selectedTicket.id })
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
        <box width={40} flexShrink={0}>
          <TicketList
            tickets={state.tickets}
            selectedId={state.selectedTicketId}
          />
        </box>
        <box flexGrow={1}>
          <EventLog events={state.events} scrollOffset={scrollOffset} />
        </box>
      </box>

      <JigStatusBar state={displayState} />

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
