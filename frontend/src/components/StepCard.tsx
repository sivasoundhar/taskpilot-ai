import { useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronUp, Loader2, XCircle } from 'lucide-react'
import { TOOL_META } from '@/lib/tools'
import { formatElapsed } from '@/lib/time'
import type { TrackedStep } from '@/hooks/useAgentRun'

/**
 * One tool-use card: icon + tool name + action + status. States: running
 * (spinner) -> done (checkmark) / failed (X). This is the key
 * component -- the whole "wow factor" is watching these fill in live.
 */
export function StepCard({ step, now }: { step: TrackedStep; now: number }) {
  const { update, description, startedAt, endedAt } = step
  const meta = TOOL_META[update.tool]
  const Icon = meta.icon
  const elapsed = formatElapsed((endedAt ?? now) - startedAt)

  return (
    // min-w-0 is load-bearing: this card sits in a flex row (StepList),
    // and a flex child's default min-width is `auto` (its content size),
    // not 0 -- without this, long step output (a full search-results
    // dump) pushes the card wider instead of the text inside it
    // truncating, breaking layout with a horizontal scrollbar. Found
    // live testing a real 3-tool run, not something a short mock string
    // would have caught.
    <div className="min-w-0 flex-1 rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-violet-500/15 text-violet-400">
            <Icon className="size-4.5" />
          </div>
          <div className="truncate font-medium text-zinc-100">{meta.label}</div>
        </div>
        <span className="shrink-0 font-mono text-xs text-zinc-500">{elapsed}</span>
      </div>

      <p className="mt-2 truncate text-sm text-zinc-400">{description}</p>

      <StatusLine status={update.status} message={update.message} result={update.result} />
    </div>
  )
}

function StatusLine({
  status,
  message,
  result,
}: {
  status: TrackedStep['update']['status']
  message: string
  result: string | null
}) {
  const [expanded, setExpanded] = useState(false)

  // The step card's own status line only shows a short message (the
  // backend truncates it to 200 chars) -- `result` carries the full,
  // untruncated output regardless of tool, but nothing rendered it
  // anywhere before this. Found live: a web-search-only task (no
  // file_system step to chain into) had no way to see its own results
  // beyond that short snippet. Only worth an expand toggle when there's
  // actually more to see than the message already shows.
  const hasMore = status === 'done' && !!result && result.length > message.length

  if (status === 'running') {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-sm text-violet-400">
        <Loader2 className="size-3.5 shrink-0 animate-spin" />
        <span>Running…</span>
      </div>
    )
  }

  if (status === 'failed') {
    return (
      <div className="mt-2 flex min-w-0 items-center gap-1.5 text-sm text-red-400">
        <XCircle className="size-3.5 shrink-0" />
        <span className="min-w-0 truncate">{message}</span>
      </div>
    )
  }

  return (
    <div className="mt-2">
      <div className="flex min-w-0 items-center gap-1.5 text-sm text-emerald-400">
        <CheckCircle2 className="size-3.5 shrink-0" />
        <span className="min-w-0 truncate">{message}</span>
        {hasMore && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-auto flex shrink-0 items-center gap-0.5 rounded px-1.5 py-0.5 text-xs text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-300"
          >
            {expanded ? (
              <>
                Hide <ChevronUp className="size-3" />
              </>
            ) : (
              <>
                View full <ChevronDown className="size-3" />
              </>
            )}
          </button>
        )}
      </div>
      {expanded && result && (
        <pre className="mt-2 max-h-[32rem] overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-4 text-sm whitespace-pre-wrap text-zinc-300">
          {result}
        </pre>
      )}
    </div>
  )
}
