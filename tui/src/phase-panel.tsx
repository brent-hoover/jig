// @ts-nocheck
import type { PhaseStatus } from "./types"

const ICONS: Record<PhaseStatus["status"], string> = {
  pending: "○",
  running: "◉",
  completed: "✓",
  failed: "✗",
}

const COLORS: Record<PhaseStatus["status"], string> = {
  pending: "#666666",
  running: "#ffcc00",
  completed: "#00cc00",
  failed: "#cc0000",
}

interface PhasePanelProps {
  phases: PhaseStatus[]
  currentPhase: string | null
}

export function PhasePanel({ phases, currentPhase }: PhasePanelProps) {
  if (phases.length === 0) {
    return (
      <box border borderStyle="rounded" borderColor="#444444" paddingX={1}>
        <text>
          <span style={{ fg: "#666666", attributes: 2 }}>
            Waiting for workflow...
          </span>
        </text>
      </box>
    )
  }

  return (
    <box
      border
      borderStyle="rounded"
      borderColor="#444444"
      paddingX={1}
      flexDirection="column"
    >
      <text>
        <span style={{ attributes: 1 }}>Workflow Progress</span>
      </text>
      <box height={1} />
      {phases.map((phase) => (
        <box key={phase.name}>
          <text>
            <span style={{ fg: COLORS[phase.status] }}>
              {ICONS[phase.status]}
            </span>
            <span>{" "}</span>
            <span
              style={{
                attributes:
                  phase.name === currentPhase
                    ? 1
                    : phase.status === "pending"
                      ? 2
                      : 0,
              }}
            >
              {phase.name}
            </span>
            <span style={{ attributes: 2 }}>{` (${phase.agentType})`}</span>
          </text>
        </box>
      ))}
    </box>
  )
}
