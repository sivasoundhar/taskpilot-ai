import { Bot, Plus } from 'lucide-react'
import { NAV_ITEMS, type View } from '@/lib/navigation'

/**
 * Design 1 ("Dark Modern") sidebar: brand, New Task, nav, the active
 * task summary, and a footer identity block. All 5 nav destinations are
 * real now (Chat, Tasks/History -> the same page, Tools, Settings).
 */
export function Sidebar({
  activeGoal,
  stepCount,
  elapsed,
  activeView,
  onNavigate,
  onNewTask,
}: {
  activeGoal: string | null
  stepCount: number
  elapsed: string
  activeView: View
  onNavigate: (view: View) => void
  onNewTask: () => void
}) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950 p-4">
      <div className="flex items-center gap-2 px-1 py-2">
        <Bot className="size-5 text-violet-500" />
        <span className="font-semibold text-zinc-100">TaskPilot AI</span>
      </div>

      <button
        type="button"
        onClick={onNewTask}
        className="mt-3 flex items-center justify-center gap-1.5 rounded-lg bg-violet-600 py-2 text-sm font-medium text-white transition hover:bg-violet-500"
      >
        <Plus className="size-4" />
        New Task
      </button>

      <nav className="mt-4 flex flex-col gap-0.5">
        {NAV_ITEMS.map(({ view, icon: Icon, label }) => (
          <button
            key={view}
            type="button"
            onClick={() => onNavigate(view)}
            className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition ${
              activeView === view
                ? 'bg-zinc-900 text-zinc-100'
                : 'text-zinc-500 hover:bg-zinc-900/50 hover:text-zinc-300'
            }`}
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <div className="flex-1" />

      {activeGoal && (
        <div className="mb-3 rounded-lg border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-xs font-medium tracking-wide text-zinc-500 uppercase">Active Task</div>
          <p className="mt-1 line-clamp-2 text-sm text-zinc-200">{activeGoal}</p>
          <div className="mt-2 flex items-center gap-3 text-xs text-zinc-500">
            <span>{stepCount} step{stepCount === 1 ? '' : 's'}</span>
            <span className="font-mono">{elapsed}</span>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 border-t border-zinc-800 pt-3">
        <div className="flex size-8 items-center justify-center rounded-full bg-zinc-800 text-xs font-medium text-zinc-300">
          AI
        </div>
        <div>
          <div className="text-sm text-zinc-200">AI Engineer</div>
          <div className="flex items-center gap-1 text-xs text-emerald-500">
            <span className="size-1.5 rounded-full bg-emerald-500" />
            Online
          </div>
        </div>
      </div>
    </aside>
  )
}
