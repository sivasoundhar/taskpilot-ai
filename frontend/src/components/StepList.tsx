import { StepCard } from '@/components/StepCard'
import type { TrackedStep } from '@/hooks/useAgentRun'

/**
 * Renders StepCards in sequence with a connecting line down the numbered
 * dots -- the literal "watch it pick tool 1 -> 2 -> 3" visual from the
 * chosen mockup (Design 1: Dark Modern). New cards appear as each `step`
 * SSE event arrives one by one, not all at once, because
 * `steps` is just whatever's accumulated in useAgentRun so far -- no
 * extra logic needed here for the "live" part.
 */
export function StepList({ steps, now }: { steps: TrackedStep[]; now: number }) {
  return (
    <ol className="flex flex-col">
      {steps.map((step, i) => {
        const isLast = i === steps.length - 1
        return (
          <li key={step.update.step_number} className="flex min-w-0 gap-3">
            <div className="flex shrink-0 flex-col items-center">
              <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-violet-600 text-xs font-semibold text-white">
                {step.update.step_number}
              </div>
              {!isLast && <div className="my-1 w-px flex-1 bg-zinc-800" />}
            </div>
            <div className={isLast ? 'min-w-0 flex-1' : 'min-w-0 flex-1 pb-4'}>
              <StepCard step={step} now={now} />
            </div>
          </li>
        )
      })}
    </ol>
  )
}
