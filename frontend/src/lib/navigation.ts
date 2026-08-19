import { History, MessageSquare, Settings, Wrench, type LucideIcon } from 'lucide-react'

/** The 4 sidebar destinations. "Tasks" and "History" used to be separate
 * entries pointing at the same page/data with no real distinction
 * between them -- "Tasks" was removed rather than keep two labels for
 * one thing. */
export type View = 'chat' | 'history' | 'tools' | 'settings'

export const NAV_ITEMS: { view: View; icon: LucideIcon; label: string }[] = [
  { view: 'chat', icon: MessageSquare, label: 'Chat' },
  { view: 'history', icon: History, label: 'History' },
  { view: 'tools', icon: Wrench, label: 'Tools' },
  { view: 'settings', icon: Settings, label: 'Settings' },
]
