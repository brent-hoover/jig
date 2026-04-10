// @ts-nocheck
import { useState, useRef, useEffect } from "react"
import { PhasePanel } from "./phase-panel"
import { MessageLog } from "./message-log"
import { JigStatusBar } from "./status-bar"
import { useJigSocket } from "./use-jig-socket"

interface AppProps {
  useKeyboard: any
  wsUrl: string
  onQuit: () => void
}

export function App({ useKeyboard, wsUrl, onQuit }: AppProps) {
  const state = useJigSocket(wsUrl)
  const [scrollOffset, setScrollOffset] = useState(0)
  const prevMessageCount = useRef(0)

  // Auto-scroll to bottom when new messages arrive (if already at bottom)
  useEffect(() => {
    if (state.messages.length > prevMessageCount.current && scrollOffset === 0) {
      // Already at bottom, stay there
    } else if (state.messages.length > prevMessageCount.current && scrollOffset > 0) {
      // New messages arrived while scrolled up — keep position stable
      setScrollOffset((prev) => prev + (state.messages.length - prevMessageCount.current))
    }
    prevMessageCount.current = state.messages.length
  }, [state.messages.length])

  useKeyboard((event: any) => {
    if (event.name === "q" || event.name === "escape") {
      onQuit()
    }
    if (event.name === "k" || event.name === "up") {
      setScrollOffset((prev) => Math.min(prev + 5, Math.max(0, state.messages.length - 1)))
    }
    if (event.name === "j" || event.name === "down") {
      setScrollOffset((prev) => Math.max(0, prev - 5))
    }
    // Jump to bottom
    if (event.name === "g") {
      setScrollOffset(0)
    }
    // Jump to top
    if (event.name === "G") {
      setScrollOffset(Math.max(0, state.messages.length - 1))
    }
  })

  return (
    <box flexDirection="column" flexGrow={1}>
      <box height={1} paddingX={1}>
        <text>
          <span style={{ fg: "#00aaff", attributes: 1 }}>jig</span>
          <span style={{ attributes: 2 }}>{" — agent harness"}</span>
        </text>
      </box>
      <box flexGrow={1} flexDirection="row" gap={1} paddingX={1}>
        <box width={30} flexShrink={0}>
          <PhasePanel phases={state.phases} currentPhase={state.currentPhase} />
        </box>
        <box flexGrow={1}>
          <MessageLog messages={state.messages} scrollOffset={scrollOffset} />
        </box>
      </box>
      <JigStatusBar state={state} />
    </box>
  )
}
