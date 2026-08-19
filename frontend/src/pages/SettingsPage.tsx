import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { fetchSettings, updateSettings } from '@/services/api'
import type { BackendSettings, LlmProviderPreference, RuntimeSettingsUpdate } from '@/types'

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
const SEARCH_RESULT_OPTIONS = [3, 5, 8, 10]

/** Status view of backend config (env, sandbox dir, CORS, key presence --
 * genuinely read-only, config.py/`.env` is the source of truth for those)
 * plus a few knobs that are actually live-editable via PATCH /settings
 * (src/runtime_settings.py): LLM provider preference, log level, search
 * result count -- real controls, not just an info page. Live-editing the
 * rest (models, sandbox dir, CORS) stays out of scope: those need a real
 * restart-safety story this project's scope doesn't call for. */
export function SettingsPage() {
  const [settings, setSettings] = useState<BackendSettings | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchSettings()
      .then((data) => {
        if (!cancelled) setSettings(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load settings.')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-5 py-4">
        <h2 className="font-semibold text-zinc-100">Settings</h2>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {error && <p className="text-sm text-red-400">{error}</p>}

        {!error && !settings && (
          <div className="flex items-center gap-2 text-sm text-zinc-500">
            <Loader2 className="size-4 animate-spin" />
            Loading settings…
          </div>
        )}

        {settings && <SettingsForm initial={settings} />}
      </div>
    </div>
  )
}

/** Split out from SettingsPage so the "settings" state below is never
 * null after this point -- every editable control can read it directly
 * without a guard. */
function SettingsForm({ initial }: { initial: BackendSettings }) {
  const [settings, setSettings] = useState(initial)
  // Tracks which single field is mid-PATCH, so only that control shows a
  // spinner -- flipping one toggle shouldn't visually freeze the others.
  const [pendingField, setPendingField] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function save(field: string, update: RuntimeSettingsUpdate) {
    setPendingField(field)
    setSaveError(null)
    try {
      const fresh = await updateSettings(update)
      setSettings(fresh)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : `Failed to save ${field}.`)
    } finally {
      setPendingField(null)
    }
  }

  return (
    <div className="space-y-4">
      {saveError && <p className="text-sm text-red-400">{saveError}</p>}

      <Section title="LLM provider">
        <ProviderPreferenceRow
          value={settings.llm_provider_preference}
          groqModel={settings.groq_model}
          ollamaModel={settings.ollama_model}
          saving={pendingField === 'llm_provider_preference'}
          onChange={(pref) => save('llm_provider_preference', { llm_provider_preference: pref })}
        />
        <RowBool
          label="Groq API key"
          ok={settings.groq_api_key_configured}
          trueText="Configured"
          falseText="Not set"
        />
        <RowBool
          label="Ollama fallback"
          ok={settings.ollama_reachable}
          trueText="Reachable now"
          falseText="Not reachable"
        />
      </Section>

      <Section title="Agent behavior">
        <SelectRow
          label="Search results per query"
          value={String(settings.web_search_max_results)}
          options={SEARCH_RESULT_OPTIONS.map((n) => String(n))}
          saving={pendingField === 'web_search_max_results'}
          onChange={(v) => save('web_search_max_results', { web_search_max_results: Number(v) })}
        />
        <SelectRow
          label="Log level"
          value={settings.log_level}
          options={LOG_LEVELS}
          saving={pendingField === 'log_level'}
          onChange={(v) => save('log_level', { log_level: v })}
        />
      </Section>

      <Section title="App">
        <RowBool
          label="Tavily API key"
          ok={settings.tavily_api_key_configured}
          trueText="Configured"
          falseText="Not set"
        />
        <Row label="File sandbox dir" value={settings.files_sandbox_dir} />
        <Row label="Environment" value={settings.app_env} />
        <Row label="CORS origins" value={settings.cors_origins.join(', ')} />
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">{title}</h3>
      <dl className="space-y-3 text-sm">{children}</dl>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-zinc-800 pb-3 last:border-0 last:pb-0">
      <dt className="text-zinc-400">{label}</dt>
      <dd className="font-mono text-zinc-200">{value}</dd>
    </div>
  )
}

/** `ok` is deliberately generic, not "configured" -- Ollama's row uses this
 * same component for "reachable right now" (a live check), not just
 * whether a key is set (it needs none). trueText/falseText let each
 * caller say what green/red actually means for that row. */
function RowBool({
  label,
  ok,
  trueText,
  falseText,
}: {
  label: string
  ok: boolean
  trueText: string
  falseText: string
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-zinc-800 pb-3 last:border-0 last:pb-0">
      <dt className="text-zinc-400">{label}</dt>
      <dd className="flex items-center gap-1.5">
        {ok ? (
          <>
            <CheckCircle2 className="size-3.5 text-emerald-400" />
            <span className="text-emerald-400">{trueText}</span>
          </>
        ) : (
          <>
            <XCircle className="size-3.5 text-red-400" />
            <span className="text-red-400">{falseText}</span>
          </>
        )}
      </dd>
    </div>
  )
}

/** A real control: two buttons, one active, clicking the inactive one
 * PATCHes immediately -- this is what actually changes which LLM the
 * agent tries first on the next task run (src/agent/llm_provider.py). */
function ProviderPreferenceRow({
  value,
  groqModel,
  ollamaModel,
  saving,
  onChange,
}: {
  value: LlmProviderPreference
  groqModel: string
  ollamaModel: string
  saving: boolean
  onChange: (value: LlmProviderPreference) => void
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-zinc-800 pb-3 last:border-0 last:pb-0">
      <dt className="text-zinc-400">
        Try first
        {saving && <Loader2 className="ml-1.5 inline size-3 animate-spin text-zinc-500" />}
      </dt>
      <dd className="flex items-center gap-1">
        <Button
          type="button"
          size="sm"
          variant={value === 'groq_first' ? 'default' : 'outline'}
          disabled={saving}
          onClick={() => onChange('groq_first')}
          title={groqModel}
        >
          Groq
        </Button>
        <Button
          type="button"
          size="sm"
          variant={value === 'ollama_first' ? 'default' : 'outline'}
          disabled={saving}
          onClick={() => onChange('ollama_first')}
          title={ollamaModel}
        >
          Ollama
        </Button>
      </dd>
    </div>
  )
}

/** A real `<select>` bound to backend state -- changing it PATCHes
 * immediately and the row reflects whatever the backend actually
 * confirmed, not just the click. */
function SelectRow({
  label,
  value,
  options,
  saving,
  onChange,
}: {
  label: string
  value: string
  options: string[]
  saving: boolean
  onChange: (value: string) => void
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-zinc-800 pb-3 last:border-0 last:pb-0">
      <dt className="text-zinc-400">
        {label}
        {saving && <Loader2 className="ml-1.5 inline size-3 animate-spin text-zinc-500" />}
      </dt>
      <dd>
        <select
          value={value}
          disabled={saving}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 font-mono text-xs text-zinc-200 disabled:opacity-50"
        >
          {options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </dd>
    </div>
  )
}
