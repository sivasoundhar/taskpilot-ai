import { Search, Code2, FolderOpen, type LucideIcon } from 'lucide-react'
import type { ToolName } from '@/types'

interface ToolMeta {
  label: string
  icon: LucideIcon
}

/** Icon + display label per tool — the "🔍/💻/📁" from the original design
 * mockup, as lucide-react components instead of emoji so they match the
 * mockup's outlined-icon style. */
export const TOOL_META: Record<ToolName, ToolMeta> = {
  web_search: { label: 'Web Search', icon: Search },
  code_execution: { label: 'Code Execution', icon: Code2 },
  file_system: { label: 'File System', icon: FolderOpen },
}
