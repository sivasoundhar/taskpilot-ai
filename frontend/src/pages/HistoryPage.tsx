import { useEffect, useState } from 'react'
import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { fetchHistory } from '@/services/api'
import { TOOL_META } from '@/lib/tools'
import type { HistoryRun } from '@/types'

/** Backs the "History" nav item -- past completed tasks. Used to also
 * back a separate "Tasks" item pointing at the same data; removed
 * rather than keep two labels for one thing. */
export function HistoryPage() {
  const [runs, setRuns] = useState<HistoryRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchHistory()
      .then((data) => {
        if (!cancelled) setRuns(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load history.')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-5 py-4">
        <h2 className="font-semibold text-zinc-100">Task History</h2>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-5">
        {error && <p className="text-sm text-red-400">{error}</p>}

        {!error && runs === null && (
          <div className="flex items-center gap-2 text-sm text-zinc-500">
            <Loader2 className="size-4 animate-spin" />
            Loading history…
          </div>
        )}

        {runs?.length === 0 && (
          <p className="mt-8 text-center text-sm text-zinc-500">
            No completed tasks yet — run something from Chat and it'll show up here.
          </p>
        )}

        {runs?.map((run) => <HistoryRunCard key={run.id} run={run} />)}
      </div>
    </div>
  )
}

function HistoryRunCard({ run }: { run: HistoryRun }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-zinc-100">{run.goal}</p>
        {run.success ? (
          <CheckCircle2 className="size-4 shrink-0 text-emerald-400" />
        ) : (
          <XCircle className="size-4 shrink-0 text-red-400" />
        )}
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        {new Date(run.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })} ·{' '}
        {run.steps_completed}/{run.steps_total} steps
      </p>
      {run.steps.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {run.steps.map((step, i) => {
            const Icon = TOOL_META[step.tool]?.icon
            return (
              <span
                key={i}
                className="inline-flex items-center gap-1 rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400"
              >
                {Icon && <Icon className="size-3" />}
                {TOOL_META[step.tool]?.label ?? step.tool}
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}
