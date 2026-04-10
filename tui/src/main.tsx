// @ts-nocheck
import { createCliRenderer, createRoot, useKeyboard } from "@gridland/bun"
import { App } from "./app"

const DEFAULT_WS_URL = "ws://127.0.0.1:9100"

async function main() {
  const wsUrl = process.argv[2] || DEFAULT_WS_URL
  const renderer = await createCliRenderer({ exitOnCtrlC: false })

  function cleanup() {
    renderer.destroy()
    process.exit(0)
  }

  // Restore terminal on signals
  process.on("SIGINT", cleanup)
  process.on("SIGTERM", cleanup)

  createRoot(renderer).render(
    <App useKeyboard={useKeyboard} wsUrl={wsUrl} onQuit={cleanup} />
  )
}

main().catch((err) => {
  console.error("Failed to start TUI:", err)
  process.exit(1)
})
