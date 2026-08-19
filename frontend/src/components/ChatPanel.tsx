import type { ReactNode } from 'react'
import { TaskInput } from '@/components/TaskInput'
import type { ChatReply, RunStatus, TrackedStep } from '@/hooks/useAgentRun'

interface ChatPanelProps {
  goal: string | null
  submittedAt: number | null
  status: RunStatus
  steps: TrackedStep[]
  chatReply: ChatReply | null
  error: string | null
  onSubmit: (goal: string) => void
}

/**
 * The mockup's Agent bubble text ("Planning... (3 steps identified)")
 * assumes knowing the total step count up front. The backend only
 * streams step-by-step events -- it doesn't send the plan size before
 * the first step starts -- so this is an honest equivalent using only
 * what's actually known at each point, not a fabricated number.
 *
 * `chatReply` takes priority when set: the backend decided the input
 * wasn't an actionable task (a greeting, small talk -- see
 * planner.check_intent) and sent a plain reply instead of ever planning.
 */
function agentStatusText(
  status: RunStatus,
  steps: TrackedStep[],
  chatReply: ChatReply | null,
  error: string | null,
): string {
  if (chatReply) return chatReply.message
  if (status === 'error') return error ?? 'Something went wrong.'
  if (status === 'idle') return ''
  if (steps.length === 0) return 'Planning your task…'
  const latest = steps[steps.length - 1]
  if (status === 'running') return `Running step ${latest.update.step_number}: ${latest.description}`
  return `Done — completed ${steps.filter((s) => s.update.status === 'done').length}/${steps.length} step(s).`
}

export function ChatPanel({ goal, submittedAt, status, steps, chatReply, error, onSubmit }: ChatPanelProps) {
  const agentText = agentStatusText(status, steps, chatReply, error)

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-5 py-4">
        <h2 className="font-semibold text-zinc-100">Chat</h2>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {goal && (
          <ChatBubble author="You" timestamp={submittedAt}>
            {goal}
          </ChatBubble>
        )}
        {goal && agentText && (
          <ChatBubble author="Agent" timestamp={submittedAt} tone="agent">
            {agentText}
          </ChatBubble>
        )}
        {!goal && (
          <p className="mt-8 text-center text-sm text-zinc-500">
            Describe a goal below and watch TaskPilot plan and execute it live.
          </p>
        )}
      </div>

      <TaskInput onSubmit={onSubmit} disabled={status === 'running'} />
    </div>
  )
}

function ChatBubble({
  author,
  timestamp,
  tone = 'user',
  children,
}: {
  author: string
  timestamp: number | null
  tone?: 'user' | 'agent'
  children: ReactNode
}) {
  return (
    <div className={`rounded-xl p-3 ${tone === 'agent' ? 'bg-violet-500/10' : 'bg-zinc-900'}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className={`text-sm font-medium ${tone === 'agent' ? 'text-violet-400' : 'text-zinc-200'}`}>
          {author}
        </span>
        {timestamp && (
          <span className="text-xs text-zinc-500">
            {new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-zinc-300">{children}</p>
    </div>
  )
}
