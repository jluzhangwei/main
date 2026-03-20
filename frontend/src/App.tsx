import { message as antMessage } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { AutomationLevelSelector } from './components/AutomationLevelSelector'
import { ChatPanel } from './components/ChatPanel'
import { ConfirmModal } from './components/ConfirmModal'
import { DeviceForm } from './components/DeviceForm'
import { TimelinePanel } from './components/TimelinePanel'
import { confirmCommand, createSession, exportMarkdown, getTimeline, streamMessage, updateSessionAutomation } from './api/client'
import type { AutomationLevel, ChatMessage, CommandExecution, DiagnosisSummary, Evidence, SessionResponse } from './types'

function App() {
  const [automationLevel, setAutomationLevel] = useState<AutomationLevel>('assisted')
  const [session, setSession] = useState<SessionResponse | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [commands, setCommands] = useState<CommandExecution[]>([])
  const [evidences, setEvidences] = useState<Evidence[]>([])
  const [summary, setSummary] = useState<DiagnosisSummary | undefined>(undefined)
  const [pendingCommand, setPendingCommand] = useState<CommandExecution | undefined>(undefined)
  const [busy, setBusy] = useState(false)

  const sessionReady = useMemo(() => Boolean(session?.id), [session])

  useEffect(() => {
    if (!session?.id) return
    if (session.automation_level === automationLevel) return

    let canceled = false

    const syncAutomation = async () => {
      try {
        const updated = await updateSessionAutomation(session.id, automationLevel)
        if (canceled) return
        setSession(updated)
        antMessage.success(`自动化等级已切换为 ${automationLabel(updated.automation_level)}`)
      } catch {
        if (canceled) return
        antMessage.error('自动化等级切换失败，已恢复原设置')
        setAutomationLevel(session.automation_level)
      }
    }

    void syncAutomation()
    return () => {
      canceled = true
    }
  }, [automationLevel, session])

  async function handleCreateSession(payload: {
    host: string
    protocol: 'ssh' | 'telnet' | 'api'
    username?: string
    password?: string
    api_token?: string
    automation_level: AutomationLevel
  }) {
    const resp = await createSession(payload)
    setSession(resp)
    setAutomationLevel(resp.automation_level)
    setMessages([])
    setCommands([])
    setEvidences([])
    setSummary(undefined)
    setPendingCommand(undefined)
    antMessage.success(`会话已创建: ${resp.id}`)
  }

  async function handleSend(content: string) {
    if (!session?.id) {
      antMessage.warning('请先创建会话')
      return
    }

    const activeSessionId = session.id
    setBusy(true)
    try {
      await streamMessage(activeSessionId, content, (event, payload) => {
        if (event === 'message_ack' && payload.message) {
          setMessages((prev) => [...prev, payload.message as ChatMessage])
        }

        if (event === 'command_completed' && payload.command) {
          setCommands((prev) => upsertCommand(prev, payload.command as CommandExecution))
        }

        if (event === 'command_blocked' && payload.command) {
          setCommands((prev) => upsertCommand(prev, payload.command as CommandExecution))
        }

        if (event === 'command_pending_confirmation' && payload.command) {
          const command = payload.command as CommandExecution
          setCommands((prev) => upsertCommand(prev, command))
          setPendingCommand(command)
        }

        if (event === 'final_summary' && payload.message) {
          setMessages((prev) => [...prev, payload.message as ChatMessage])
        }
        if (event === 'final_summary' && payload.summary) {
          setSummary(payload.summary)
        }
      })
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      try {
        if (session?.id === activeSessionId) {
          await refreshTimeline(activeSessionId)
        }
      } catch {
        antMessage.warning('时间线刷新失败，请手动刷新')
      }
      setBusy(false)
    }
  }

  async function refreshTimeline(targetSessionId?: string) {
    const sid = targetSessionId ?? session?.id
    if (!sid) return
    const data = await getTimeline(sid)
    setMessages(data.messages)
    setCommands(data.commands)
    setEvidences(data.evidences)
    setSummary(data.summary)
  }

  async function handleExport() {
    if (!session?.id) return
    const content = await exportMarkdown(session.id)
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `session-${session.id}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function handleApprove() {
    if (!session?.id || !pendingCommand) return
    await confirmCommand(session.id, pendingCommand.id, true)
    setPendingCommand(undefined)
    await refreshTimeline()
    antMessage.success('已执行高风险命令')
  }

  async function handleReject() {
    if (!session?.id || !pendingCommand) return
    await confirmCommand(session.id, pendingCommand.id, false)
    setPendingCommand(undefined)
    await refreshTimeline()
    antMessage.info('已拒绝高风险命令')
  }

  return (
    <div className="app-shell">
      <aside className="left-panel">
        <h1>NetOps AI V1</h1>
        <p className="muted">对话式网络故障排查平台</p>
        <AutomationLevelSelector value={automationLevel} onChange={setAutomationLevel} />
        <DeviceForm automationLevel={automationLevel} onCreate={handleCreateSession} />
        <p className="muted">
          当前会话: {sessionReady ? session?.id : '未创建'}
        </p>
      </aside>

      <main className="center-panel">
        <ChatPanel messages={messages} disabled={!sessionReady || busy} onSend={handleSend} />
      </main>

      <section className="right-panel">
        <TimelinePanel
          commands={commands}
          evidences={evidences}
          summary={summary}
          onRefresh={refreshTimeline}
          onExport={handleExport}
        />
      </section>

      <ConfirmModal command={pendingCommand} onApprove={handleApprove} onReject={handleReject} />
    </div>
  )
}

function upsertCommand(existing: CommandExecution[], incoming: CommandExecution): CommandExecution[] {
  const idx = existing.findIndex((item) => item.id === incoming.id)
  if (idx < 0) {
    return [...existing, incoming]
  }

  const clone = [...existing]
  clone[idx] = incoming
  return clone
}

export default App

function automationLabel(level: AutomationLevel): string {
  if (level === 'read_only') return '只读'
  if (level === 'assisted') return '半自动'
  return '全自动'
}
