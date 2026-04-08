// @ts-nocheck
import { PhasePanel } from "./phase-panel"
import { MessageLog } from "./message-log"
import { JigStatusBar } from "./status-bar"
import { useJigSocket } from "./use-jig-socket"

interface AppProps {
  useKeyboard: any
  wsUrl: string
}

export function App({ useKeyboard, wsUrl }: AppProps) {
  const state = useJigSocket(wsUrl)

  useKeyboard((event: any) => {
    if (event.name === "q" || event.name === "escape") {
      process.exit(0)
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
          <MessageLog messages={state.messages} />
        </box>
      </box>
      <JigStatusBar state={state} />
    </box>
  )
}
