import { useState, useEffect, useRef, useCallback } from "react"
import type { JigEvent, JigState, PhaseStatus } from "./types"
import { INITIAL_STATE } from "./types"

export function useJigSocket(url: string): JigState {
  const [state, setState] = useState<JigState>(INITIAL_STATE)
  const wsRef = useRef<WebSocket | null>(null)

  const handleEvent = useCallback((event: JigEvent) => {
    setState((prev) => {
      switch (event.type) {
        case "workflow_started":
          return {
            ...prev,
            issueId: event.data.issue_id as string,
            workflow: event.data.workflow as string,
            status: "running",
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
            phases,
            messages: [...prev.messages, event],
          }
        }
        case "phase_completed": {
          const phaseName = event.data.phase as string
          return {
            ...prev,
            phases: prev.phases.map((p) =>
              p.name === phaseName
                ? { ...p, status: "completed" as const }
                : p,
            ),
            messages: [...prev.messages, event],
          }
        }
        case "workflow_completed":
          return {
            ...prev,
            status: "completed",
            messages: [...prev.messages, event],
          }
        case "workflow_paused":
          return {
            ...prev,
            status: "paused",
            messages: [...prev.messages, event],
          }
        case "workflow_failed":
          return {
            ...prev,
            status: "failed",
            messages: [...prev.messages, event],
          }
        default:
          return { ...prev, messages: [...prev.messages, event] }
      }
    })
  }, [])

  useEffect(() => {
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => setState((prev) => ({ ...prev, connected: true }))
    ws.onclose = () => setState((prev) => ({ ...prev, connected: false }))
    ws.onmessage = (msg) => {
      try {
        handleEvent(JSON.parse(msg.data))
      } catch {
        // ignore malformed messages
      }
    }

    return () => {
      ws.close()
    }
  }, [url, handleEvent])

  return state
}
