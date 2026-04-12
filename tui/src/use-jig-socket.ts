// @ts-nocheck
import { useState, useEffect, useRef, useCallback } from "react"
import type { AppState, JigEvent, ModalState, Ticket, TicketType } from "./types"
import { INITIAL_STATE } from "./types"

const RECONNECT_INTERVAL = 2000

// A pending command: the socket sends a request and keeps a promise in a
// FIFO queue. The backend replies in order with `{ok: true, ...}` or
// `{ok: false, error}` — we have no correlation IDs, so ordering is all we've
// got. That's fine because we only send one command at a time from the UI.
type Pending = {
  resolve: (reply: Reply) => void
  reject: (err: Error) => void
}

export type Reply =
  | { ok: true; ticket_id?: string; comment_id?: string; status?: string }
  | { ok: false; error: string }

export type SendCommand = (
  command: string,
  args: Record<string, unknown>,
) => Promise<Reply>

export interface SocketHandle {
  state: AppState
  sendCommand: SendCommand
  selectTicket: (id: string | null) => void
  moveSelection: (delta: number) => void
  openModal: (modal: NonNullable<ModalState>) => void
  closeModal: () => void
  // Titles travel in-band with create_ticket commands; this lets the caller
  // inject them into the ticket map once the server acks the ticket_id.
  registerTitle: (ticketId: string, title: string, type: TicketType) => void
  clearError: () => void
}

function bumpTicket(
  tickets: Record<string, Ticket>,
  id: string,
  patch: Partial<Ticket>,
): Record<string, Ticket> {
  const existing = tickets[id]
  const base: Ticket = existing ?? {
    id,
    type: "task",
    status: "open",
    title: id, // fallback until registerTitle or future fetch fills it
    assignee: null,
    parentId: null,
    lastActivity: Date.now(),
  }
  return {
    ...tickets,
    [id]: { ...base, ...patch, lastActivity: Date.now() },
  }
}

function reduce(state: AppState, event: JigEvent): AppState {
  const data = event.data || {}
  const ticketId = data.ticket_id as string | undefined
  const events = [...state.events, event]

  switch (event.type) {
    case "ticket_created": {
      if (!ticketId) return { ...state, events }
      const tickets = bumpTicket(state.tickets, ticketId, {
        type: (data.type as TicketType) ?? "task",
        assignee: (data.assignee as string | null) ?? null,
        parentId: (data.parent_id as string | null) ?? null,
        status: "open",
      })
      const selectedTicketId =
        state.selectedTicketId ?? ticketId
      return { ...state, tickets, events, selectedTicketId }
    }
    case "ticket_updated": {
      if (!ticketId) return { ...state, events }
      const tickets = bumpTicket(state.tickets, ticketId, {
        status: (data.status as Ticket["status"]) ?? "open",
      })
      return { ...state, tickets, events }
    }
    case "comment_posted":
    case "commit_recorded": {
      if (!ticketId) return { ...state, events }
      const tickets = bumpTicket(state.tickets, ticketId, {})
      return { ...state, tickets, events }
    }
    default:
      return { ...state, events }
  }
}

export function useJigSocket(url: string): SocketHandle {
  const [state, setState] = useState<AppState>(INITIAL_STATE)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = useRef<Pending[]>([])

  const connect = useCallback(() => {
    if (wsRef.current) wsRef.current.close()
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      setState((prev) => ({ ...prev, connected: true }))
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }

    ws.onclose = () => {
      setState((prev) => ({ ...prev, connected: false }))
      // Any in-flight command is now toast.
      while (pending.current.length > 0) {
        const p = pending.current.shift()!
        p.reject(new Error("socket closed"))
      }
      reconnectTimer.current = setTimeout(connect, RECONNECT_INTERVAL)
    }

    ws.onerror = () => {
      // onclose will fire and trigger reconnect.
    }

    ws.onmessage = (msg) => {
      let parsed: unknown
      try {
        parsed = JSON.parse(msg.data)
      } catch {
        return
      }
      if (!parsed || typeof parsed !== "object") return

      const obj = parsed as Record<string, unknown>
      // Backend events are {type, data}; command replies are {ok, ...}.
      if ("type" in obj && "data" in obj) {
        setState((prev) => reduce(prev, obj as JigEvent))
      } else if ("ok" in obj) {
        const p = pending.current.shift()
        if (p) p.resolve(obj as Reply)
      }
    }
  }, [url])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) wsRef.current.close()
    }
  }, [connect])

  const sendCommand: SendCommand = useCallback((command, args) => {
    return new Promise<Reply>((resolve, reject) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error("not connected"))
        return
      }
      pending.current.push({ resolve, reject })
      ws.send(JSON.stringify({ command, args }))
    })
  }, [])

  const selectTicket = useCallback((id: string | null) => {
    setState((prev) => ({ ...prev, selectedTicketId: id }))
  }, [])

  const moveSelection = useCallback((delta: number) => {
    setState((prev) => {
      const ids = Object.keys(prev.tickets).sort((a, b) => {
        return prev.tickets[b].lastActivity - prev.tickets[a].lastActivity
      })
      if (ids.length === 0) return prev
      const currentIdx = prev.selectedTicketId
        ? ids.indexOf(prev.selectedTicketId)
        : -1
      const nextIdx = Math.max(
        0,
        Math.min(ids.length - 1, (currentIdx < 0 ? 0 : currentIdx) + delta),
      )
      return { ...prev, selectedTicketId: ids[nextIdx] }
    })
  }, [])

  const openModal = useCallback((modal: NonNullable<ModalState>) => {
    setState((prev) => ({ ...prev, modal }))
  }, [])

  const closeModal = useCallback(() => {
    setState((prev) => ({ ...prev, modal: null, lastError: null }))
  }, [])

  const registerTitle = useCallback(
    (ticketId: string, title: string, type: TicketType) => {
      setState((prev) => ({
        ...prev,
        tickets: bumpTicket(prev.tickets, ticketId, { title, type }),
        selectedTicketId: prev.selectedTicketId ?? ticketId,
      }))
    },
    [],
  )

  const clearError = useCallback(() => {
    setState((prev) => ({ ...prev, lastError: null }))
  }, [])

  return {
    state,
    sendCommand,
    selectTicket,
    moveSelection,
    openModal,
    closeModal,
    registerTitle,
    clearError,
  }
}
