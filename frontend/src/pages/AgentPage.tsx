import { useRef, useState } from 'react'
import { Sidebar } from '@/components/Sidebar'
import { ChatPanel } from '@/components/ChatPanel'
import { ExecutionPanel } from '@/components/ExecutionPanel'
import { HistoryPage } from '@/pages/HistoryPage'
import { ToolsPage } from '@/pages/ToolsPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { useAgentRun } from '@/hooks/useAgentRun'
import { formatElapsed } from '@/lib/time'
import type { View } from '@/lib/navigation'

/**
 * The main page: sidebar nav + whichever view is active. "chat" is the
 * original wow-factor layout (task input -> live step cards -> result);
 * the other 4 nav destinations are simpler single-panel pages. No router
 * library — 5 destinations, all within one page, don't need one.
 */
export function AgentPage() {
  const { goal, steps, result, chatReply, status, error, now, run, reset } = useAgentRun()
  const submittedAtRef = useRef<number | null>(null)
  const [view, setView] = useState<View>('chat')

  function handleSubmit(newGoal: string) {
    submittedAtRef.current = Date.now()
    void run(newGoal)
  }

  function handleNewTask() {
    submittedAtRef.current = null
    reset()
    setView('chat')
  }

  const totalElapsed =
    steps.length > 0 ? formatElapsed((steps[steps.length - 1].endedAt ?? now) - steps[0].startedAt) : '00:00'

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      <Sidebar
        activeGoal={goal}
        stepCount={steps.length}
        elapsed={totalElapsed}
        activeView={view}
        onNavigate={setView}
        onNewTask={handleNewTask}
      />

      {view === 'chat' ? (
        <div className="grid flex-1 grid-cols-2 divide-x divide-zinc-800">
          <ChatPanel
            goal={goal}
            submittedAt={submittedAtRef.current}
            status={status}
            steps={steps}
            chatReply={chatReply}
            error={error}
            onSubmit={handleSubmit}
          />
          <ExecutionPanel steps={steps} result={result} now={now} />
        </div>
      ) : (
        <div className="flex-1">
          {view === 'history' && <HistoryPage />}
          {view === 'tools' && <ToolsPage />}
          {view === 'settings' && <SettingsPage />}
        </div>
      )}
    </div>
  )
}
