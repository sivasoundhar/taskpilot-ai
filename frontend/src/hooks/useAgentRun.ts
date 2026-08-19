import { useCallback, useEffect, useRef, useState } from 'react'
import { streamRun } from '@/services/api'
import type { StepUpdate, TaskResult } from '@/types'

/** "hi" / "thanks" / etc. -- the backend detected this wasn't an
 * actionable task and sent a plain reply instead of a plan (see
 * planner.check_intent on the backend). No steps ever ran. */
export interface ChatReply {
  message: string
}

export interface TrackedStep {
  update: StepUpdate
  /** The step's action description, captured from its RUNNING message
   * ("Running web_search: Search for X" -> "Search for X") before it's
   * overwritten by the DONE/FAILED message -- the mockup shows both the
   * action ("Searching 'latest AI news'...") and the outcome ("✓ Found 8
   * articles") at once, but the backend only ever sends one `message`
   * per event. */
  description: string
  startedAt: number
  /** null while still running -- elapsed time keeps ticking against `now`. */
  endedAt: number | null
}

function extractDescription(message: string): string {
  const match = /^Running \w+: (.*)$/.exec(message)
  return match ? match[1] : message
}

export type RunStatus = 'idle' | 'running' | 'done' | 'error'

/**
 * Drives one POST /run session: submits a goal, consumes the SSE stream
 * from src/services/api.ts's streamRun(), and exposes React state the UI
 * renders live as events arrive -- this is what makes the step cards
 * appear one by one instead of all at once.
 */
export function useAgentRun() {
  const [goal, setGoal] = useState<string | null>(null)
  const [steps, setSteps] = useState<TrackedStep[]>([])
  const [result, setResult] = useState<TaskResult | null>(null)
  const [chatReply, setChatReply] = useState<ChatReply | null>(null)
  const [status, setStatus] = useState<RunStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const abortRef = useRef<AbortController | null>(null)

  // Live-ticking clock so a still-running step's timer updates every
  // second (the mockup's "00:12" counters) -- only while a run is
  // actually in flight, not forever.
  useEffect(() => {
    if (status !== 'running') return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [status])

  useEffect(() => () => abortRef.current?.abort(), [])

  const run = useCallback(async (newGoal: string) => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setGoal(newGoal)
    setSteps([])
    setResult(null)
    setChatReply(null)
    setError(null)
    setStatus('running')
    setNow(Date.now())

    try {
      for await (const event of streamRun(newGoal, controller.signal)) {
        if (event.event === 'step') {
          const update = event.data
          setSteps((prev) => {
            const existing = prev.find((s) => s.update.step_number === update.step_number)
            const startedAt = existing?.startedAt ?? Date.now()
            const endedAt = update.status === 'running' ? null : Date.now()
            // Falls back to the current message if a DONE/FAILED event
            // somehow arrives with no prior RUNNING one on record --
            // shouldn't happen given the backend's contract, but safer
            // than a non-null assertion that could crash the UI.
            const description =
              update.status === 'running'
                ? extractDescription(update.message)
                : (existing?.description ?? update.message)
            const rest = prev.filter((s) => s.update.step_number !== update.step_number)
            return [...rest, { update, description, startedAt, endedAt }].sort(
              (a, b) => a.update.step_number - b.update.step_number,
            )
          })
        } else if (event.event === 'result') {
          setResult(event.data)
          setStatus('done')
        } else if (event.event === 'chat') {
          setChatReply(event.data)
          setStatus('done')
        } else if (event.event === 'error') {
          setError(event.data)
          setStatus('error')
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return
      setError(err instanceof Error ? err.message : 'Something went wrong.')
      setStatus('error')
    }
  }, [])

  /** Aborts any in-flight run and clears everything back to the empty
   * "idle" state -- what the sidebar's "New Task" button calls. */
  const reset = useCallback(() => {
    abortRef.current?.abort()
    setGoal(null)
    setSteps([])
    setResult(null)
    setChatReply(null)
    setError(null)
    setStatus('idle')
  }, [])

  return { goal, steps, result, chatReply, status, error, now, run, reset }
}
