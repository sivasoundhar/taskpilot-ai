import axios from 'axios'
import type {
  BackendSettings,
  ChatMessage,
  HistoryRun,
  RunEvent,
  RuntimeSettingsUpdate,
  StepUpdate,
  TaskResult,
} from '@/types'

/**
 * NOTE on SSE + POST: native browser `EventSource` only supports GET
 * requests, and POST /run needs a JSON body (the goal). Rather than
 * force the goal into a query string, this reads the streamed response
 * body by hand and parses SSE frames itself -- the standard workaround
 * for POST+SSE. Axios is still used for the plain /health check below.
 */

/**
 * Streams POST /run's SSE events as they arrive. Usage:
 *   for await (const event of streamRun(goal)) { ... }
 */
export async function* streamRun(goal: string, signal?: AbortSignal): AsyncGenerator<RunEvent> {
  const response = await fetch('/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal }),
    signal,
  })

  if (!response.ok || !response.body) {
    throw new Error(`POST /run failed: ${response.status} ${response.statusText}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      // Normalize CRLF ("\r\n", what sse-starlette actually sends) to LF
      // up front so every frame/line boundary check below only has to
      // handle one line-ending style.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const parsed = parseFrame(frame)
        if (parsed) yield parsed
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function parseFrame(frame: string): RunEvent | null {
  let eventType = ''
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      eventType = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      data += line.slice('data:'.length).trim()
    }
  }

  switch (eventType) {
    case 'step':
      return { event: 'step', data: JSON.parse(data) as StepUpdate }
    case 'result':
      return { event: 'result', data: JSON.parse(data) as TaskResult }
    case 'chat':
      return { event: 'chat', data: JSON.parse(data) as ChatMessage }
    case 'error':
      return { event: 'error', data }
    default:
      return null // unrecognized frame (e.g. a keep-alive ping) -- skip
  }
}

export async function checkHealth(): Promise<{ status: string; env: string }> {
  const response = await axios.get('/health')
  return response.data
}

/** Backs the Tasks/History nav pages -- same data, no meaningful
 * difference between them for this project's scope. */
export async function fetchHistory(): Promise<HistoryRun[]> {
  const response = await axios.get<HistoryRun[]>('/history')
  return response.data
}

/** Backs the Settings nav page. */
export async function fetchSettings(): Promise<BackendSettings> {
  const response = await axios.get<BackendSettings>('/settings')
  return response.data
}

/** The few Settings-page controls that are actually live-editable (LLM
 * provider preference, log level, search result count) -- takes effect
 * immediately backend-side, no restart. See src/runtime_settings.py. */
export async function updateSettings(update: RuntimeSettingsUpdate): Promise<BackendSettings> {
  const response = await axios.patch<BackendSettings>('/settings', update)
  return response.data
}
