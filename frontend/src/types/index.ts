/**
 * TypeScript mirrors of src/models.py's Pydantic schemas — the single
 * source of truth is the backend; keep these in sync by hand whenever
 * models.py changes (small enough surface that codegen isn't worth it
 * yet).
 */

/** The 3 tools — fixed, do not add more. */
export type ToolName = 'web_search' | 'file_system' | 'code_execution'

export type StepStatus = 'pending' | 'running' | 'done' | 'failed'

/** What the frontend sends to POST /run. */
export interface TaskRequest {
  goal: string
}

/** One streamed SSE event describing a step's progress (event: "step"). */
export interface StepUpdate {
  step_number: number
  tool: ToolName
  status: StepStatus
  message: string
  result: string | null
  /** Full content behind a file_system save (generated or chained from a
   * prior step) -- distinct from `message`'s short "Saved to X"
   * confirmation, for previewing what's actually in the file. */
  content: string | null
}

/** The final SSE event once the plan completes or stops (event: "result"). */
export interface TaskResult {
  goal: string
  success: boolean
  summary: string
  steps_completed: number
  steps_total: number
}

/** A plain conversational reply — sent instead of any step/result when
 * the input wasn't an actionable task (event: "chat"). See
 * src/agent/planner.py's check_intent(). */
export interface ChatMessage {
  message: string
}

/** A parsed SSE frame from POST /run before its `data` is decoded. */
export type RunEvent =
  | { event: 'step'; data: StepUpdate }
  | { event: 'result'; data: TaskResult }
  | { event: 'chat'; data: ChatMessage }
  | { event: 'error'; data: string }

/** One entry from GET /history — a previously completed task. */
export interface HistoryRun {
  id: number
  goal: string
  success: boolean
  summary: string
  steps_completed: number
  steps_total: number
  steps: { tool: ToolName; status: StepStatus; message: string }[]
  created_at: string
}

/** GET /settings — read-only, secrets never included (only whether
 * they're configured). See src/main.py's settings_view(). */
export type LlmProviderPreference = 'groq_first' | 'ollama_first'

export interface BackendSettings {
  app_env: string
  log_level: string
  groq_model: string
  ollama_model: string
  files_sandbox_dir: string
  cors_origins: string[]
  groq_api_key_configured: boolean
  tavily_api_key_configured: boolean
  /** Live check, not just "configured" -- Ollama needs no key, so whether
   * the fallback would actually work right now is the only meaningful
   * status for it. See src/agent/llm_provider.py's is_ollama_reachable. */
  ollama_reachable: boolean
  /** The few knobs actually editable live, no restart -- PATCH /settings.
   * See src/runtime_settings.py. */
  llm_provider_preference: LlmProviderPreference
  web_search_max_results: number
}

/** What PATCH /settings accepts -- every field optional, only what
 * actually changed is sent. */
export interface RuntimeSettingsUpdate {
  llm_provider_preference?: LlmProviderPreference
  log_level?: string
  web_search_max_results?: number
}
