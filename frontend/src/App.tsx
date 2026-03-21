import { Button, Input, message as antMessage } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { AutomationLevelSelector } from './components/AutomationLevelSelector'
import { ConfirmModal } from './components/ConfirmModal'
import { DeviceForm } from './components/DeviceForm'
import {
  configureLlm,
  confirmCommand,
  createSession,
  exportMarkdown,
  getLlmStatus,
  getTimeline,
  streamMessage,
  updateSessionAutomation,
} from './api/client'
import type {
  AutomationLevel,
  ChatMessage,
  CommandExecution,
  DiagnosisSummary,
  Evidence,
  LLMStatus,
  OperationMode,
  SessionResponse,
} from './types'

type PageId = 'workbench' | 'control' | 'sessions' | 'learning' | 'lab' | 'ai_settings'

type PersistedUiState = {
  activePage?: PageId
  rightPanelWidth?: number
  statusCollapsed?: boolean
  directionInput?: string
}

const UI_STATE_KEY = 'netops_ui_prefs_v1'
const NAV_ITEMS: Array<{ id: PageId; title: string }> = [
  { id: 'workbench', title: '诊断工作台' },
  { id: 'control', title: '连接控制' },
  { id: 'sessions', title: '会话历史' },
  { id: 'learning', title: '知识学习' },
  { id: 'lab', title: 'Lab 对抗' },
  { id: 'ai_settings', title: 'AI 设置' },
]

const FLOW_STEPS = ['Create', 'Baseline', 'Plan', 'Approve', 'Execute', 'Analyze', 'Conclude'] as const

