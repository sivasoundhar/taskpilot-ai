import { TOOL_META } from '@/lib/tools'
import type { ToolName } from '@/types'

interface ToolInfo {
  tool: ToolName
  backend: string
  apiKey: string
  description: string
}

/** Static — sourced from docs/MCP_NOTES.md, TaskPilot's actual tool
 * decisions (verified live during Days 3-5). No backend call needed
 * since this doesn't change at runtime. */
const TOOLS: ToolInfo[] = [
  {
    tool: 'web_search',
    description: 'Fetches live information from the internet.',
    backend: 'Tavily if configured, else duckduckgo-mcp-server (real MCP)',
    apiKey: 'Optional — Tavily key enables it, else runs key-free',
  },
  {
    tool: 'code_execution',
    description: 'Runs Python for math, data analysis, and logic.',
    backend: 'Sandboxed local subprocess (not MCP — see docs/MCP_NOTES.md)',
    apiKey: 'None required',
  },
  {
    tool: 'file_system',
    description: 'Reads and writes files inside a sandboxed workspace directory.',
    backend: '@modelcontextprotocol/server-filesystem (real MCP)',
    apiKey: 'None required',
  },
]

export function ToolsPage() {
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-5 py-4">
        <h2 className="font-semibold text-zinc-100">Tools</h2>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {TOOLS.map(({ tool, description, backend, apiKey }) => {
          const meta = TOOL_META[tool]
          const Icon = meta.icon
          return (
            <div key={tool} className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="flex items-center gap-3">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-violet-500/15 text-violet-400">
                  <Icon className="size-4.5" />
                </div>
                <div className="font-medium text-zinc-100">{meta.label}</div>
              </div>
              <p className="mt-2 text-sm text-zinc-400">{description}</p>
              <dl className="mt-3 space-y-1 text-xs text-zinc-500">
                <div className="flex gap-2">
                  <dt className="shrink-0 font-medium text-zinc-400">Backend:</dt>
                  <dd>{backend}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="shrink-0 font-medium text-zinc-400">API key:</dt>
                  <dd>{apiKey}</dd>
                </div>
              </dl>
            </div>
          )
        })}
      </div>
    </div>
  )
}
