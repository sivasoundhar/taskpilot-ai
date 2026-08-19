import { StepList } from '@/components/StepList'
import { ResultDisplay } from '@/components/ResultDisplay'
import type { TrackedStep } from '@/hooks/useAgentRun'
import type { TaskResult } from '@/types'

interface ExecutionPanelProps {
  steps: TrackedStep[]
  result: TaskResult | null
  now: number
}

export function ExecutionPanel({ steps, result, now }: ExecutionPanelProps) {
  const doneCount = steps.filter((s) => s.update.status === 'done').length

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-4">
        <h2 className="font-semibold text-zinc-100">Execution Steps</h2>
        {steps.length > 0 && (
          <span className="rounded-full bg-zinc-900 px-2.5 py-1 text-xs font-medium text-zinc-400">
            {result ? `${result.steps_completed}/${result.steps_total}` : `${doneCount}/${steps.length}`}{' '}
            completed
          </span>
        )}
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {steps.length === 0 && (
          <p className="mt-8 text-center text-sm text-zinc-500">Steps will appear here as the agent works.</p>
        )}
        <StepList steps={steps} now={now} />
        {result && <ResultDisplay result={result} steps={steps} />}
      </div>
    </div>
  )
}
