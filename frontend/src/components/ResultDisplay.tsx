import { useState } from 'react'
import { CheckCircle2, FileText, XCircle } from 'lucide-react'
import type { TaskResult } from '@/types'
import type { TrackedStep } from '@/hooks/useAgentRun'

/**
 * Final result banner + "any file created". There's no file-serving
 * endpoint (deliberately out of scope), so "Open <file>" expands a
 * preview instead of faking a download.
 * file_system's own StepUpdate.content carries the actual saved content
 * (whether generated fresh or chained from a prior step -- see
 * src/agent/executor.py) separately from `message`'s short "Saved to X"
 * confirmation, so no need to go hunting through other steps for it.
 */
export function ResultDisplay({ result, steps }: { result: TaskResult; steps: TrackedStep[] }) {
  const [previewOpen, setPreviewOpen] = useState(false)

  const fileStep = steps.find((s) => s.update.tool === 'file_system' && s.update.status === 'done')
  const savedFile = fileStep ? /Saved to (.+)$/.exec(fileStep.update.message)?.[1] : null
  const preview = fileStep?.update.content ?? null

  if (!result.success) {
    return (
      <div className="rounded-xl border border-red-900/50 bg-red-950/30 p-4">
        <div className="flex items-center gap-2 font-medium text-red-400">
          <XCircle className="size-4.5" />
          Task Failed
        </div>
        <p className="mt-1 text-sm text-red-300/80">{result.summary}</p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-emerald-900/50 bg-emerald-950/30 p-4">
      <div className="flex items-center gap-2 font-medium text-emerald-400">
        <CheckCircle2 className="size-4.5" />
        Task Completed Successfully!
      </div>
      <p className="mt-1 text-sm text-emerald-300/80">{result.summary}</p>

      {savedFile && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setPreviewOpen((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-emerald-500"
          >
            <FileText className="size-3.5" />
            {previewOpen ? 'Hide' : 'Open'} {savedFile}
          </button>
          {previewOpen && (
            <pre className="mt-2 max-h-64 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs whitespace-pre-wrap text-zinc-400">
              {preview ?? '(no preview available for this step)'}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
