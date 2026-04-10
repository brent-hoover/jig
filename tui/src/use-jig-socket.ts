import { useState, useEffect, useRef, useCallback } from "react"
import type { JigEvent, JigState, PhaseStatus } from "./types"
import { INITIAL_STATE } from "./types"

const RECONNECT_INTERVAL = 2000

export function useJigSocket(url: string): JigState {
  const [state, setState] = useState<JigState>(INITIAL_STATE)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleEvent = useCallback((event: JigEvent) => {
    setState((prev) => {
      switch (event.type) {
        case "workflow_started":
          return {
            ...prev,
            issueId: event.data.issue_id as string,
            workflow: event.data.workflow as string,
            status: "running",
            runStartedAt: Date.now(),
            currentAgent: null,
            messages: [...prev.messages, event],
          }
        case "phase_started": {
          const phaseName = event.data.phase as string
          const agentType = event.data.agent_type as string
          const phases = prev.phases.map((p) =>
            p.name === phaseName ? { ...p, status: "running" as const } : p,
          )
          if (!phases.find((p) => p.name === phaseName)) {
            phases.push({ name: phaseName, agentType, status: "running" })
          }
          return {
            ...prev,
            currentPhase: phaseName,
            currentAgent: null,
            phases,
            messages: [...prev.messages, event],
          }
        }
        case "phase_completed": {
          const phaseName = event.data.phase as string
          return {
            ...prev,
            currentAgent: null,
            phases: prev.phases.map((p) =>
              p.name === phaseName
                ? { ...p, status: "completed" as const }
                : p,
            ),
            messages: [...prev.messages, event],
          }
        }
        case "agent_started":
          return {
            ...prev,
            currentAgent: event.data.agent as string,
            messages: [...prev.messages, event],
          }
        case "agent_result":
          return {
            ...prev,
            currentAgent: null,
            messages: [...prev.messages, event],
          }
        case "workflow_completed":
          return {
            ...prev,
            status: "completed",
            currentAgent: null,
            messages: [...prev.messages, event],
          }
        case "workflow_paused":
          return {
            ...prev,
            status: "paused",
            currentAgent: null,
            messages: [...prev.messages, event],
          }
        case "workflow_failed":
          return {
            ...prev,
            status: "failed",
            currentAgent: null,
            messages: [...prev.messages, event],
          }
        default:
          return { ...prev, messages: [...prev.messages, event] }
      }
    })
  }, [])

  const connect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
    }

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
      // Auto-reconnect
      reconnectTimer.current = setTimeout(connect, RECONNECT_INTERVAL)
    }

    ws.onerror = () => {
      // onclose will fire after this, triggering reconnect
    }

    ws.onmessage = (msg) => {
      try {
        handleEvent(JSON.parse(msg.data))
      } catch {
        // ignore malformed messages
      }
    }
  }, [url, handleEvent])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
      }
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [connect])

  return state
}