function App() {
  const [automationLevel, setAutomationLevel] = useState<AutomationLevel>('assisted')
  const [session, setSession] = useState<SessionResponse | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [commands, setCommands] = useState<CommandExecution[]>([])
  const [evidences, setEvidences] = useState<Evidence[]>([])
  const [summary, setSummary] = useState<DiagnosisSummary | undefined>(undefined)
  const [pendingCommand, setPendingCommand] = useState<CommandExecution | undefined>(undefined)
  const [busy, setBusy] = useState(false)

  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [llmSaving, setLlmSaving] = useState(false)

  const [activePage, setActivePage] = useState<PageId>('workbench')
  const [statusCollapsed, setStatusCollapsed] = useState(false)
  const [rightPanelWidth, setRightPanelWidth] = useState(404)
  const [resizing, setResizing] = useState(false)
  const [directionInput, setDirectionInput] = useState('')
  const [draftInput, setDraftInput] = useState('')
  const [selectedCommandId, setSelectedCommandId] = useState<string | undefined>(undefined)

  const sessionReady = useMemo(() => Boolean(session?.id), [session])
  const flowIndex = useMemo(
    () => currentFlowIndex(sessionReady, commands.length, pendingCommand, summary),
    [sessionReady, commands.length, pendingCommand, summary],
  )

  const selectedCommand = useMemo(() => {
    if (!selectedCommandId) return commands[commands.length - 1]
    return commands.find((item) => item.id === selectedCommandId) || commands[commands.length - 1]
  }, [commands, selectedCommandId])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(UI_STATE_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw) as PersistedUiState
      if (parsed.activePage && NAV_ITEMS.some((item) => item.id === parsed.activePage)) {
        setActivePage(parsed.activePage)
      }
      if (typeof parsed.rightPanelWidth === 'number' && Number.isFinite(parsed.rightPanelWidth)) {
        setRightPanelWidth(Math.min(760, Math.max(340, parsed.rightPanelWidth)))
      }
      if (typeof parsed.statusCollapsed === 'boolean') {
        setStatusCollapsed(parsed.statusCollapsed)
      }
      if (typeof parsed.directionInput === 'string') {
        setDirectionInput(parsed.directionInput)
      }
    } catch {
      // ignore local storage parse errors
    }
  }, [])

  useEffect(() => {
    const payload: PersistedUiState = {
      activePage,
      rightPanelWidth,
      statusCollapsed,
      directionInput,
    }
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(payload))
  }, [activePage, rightPanelWidth, statusCollapsed, directionInput])

  useEffect(() => {
    if (commands.length === 0) {
      setSelectedCommandId(undefined)
      return
    }
    setSelectedCommandId(commands[commands.length - 1].id)
  }, [commands])

  useEffect(() => {
    if (!resizing) return

    const onMove = (event: MouseEvent) => {
      const min = 340
      const max = Math.max(440, Math.min(760, window.innerWidth - 430))
      const next = Math.max(min, Math.min(max, window.innerWidth - event.clientX - 12))
      setRightPanelWidth(next)
    }

    const onUp = () => setResizing(false)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)

    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [resizing])

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

  useEffect(() => {
    const load = async () => {
      try {
        const status = await getLlmStatus()
        setLlmStatus(status)
      } catch {
        setLlmStatus(null)
      }
    }
    void load()
  }, [])

  async function handleCreateSession(payload: {
    host: string
    protocol: 'ssh' | 'telnet' | 'api'
    operation_mode: OperationMode
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
    setDraftInput('')
    antMessage.success(`会话已创建: ${resp.id}`)
    setActivePage('workbench')
  }

  async function handleSaveApiKey() {
    if (!apiKeyInput.trim()) {
      antMessage.warning('请输入 API Key')
      return
    }

    setLlmSaving(true)
    try {
      const status = await configureLlm(apiKeyInput)
      setLlmStatus(status)
      setApiKeyInput('')
      antMessage.success(status.enabled ? '大模型已启用' : '大模型已禁用')
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setLlmSaving(false)
    }
  }

  async function handleSend(content: string) {
    if (!session?.id) {
      antMessage.warning('请先在连接控制创建会话')
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

  async function handleSendComposer() {
    if (!draftInput.trim()) {
      antMessage.warning('请输入问题描述')
      return
    }
    const messageContent = directionInput.trim()
      ? `${draftInput.trim()}\n方向: ${directionInput.trim()}`
      : draftInput.trim()
    setDraftInput('')
    await handleSend(messageContent)
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
    <div className="noc-root">
      <header className="brand-bar">
        <div className="brand-left">
          <div className="brand-mark">NA</div>
          <div>
            <h1>NetOps AI V1</h1>
            <p className="muted">AIOps Workbench</p>
          </div>
        </div>
        <div className="brand-meta">
          <span className={`status-chip ${llmStatus?.enabled ? 'ok' : 'warn'}`}>LLM {llmStatus?.enabled ? 'ON' : 'OFF'}</span>
          <span className="status-chip">Mode {session?.operation_mode || '-'}</span>
          <span className="status-chip">Session {sessionReady ? 'READY' : 'IDLE'}</span>
        </div>
      </header>

      <div className="sub-bar">
        <div className="flow-strip">
          {FLOW_STEPS.map((step, idx) => (
            <div key={step} className={`flow-step ${idx < flowIndex ? 'done' : idx === flowIndex ? 'active' : ''}`}>
              <span className="flow-index">{idx + 1}</span>
              <span>{step}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="shell-body">
        <nav className="icon-rail" aria-label="Pages">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`rail-btn ${activePage === item.id ? 'active' : ''}`}
              type="button"
              title={item.title}
              aria-current={activePage === item.id ? 'page' : undefined}
              onClick={() => setActivePage(item.id)}
            >
              {renderNavIcon(item.id)}
            </button>
          ))}
        </nav>

        <section className="content-board">
          {activePage === 'workbench' && (
            <div className="workbench-grid">
              <div className="workbench-left">
                <section className="workspace-toolbar panel-card compact-row">
                  <div className="conversation-title-row">
                    <h3>AI 诊断对话</h3>
                    <div className="conversation-meta-row">
                      <span className="meta-pill">会话: {session?.id ? `${session.id.slice(0, 8)}...` : '-'}</span>
                      <span className="meta-pill">状态: {busy ? '运行中' : pendingCommand ? '待确认' : summary ? '已完成' : '空闲'}</span>
                      <span className="meta-pill">轮次: {commands.length}</span>
                      <span className="meta-pill">更新时间: {messages.length > 0 ? formatTime(messages[messages.length - 1].created_at) : '-'}</span>
                    </div>
                    <div className="conversation-actions icon-only">
                      <Button size="small" shape="circle" onClick={() => void handleExport()} disabled={!sessionReady}>+</Button>
                      <Button size="small" shape="circle" onClick={() => void refreshTimeline()} disabled={!sessionReady}>↻</Button>
                    </div>
                  </div>
                  <div className="intent-row">
                    <div className="intent-field">
                      <label>问题</label>
                      <Input
                        size="small"
                        value={draftInput}
                        onChange={(event) => setDraftInput(event.target.value)}
                        placeholder="例如：检查 up 的端口"
                        disabled={!sessionReady || busy}
                      />
                    </div>
                    <div className="intent-field">
                      <label>方向</label>
                      <Input
                        size="small"
                        value={directionInput}
                        onChange={(event) => setDirectionInput(event.target.value)}
                        placeholder="例如：优先接口状态与错误计数"
                      />
                    </div>
                  </div>
                  <div className="status-strip-head">
                    <strong>实时状态（可折叠）</strong>
                    <Button size="small" onClick={() => setStatusCollapsed((prev) => !prev)}>
                      {statusCollapsed ? '展开' : '折叠'}
                    </Button>
                  </div>
                  {!statusCollapsed && (
                    <div className="status-grid">
                      <div className="status-item">
                        <span>当前状态</span>
                        <strong>{busy ? '执行中' : pendingCommand ? '等待确认' : summary ? '本轮完成' : '待执行'}</strong>
                      </div>
                      <div className="status-item">
                        <span>下一步动作</span>
                        <strong>{pendingCommand ? '人工确认高风险命令' : busy ? '执行下一条命令' : sessionReady ? '发送问题继续诊断' : '前往连接控制创建会话'}</strong>
                      </div>
                      <div className="status-item">
                        <span>结果摘要</span>
                        <strong>{summary ? summary.query_result || summary.root_cause : '等待 AI 诊断结果'}</strong>
                      </div>
                    </div>
                  )}
                </section>

                <section className="chat-log panel-card">
                  {messages.length === 0 && (
                    <div className="chat-empty">请先到“连接控制”创建会话，然后回到工作台发起诊断。</div>
                  )}
                  {messages.map((msg) => (
                    <article key={msg.id} className={`chat-bubble chat-${msg.role}`}>
                      <div className="chat-head">
                        <strong>{msg.role === 'assistant' ? 'AI' : msg.role === 'user' ? '你' : '系统'}</strong>
                        <span>{formatTime(msg.created_at)}</span>
                      </div>
                      <div className="chat-body">{msg.content}</div>
                    </article>
                  ))}
                </section>

                <section className="composer panel-card compact-row">
                  <Input.TextArea
                    value={draftInput}
                    onChange={(event) => setDraftInput(event.target.value)}
                    rows={3}
                    disabled={!sessionReady || busy}
                    placeholder="示例：检查 up 的端口，并确认是否存在异常 flap"
                  />
                  <div className="composer-actions">
                    <Button
                      type="primary"
                      onClick={() => void handleSendComposer()}
                      disabled={!sessionReady || busy || !draftInput.trim()}
                    >
                      发送
                    </Button>
                  </div>
                </section>
              </div>

              <div className="drag-divider" role="separator" aria-orientation="vertical" onMouseDown={() => setResizing(true)} />

              <aside className="workbench-right" style={{ width: `${rightPanelWidth}px` }}>
                <section className="right-top panel-card">
                  <div className="right-block-head">
                    <h3>显示工作区</h3>
                    <Button size="small" disabled>自动</Button>
                  </div>
                  <div className="summary-title">已选会话 / 分析详情</div>
                  <div className="summary-title">分析显示区</div>
                  <div className="analysis-result-box">
                    {summary ? renderSummaryBrief(summary) : '等待 AI 诊断输出...'}
                  </div>
                </section>

                <section className="right-bottom panel-card">
                  <h3>设备终端回显</h3>
                  <div className="terminal-grid">
                    <div>
                      <div className="summary-title">命令列表</div>
                      <div className="command-list mini">
                        {commands.length === 0 && <div className="muted">-</div>}
                        {commands.map((item) => (
                          <button
                            type="button"
                            key={item.id}
                            className={`command-row ${selectedCommand?.id === item.id ? 'active' : ''}`}
                            onClick={() => setSelectedCommandId(item.id)}
                          >
                            <div className="command-row-main">
                              <span className="command-step">#{item.step_no}</span>
                              <span className="command-title">{item.title}</span>
                              <span className={`cmd-status ${statusClass(item.status)}`}>{item.status}</span>
                            </div>
                            <code>{item.command}</code>
                          </button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="summary-title">执行结果</div>
                      <div className="terminal-box">
                        {selectedCommand?.output || selectedCommand?.error || '-'}
                      </div>
                    </div>
                  </div>
                </section>
              </aside>
            </div>
          )}

          {activePage === 'control' && (
            <div className="page-grid two-col">
              <div className="panel-card">
                <h3>连接控制</h3>
                <p className="muted">会话创建、设备连接、自动化等级设置放在此页面。</p>
                <AutomationLevelSelector value={automationLevel} onChange={setAutomationLevel} />
                <DeviceForm automationLevel={automationLevel} onCreate={handleCreateSession} />
              </div>
              <div className="panel-card">
                <h3>连接状态</h3>
                <div className="kv"><span>会话 ID</span><strong>{session?.id || '-'}</strong></div>
                <div className="kv"><span>会话模式</span><strong>{session?.operation_mode || '-'}</strong></div>
                <div className="kv"><span>自动化等级</span><strong>{automationLabel(automationLevel)}</strong></div>
                <div className="kv"><span>LLM</span><strong>{llmStatus?.enabled ? '已启用' : '未启用'}</strong></div>
                <p className="muted section-tip">更多连接策略、凭据托管、设备模板能力预留到后续迭代。</p>
              </div>
            </div>
          )}

          {activePage === 'sessions' && (
            <div className="page-grid">
              <div className="panel-card">
                <h3>会话视图</h3>
                <p className="muted">沿用 netdiag 逻辑，后续将扩展多会话列表、筛选和回放。当前展示当前会话快照。</p>
                <div className="kv"><span>会话</span><strong>{session?.id || '-'}</strong></div>
                <div className="kv"><span>消息数</span><strong>{messages.length}</strong></div>
                <div className="kv"><span>命令数</span><strong>{commands.length}</strong></div>
                <div className="kv"><span>证据数</span><strong>{evidences.length}</strong></div>
              </div>
            </div>
          )}

          {activePage === 'learning' && (
            <div className="page-grid two-col">
              <div className="panel-card placeholder-card">
                <h3>学习库（预留）</h3>
                <p className="muted">对齐 netdiag 的规则库 / 案例库 / 知识沉淀，当前留空待开发。</p>
              </div>
              <div className="panel-card placeholder-card">
                <h3>命令库（预留）</h3>
                <p className="muted">用于维护厂商命令模板与经验条目，后续与你一起落地。</p>
              </div>
            </div>
          )}

          {activePage === 'lab' && (
            <div className="page-grid two-col">
              <div className="panel-card placeholder-card">
                <h3>Lab 对抗（预留）</h3>
                <p className="muted">用于模拟故障注入、AI 对抗回放、案例晋级，当前留空。</p>
              </div>
              <div className="panel-card placeholder-card">
                <h3>回滚与评估（预留）</h3>
                <p className="muted">后续补齐评分、回滚、Promote Case 等能力。</p>
              </div>
            </div>
          )}

          {activePage === 'ai_settings' && (
            <div className="page-grid two-col">
              <div className="panel-card">
                <h3>AI 设置</h3>
                <p className="muted">模型配置统一放到此页，工作台保持诊断专注。</p>
                <div className="kv"><span>状态</span><strong>{llmStatus?.enabled ? '已启用' : '未启用'}</strong></div>
                <Input.Password
                  value={apiKeyInput}
                  onChange={(event) => setApiKeyInput(event.target.value)}
                  placeholder="输入 DeepSeek API Key (sk-...)"
                />
                <Button style={{ marginTop: 8 }} type="primary" loading={llmSaving} onClick={() => void handleSaveApiKey()}>
                  保存 API Key
                </Button>
              </div>
              <div className="panel-card placeholder-card">
                <h3>提示词与策略（预留）</h3>
                <p className="muted">后续可按 netdiag 风格补充系统提示词、任务提示词、模型路由策略。</p>
              </div>
            </div>
          )}
        </section>
      </div>

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

function automationLabel(level: AutomationLevel): string {
  if (level === 'read_only') return '只读'
  if (level === 'assisted') return '半自动'
  return '全自动'
}

function currentFlowIndex(
  sessionReady: boolean,
  commandCount: number,
  pendingCommand: CommandExecution | undefined,
  summary: DiagnosisSummary | undefined,
): number {
  if (!sessionReady) return 0
  if (summary) return 6
  if (pendingCommand) return 3
  if (commandCount === 0) return 1
  return 4
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return '-'
  }
}

function statusClass(status: string): string {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed' || status === 'rejected' || status === 'blocked') return 'err'
  if (status === 'pending_confirm') return 'warn'
  return 'idle'
}

function renderSummaryBrief(summary: DiagnosisSummary): string {
  if (summary.mode === 'query' || summary.mode === 'config') {
    return summary.query_result || summary.root_cause
  }
  return [summary.root_cause, summary.impact_scope, summary.recommendation].filter(Boolean).join(' | ')
}

function renderNavIcon(page: PageId): string {
  if (page === 'workbench') return '◫'
  if (page === 'control') return '⌘'
  if (page === 'sessions') return '☷'
  if (page === 'learning') return '⌬'
  if (page === 'lab') return '△'
  return '◎'
}

export default App
