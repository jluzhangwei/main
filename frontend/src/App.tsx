import { Button, Input, Select, Switch, message as antMessage } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { UIEvent } from 'react'
import { AutomationLevelSelector } from './components/AutomationLevelSelector'
import { DeviceForm } from './components/DeviceForm'
import {
  configureLlm,
  confirmCommand,
  createSession,
  deleteLlmConfig,
  exportMarkdown,
  getCommandPolicy,
  getLlmPromptPolicy,
  getLlmStatus,
  getRiskPolicy,
  getServiceTrace,
  getTimeline,
  listSessions,
  resetCommandPolicy,
  resetRiskPolicy,
  streamMessage,
  stopSession,
  updateCommandPolicy,
  updateRiskPolicy,
  updateSessionCredentials,
  updateSessionAutomation,
} from './api/client'
import type {
  AutomationLevel,
  ChatMessage,
  CommandPolicy,
  CommandExecution,
  DiagnosisSummary,
  Evidence,
  LLMPromptPolicy,
  LLMStatus,
  OperationMode,
  RiskPolicy,
  ServiceTraceStep,
  SessionListItem,
  SessionResponse,
} from './types'

type PageId =
  | 'workbench'
  | 'control'
  | 'command_policy'
  | 'sessions'
  | 'service_trace'
  | 'learning'
  | 'lab'
  | 'ai_settings'

type PersistedUiState = {
  activePage?: PageId
  rightPanelWidth?: number
  terminalSplitRatio?: number
  statusCollapsed?: boolean
  directionInput?: string
  currentSessionId?: string
}

const UI_STATE_KEY = 'netops_ui_prefs_v1'
const DEVICE_AUTH_CACHE_KEY = 'netops_device_auth_cache_v1'

type DeviceAuthRecord = {
  username?: string
  password?: string
  api_token?: string
  updated_at?: string
}
const NAV_ITEMS: Array<{ id: PageId; title: string }> = [
  { id: 'workbench', title: '诊断工作台' },
  { id: 'control', title: '连接控制' },
  { id: 'command_policy', title: '命令执行控制' },
  { id: 'sessions', title: '会话历史' },
  { id: 'service_trace', title: '流程追踪' },
  { id: 'learning', title: '知识学习' },
  { id: 'lab', title: 'Lab 对抗' },
  { id: 'ai_settings', title: 'AI 设置' },
]

const MODEL_OPTIONS = [
  { value: 'deepseek-chat', label: 'DeepSeek Chat' },
  { value: 'deepseek-reasoner', label: 'DeepSeek Reasoner' },
  { value: 'gpt-5.3-codex', label: 'GPT-5.3-Codex' },
  { value: 'gpt-5.4', label: 'GPT-5.4' },
]

type ActivityCard =
  | {
      key: string
      kind: 'message'
      createdAt: string
      label: string
      title: string
      preview: string
      message: ChatMessage
    }
  | {
      key: string
      kind: 'command'
      createdAt: string
      label: string
      title: string
      preview: string
      status: string
      riskLevel: string
      command: CommandExecution
    }
  | {
      key: string
      kind: 'summary'
      createdAt: string
      label: string
      title: string
      preview: string
      summary: DiagnosisSummary
    }

type CommandDisplayRow = {
  key: string
  primary: CommandExecution
  members: CommandExecution[]
  status: string
  risk_level: CommandExecution['risk_level']
  stepLabel: string
  title: string
  commandText: string
}

type FlowLane = {
  key: string
  label: string
  steps: ServiceTraceStep[]
}

function App() {
  const [automationLevel, setAutomationLevel] = useState<AutomationLevel>('assisted')
  const [session, setSession] = useState<SessionResponse | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [commands, setCommands] = useState<CommandExecution[]>([])
  const [evidences, setEvidences] = useState<Evidence[]>([])
  const [summary, setSummary] = useState<DiagnosisSummary | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [stoppingSession, setStoppingSession] = useState(false)
  const [resumedSessionId, setResumedSessionId] = useState<string | undefined>(undefined)
  const [confirmingCommandId, setConfirmingCommandId] = useState<string | undefined>(undefined)

  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null)
  const [llmPromptPolicy, setLlmPromptPolicy] = useState<LLMPromptPolicy | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [llmModelInput, setLlmModelInput] = useState('deepseek-chat')
  const [llmFailoverEnabled, setLlmFailoverEnabled] = useState(true)
  const [llmBatchExecutionEnabled, setLlmBatchExecutionEnabled] = useState(true)
  const [llmSaving, setLlmSaving] = useState(false)
  const [commandPolicy, setCommandPolicy] = useState<CommandPolicy | null>(null)
  const [blockedRules, setBlockedRules] = useState<string[]>([])
  const [executableRules, setExecutableRules] = useState<string[]>([])
  const [blockedSearch, setBlockedSearch] = useState('')
  const [executableSearch, setExecutableSearch] = useState('')
  const [newBlockedRule, setNewBlockedRule] = useState('')
  const [newExecutableRule, setNewExecutableRule] = useState('')
  const [legalityCheckEnabled, setLegalityCheckEnabled] = useState(true)
  const [riskPolicy, setRiskPolicy] = useState<RiskPolicy | null>(null)
  const [highRiskRules, setHighRiskRules] = useState<string[]>([])
  const [mediumRiskRules, setMediumRiskRules] = useState<string[]>([])
  const [highRiskSearch, setHighRiskSearch] = useState('')
  const [mediumRiskSearch, setMediumRiskSearch] = useState('')
  const [newHighRiskRule, setNewHighRiskRule] = useState('')
  const [newMediumRiskRule, setNewMediumRiskRule] = useState('')
  const [policySaving, setPolicySaving] = useState(false)
  const [editingRule, setEditingRule] = useState<
    { kind: 'blocked' | 'executable'; index: number; value: string } | undefined
  >(undefined)
  const [editingRiskRule, setEditingRiskRule] = useState<
    { kind: 'high' | 'medium'; index: number; value: string } | undefined
  >(undefined)
  const policyImportInputRef = useRef<HTMLInputElement | null>(null)

  const [activePage, setActivePage] = useState<PageId>('workbench')
  const [statusCollapsed, setStatusCollapsed] = useState(false)
  const [rightPanelWidth, setRightPanelWidth] = useState(getDefaultWorkbenchRightWidth)
  const [resizing, setResizing] = useState(false)
  const [terminalSplitRatio, setTerminalSplitRatio] = useState(0.5)
  const [terminalResizing, setTerminalResizing] = useState(false)
  const [directionInput, setDirectionInput] = useState('')
  const [draftInput, setDraftInput] = useState('')
  const [sessionDeviceAddress, setSessionDeviceAddress] = useState('')
  const [sessionDeviceName, setSessionDeviceName] = useState('')
  const [sessionHistory, setSessionHistory] = useState<SessionListItem[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [initialSessionId, setInitialSessionId] = useState<string | undefined>(undefined)
  const [bootstrapped, setBootstrapped] = useState(false)
  const [selectedCommandId, setSelectedCommandId] = useState<string | undefined>(undefined)
  const [selectedActivityKey, setSelectedActivityKey] = useState<string | undefined>(undefined)
  const [traceSteps, setTraceSteps] = useState<ServiceTraceStep[]>([])
  const [traceLoading, setTraceLoading] = useState(false)
  const [selectedTraceStepId, setSelectedTraceStepId] = useState<string | undefined>(undefined)
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const chatLogRef = useRef<HTMLElement | null>(null)
  const [commandAutoScrollEnabled, setCommandAutoScrollEnabled] = useState(true)
  const [showCommandScrollToBottom, setShowCommandScrollToBottom] = useState(false)
  const commandListRef = useRef<HTMLDivElement | null>(null)
  const terminalGridRef = useRef<HTMLDivElement | null>(null)
  const streamAbortRef = useRef<AbortController | null>(null)

  const sessionReady = useMemo(() => Boolean(session?.id), [session])
  const commandDisplayRows = useMemo(() => groupCommandsForDisplay(commands), [commands])
  const commandListRows = useMemo(
    () => [...commands].sort((a, b) => a.step_no - b.step_no),
    [commands],
  )
  const activityCards = useMemo(() => buildActivityCards(messages, commands, summary), [messages, commands, summary])

  const selectedCommand = useMemo(() => {
    if (!selectedCommandId) return commands[commands.length - 1]
    return commands.find((item) => item.id === selectedCommandId) || commands[commands.length - 1]
  }, [commands, selectedCommandId])
  const selectedCommandRow = useMemo(() => {
    if (commandDisplayRows.length === 0) return undefined
    if (!selectedCommandId) return commandDisplayRows[commandDisplayRows.length - 1]
    return commandDisplayRows.find((row) => row.members.some((item) => item.id === selectedCommandId))
      || commandDisplayRows[commandDisplayRows.length - 1]
  }, [commandDisplayRows, selectedCommandId])
  const pendingBatchMetaByCommandId = useMemo(() => {
    const pendingGroups = new Map<string, CommandExecution[]>()
    for (const command of commands) {
      if (command.status !== 'pending_confirm' || !command.batch_id) continue
      const list = pendingGroups.get(command.batch_id) || []
      list.push(command)
      pendingGroups.set(command.batch_id, list)
    }
    const meta = new Map<string, { isLeader: boolean; total: number; commands: CommandExecution[] }>()
    for (const [, list] of pendingGroups) {
      const sorted = [...list].sort((a, b) => (a.batch_index || a.step_no) - (b.batch_index || b.step_no))
      const leaderId = sorted[0]?.id
      for (const item of sorted) {
        meta.set(item.id, {
          isLeader: item.id === leaderId,
          total: sorted.length,
          commands: sorted,
        })
      }
    }
    return meta
  }, [commands])
  const pendingCommand = useMemo(
    () =>
      [...commands]
        .reverse()
        .find(
          (item) =>
            item.status === 'pending_confirm'
            && (!item.batch_id || pendingBatchMetaByCommandId.get(item.id)?.isLeader),
        ),
    [commands, pendingBatchMetaByCommandId],
  )
  const pendingConfirmMeta = useMemo(() => {
    if (!pendingCommand) return undefined
    const meta = pendingBatchMetaByCommandId.get(pendingCommand.id)
    const total = meta?.total || 1
    const commandList = meta?.commands || [pendingCommand]
    return {
      commandId: pendingCommand.id,
      total,
      commands: commandList,
      isBatch: total > 1,
    }
  }, [pendingCommand, pendingBatchMetaByCommandId])
  const runningCommand = useMemo(
    () => [...commands].reverse().find((item) => item.status === 'running'),
    [commands],
  )
  const sessionStopped = useMemo(() => {
    if (!session?.id || busy || stoppingSession) return false
    if (resumedSessionId && resumedSessionId === session.id) return false
    const latest = messages[messages.length - 1]
    if (!latest) return false
    return latest.role === 'system' && latest.content.includes('会话已手动停止')
  }, [session?.id, busy, stoppingSession, resumedSessionId, messages])
  const latestUserRequirement = useMemo(() => {
    const draft = draftInput.trim()
    if (draft) return draft
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (message.role === 'user' && message.content.trim()) {
        return message.content.trim()
      }
    }
    return '-'
  }, [draftInput, messages])
  const currentExecutionStatus = useMemo(() => {
    if (pendingCommand) {
      return `待确认: ${pendingCommand.command}`
    }
    if (runningCommand) {
      return `执行中: ${runningCommand.command}`
    }
    if (busy) {
      return commands.length > 0 ? `处理中: ${commands[commands.length - 1].title}` : 'AI 正在规划下一步'
    }
    if (summary) {
      return `已完成: ${summary.query_result || summary.root_cause}`
    }
    if (sessionReady) {
      return '待发送'
    }
    return '未创建会话'
  }, [pendingCommand, runningCommand, busy, commands, summary, sessionReady])
  const traceStats = useMemo(() => buildTraceStats(traceSteps), [traceSteps])
  const flowLanes = useMemo(() => buildFlowLanes(traceSteps), [traceSteps])
  const activeFlowStepId = useMemo(() => resolveActiveFlowStepId(traceSteps), [traceSteps])
  const selectedTraceStep = useMemo(() => {
    if (traceSteps.length === 0) return undefined
    if (selectedTraceStepId) {
      const found = traceSteps.find((item) => item.id === selectedTraceStepId)
      if (found) return found
    }
    if (activeFlowStepId) {
      const active = traceSteps.find((item) => item.id === activeFlowStepId)
      if (active) return active
    }
    return traceSteps[traceSteps.length - 1]
  }, [traceSteps, selectedTraceStepId, activeFlowStepId])
  const llmControlsLocked = useMemo(() => busy || Boolean(confirmingCommandId), [busy, confirmingCommandId])

  const selectedActivity = useMemo(() => {
    if (activityCards.length === 0) return undefined
    if (!selectedActivityKey) return activityCards[activityCards.length - 1]
    return activityCards.find((item) => item.key === selectedActivityKey) || activityCards[activityCards.length - 1]
  }, [activityCards, selectedActivityKey])

  const selectedDetailTitle = useMemo(() => {
    if (!selectedActivity) return '分析显示区'
    if (selectedActivity.kind === 'command') {
      return `步骤 #${selectedCommandRow?.stepLabel || selectedActivity.command.step_no} 详情`
    }
    if (selectedActivity.kind === 'message') return `${selectedActivity.label} 消息详情`
    return '最终诊断详情'
  }, [selectedActivity, selectedCommandRow])

  const selectedDetailBody = useMemo(() => {
    if (selectedActivity?.kind === 'command' && selectedCommandRow) {
      return renderCommandDisplayRowDetail(selectedCommandRow)
    }
    if (selectedActivity) return renderActivityDetail(selectedActivity)
    if (summary) return renderSummaryBrief(summary)
    return '等待 AI 诊断输出...'
  }, [selectedActivity, selectedCommandRow, summary])

  const blockedRuleRows = useMemo(
    () => buildRuleRows(blockedRules, blockedSearch),
    [blockedRules, blockedSearch],
  )
  const executableRuleRows = useMemo(
    () => buildRuleRows(executableRules, executableSearch),
    [executableRules, executableSearch],
  )
  const highRiskRuleRows = useMemo(
    () => buildRuleRows(highRiskRules, highRiskSearch),
    [highRiskRules, highRiskSearch],
  )
  const mediumRiskRuleRows = useMemo(
    () => buildRuleRows(mediumRiskRules, mediumRiskSearch),
    [mediumRiskRules, mediumRiskSearch],
  )
  const policyDirty = useMemo(() => {
    const commandDirty = commandPolicy
      ? (
      legalityCheckEnabled !== commandPolicy.legality_check_enabled ||
      !sameRules(blockedRules, commandPolicy.blocked_patterns) ||
      !sameRules(executableRules, commandPolicy.executable_patterns)
        )
      : false
    const riskDirty = riskPolicy
      ? (
      !sameRules(highRiskRules, riskPolicy.high_risk_patterns) ||
      !sameRules(mediumRiskRules, riskPolicy.medium_risk_patterns)
        )
      : false
    return commandDirty || riskDirty
  }, [commandPolicy, riskPolicy, legalityCheckEnabled, blockedRules, executableRules, highRiskRules, mediumRiskRules])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(UI_STATE_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw) as PersistedUiState
      if (parsed.activePage && NAV_ITEMS.some((item) => item.id === parsed.activePage)) {
        setActivePage(parsed.activePage)
      }
      if (typeof parsed.rightPanelWidth === 'number' && Number.isFinite(parsed.rightPanelWidth)) {
        setRightPanelWidth(Math.min(1400, Math.max(300, parsed.rightPanelWidth)))
      } else {
        setRightPanelWidth(getDefaultWorkbenchRightWidth())
      }
      if (typeof parsed.terminalSplitRatio === 'number' && Number.isFinite(parsed.terminalSplitRatio)) {
        setTerminalSplitRatio(Math.min(0.75, Math.max(0.25, parsed.terminalSplitRatio)))
      }
      if (typeof parsed.statusCollapsed === 'boolean') {
        setStatusCollapsed(parsed.statusCollapsed)
      }
      if (typeof parsed.directionInput === 'string') {
        setDirectionInput(parsed.directionInput)
      }
      if (typeof parsed.currentSessionId === 'string' && parsed.currentSessionId.trim()) {
        setInitialSessionId(parsed.currentSessionId.trim())
      }
    } catch {
      // ignore local storage parse errors
    }
  }, [])

  useEffect(() => {
    const payload: PersistedUiState = {
      activePage,
      rightPanelWidth,
      terminalSplitRatio,
      statusCollapsed,
      directionInput,
      currentSessionId: session?.id,
    }
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(payload))
  }, [activePage, rightPanelWidth, terminalSplitRatio, statusCollapsed, directionInput, session?.id])

  useEffect(() => {
    if (commands.length === 0) {
      setSelectedCommandId(undefined)
      return
    }
    setSelectedCommandId(commands[commands.length - 1].id)
  }, [commands])

  useEffect(() => {
    if (activityCards.length === 0) {
      setSelectedActivityKey(undefined)
      return
    }
    if (!selectedActivityKey || !activityCards.some((item) => item.key === selectedActivityKey)) {
      setSelectedActivityKey(activityCards[activityCards.length - 1].key)
    }
  }, [activityCards, selectedActivityKey])

  useEffect(() => {
    if (traceSteps.length === 0) {
      setSelectedTraceStepId(undefined)
      return
    }
    if (selectedTraceStepId && traceSteps.some((item) => item.id === selectedTraceStepId)) {
      return
    }
    const nextId = resolveActiveFlowStepId(traceSteps) || traceSteps[traceSteps.length - 1].id
    setSelectedTraceStepId(nextId)
  }, [traceSteps, selectedTraceStepId])

  useEffect(() => {
    if (!autoScrollEnabled) return
    const target = chatLogRef.current
    if (!target) return
    target.scrollTo({ top: target.scrollHeight, behavior: 'auto' })
    setShowScrollToBottom(false)
  }, [activityCards, autoScrollEnabled])

  useEffect(() => {
    if (!commandAutoScrollEnabled) return
    const target = commandListRef.current
    if (!target) return
    target.scrollTo({ top: target.scrollHeight, behavior: 'auto' })
    setShowCommandScrollToBottom(false)
  }, [commands, commandAutoScrollEnabled])

  useEffect(() => {
    if (!resizing) return

    const onMove = (event: MouseEvent) => {
      const min = 300
      const max = Math.max(620, Math.min(1400, window.innerWidth - 220))
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
    if (!terminalResizing) return

    const onMove = (event: MouseEvent) => {
      const target = terminalGridRef.current
      if (!target) return
      const rect = target.getBoundingClientRect()
      if (rect.width <= 0) return
      const next = (event.clientX - rect.left) / rect.width
      setTerminalSplitRatio(Math.min(0.75, Math.max(0.25, next)))
    }

    const onUp = () => setTerminalResizing(false)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)

    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [terminalResizing])

  useEffect(() => {
    if (activePage !== 'service_trace') return
    if (!session?.id) return
    void refreshServiceTrace(session.id)
  }, [activePage, session?.id])

  useEffect(() => {
    if (activePage !== 'service_trace') return
    if (!session?.id) return
    if (!busy && !stoppingSession) return
    const intervalId = window.setInterval(() => {
      void refreshServiceTrace(session.id)
    }, 900)
    return () => window.clearInterval(intervalId)
  }, [activePage, session?.id, busy, stoppingSession])

  useEffect(() => {
    if (!bootstrapped) return
    if (!initialSessionId) return
    let canceled = false
    const restore = async () => {
      const ok = await hydrateSessionById(initialSessionId, true)
      if (canceled) return
      if (!ok) {
        try {
          const raw = localStorage.getItem(UI_STATE_KEY)
          if (raw) {
            const parsed = JSON.parse(raw) as PersistedUiState
            delete parsed.currentSessionId
            localStorage.setItem(UI_STATE_KEY, JSON.stringify(parsed))
          }
        } catch {
          // ignore
        }
      }
      setInitialSessionId(undefined)
    }
    void restore()
    return () => {
      canceled = true
    }
  }, [bootstrapped, initialSessionId])

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
    const loadPromptPolicy = async () => {
      try {
        const promptPolicy = await getLlmPromptPolicy()
        setLlmPromptPolicy(promptPolicy)
      } catch {
        setLlmPromptPolicy(null)
      }
    }

    const load = async () => {
      try {
        const status = await getLlmStatus()
        setLlmStatus(status)
        if (status.model) {
          setLlmModelInput(status.model)
        }
        if (typeof status.failover_enabled === 'boolean') {
          setLlmFailoverEnabled(status.failover_enabled)
        }
        if (typeof status.batch_execution_enabled === 'boolean') {
          setLlmBatchExecutionEnabled(status.batch_execution_enabled)
        }
      } catch {
        setLlmStatus(null)
      }

      await loadPromptPolicy()

      try {
        const policy = await getCommandPolicy()
        setCommandPolicy(policy)
        setBlockedRules(normalizeRules(policy.blocked_patterns))
        setExecutableRules(normalizeRules(policy.executable_patterns))
        setLegalityCheckEnabled(policy.legality_check_enabled)
      } catch {
        setCommandPolicy(null)
      }

      try {
        const policy = await getRiskPolicy()
        setRiskPolicy(policy)
        setHighRiskRules(normalizeRules(policy.high_risk_patterns))
        setMediumRiskRules(normalizeRules(policy.medium_risk_patterns))
      } catch {
        setRiskPolicy(null)
      }

      await refreshSessionHistory()
      setBootstrapped(true)
    }
    void load()
  }, [])

  async function refreshPromptPolicy() {
    try {
      const promptPolicy = await getLlmPromptPolicy()
      setLlmPromptPolicy(promptPolicy)
    } catch {
      setLlmPromptPolicy(null)
    }
  }

  async function refreshSessionHistory() {
    setSessionsLoading(true)
    try {
      const items = await listSessions()
      setSessionHistory(items)
    } catch {
      setSessionHistory([])
    } finally {
      setSessionsLoading(false)
    }
  }

  async function hydrateSessionById(sessionId: string, silent = false) {
    try {
      const data = await getTimeline(sessionId)
      const restoredSession: SessionResponse = {
        id: data.session.id,
        automation_level: data.session.automation_level,
        operation_mode: data.session.operation_mode,
        status: data.session.status,
        created_at: data.session.created_at,
      }
      setSession(restoredSession)
      setAutomationLevel(restoredSession.automation_level)
      setMessages(data.messages)
      setCommands(data.commands)
      setEvidences(data.evidences)
      setSummary(data.summary)
      setSessionDeviceAddress(data.session.device?.host || '')
      setSessionDeviceName(formatDeviceName(data.session.device?.name))
      await refreshServiceTrace(sessionId)
      if (!silent) {
        antMessage.success(`已恢复会话 ${sessionId.slice(0, 8)}...`)
      }
      return true
    } catch {
      if (!silent) {
        antMessage.error('恢复会话失败')
      }
      return false
    }
  }

  async function handleCreateSession(payload: {
    host: string
    protocol: 'ssh' | 'telnet' | 'api'
    operation_mode: OperationMode
    username?: string
    password?: string
    api_token?: string
    automation_level: AutomationLevel
  }) {
    try {
      cacheDeviceAuth(payload.host, {
        username: payload.username,
        password: payload.password,
        api_token: payload.api_token,
      })
      const resp = await createSession(payload)
      setSession(resp)
      setAutomationLevel(resp.automation_level)
      setMessages([])
      setCommands([])
      setEvidences([])
      setSummary(undefined)
      setTraceSteps([])
      setSessionDeviceAddress(payload.host)
      setSessionDeviceName('-')
      setDraftInput('')
      await refreshSessionHistory()
      antMessage.success(`会话已创建: ${resp.id}`)
      setActivePage('workbench')
    } catch (error) {
      antMessage.error((error as Error).message || '创建会话失败，请检查后端服务状态')
    }
  }

  async function handleSaveApiKey() {
    if (!apiKeyInput.trim()) {
      antMessage.warning('请输入 API Key')
      return
    }

    setLlmSaving(true)
    try {
      const status = await configureLlm({
        apiKey: apiKeyInput,
        model: llmModelInput,
        failoverEnabled: llmFailoverEnabled,
        batchExecutionEnabled: llmBatchExecutionEnabled,
        modelCandidates: resolveModelCandidates(llmModelInput, llmStatus?.model_candidates),
      })
      setLlmStatus(status)
      if (typeof status.failover_enabled === 'boolean') {
        setLlmFailoverEnabled(status.failover_enabled)
      }
      if (typeof status.batch_execution_enabled === 'boolean') {
        setLlmBatchExecutionEnabled(status.batch_execution_enabled)
      }
      await refreshPromptPolicy()
      setApiKeyInput('')
      antMessage.success(status.enabled ? '大模型已启用，Key 已保存到服务器' : '大模型已禁用')
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setLlmSaving(false)
    }
  }

  async function handleModelChange(nextModel: string) {
    if (llmControlsLocked) {
      antMessage.info('命令执行中，暂不可切换模型')
      return
    }
    if (!nextModel.trim()) {
      antMessage.warning('请选择模型')
      return
    }
    setLlmModelInput(nextModel)
    setLlmSaving(true)
    try {
      const status = await configureLlm({ model: nextModel })
      setLlmStatus(status)
      if (typeof status.failover_enabled === 'boolean') {
        setLlmFailoverEnabled(status.failover_enabled)
      }
      if (typeof status.batch_execution_enabled === 'boolean') {
        setLlmBatchExecutionEnabled(status.batch_execution_enabled)
      }
      await refreshPromptPolicy()
      antMessage.success(`模型已切换为 ${status.model}`)
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setLlmSaving(false)
    }
  }

  async function handleDeleteApiKey() {
    if (!window.confirm('确认删除服务器已保存的 API Key 吗？')) return
    setLlmSaving(true)
    try {
      const status = await deleteLlmConfig()
      setLlmStatus(status)
      if (typeof status.failover_enabled === 'boolean') {
        setLlmFailoverEnabled(status.failover_enabled)
      }
      if (typeof status.batch_execution_enabled === 'boolean') {
        setLlmBatchExecutionEnabled(status.batch_execution_enabled)
      }
      await refreshPromptPolicy()
      setApiKeyInput('')
      antMessage.success('已删除服务器保存的 Key')
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setLlmSaving(false)
    }
  }

  async function handleToggleFailover(enabled: boolean) {
    setLlmFailoverEnabled(enabled)
    setLlmSaving(true)
    try {
      const status = await configureLlm({
        failoverEnabled: enabled,
        modelCandidates: resolveModelCandidates(llmModelInput, llmStatus?.model_candidates),
      })
      setLlmStatus(status)
      if (typeof status.failover_enabled === 'boolean') {
        setLlmFailoverEnabled(status.failover_enabled)
      }
      antMessage.success(`自动模型切换已${enabled ? '开启' : '关闭'}`)
    } catch (error) {
      antMessage.error((error as Error).message)
      setLlmFailoverEnabled((prev) => !prev)
    } finally {
      setLlmSaving(false)
    }
  }

  async function handleToggleBatchExecution(enabled: boolean) {
    if (llmControlsLocked) {
      antMessage.info('命令执行中，暂不可修改批量执行模式')
      return
    }
    setLlmBatchExecutionEnabled(enabled)
    setLlmSaving(true)
    try {
      const status = await configureLlm({
        batchExecutionEnabled: enabled,
        modelCandidates: resolveModelCandidates(llmModelInput, llmStatus?.model_candidates),
      })
      setLlmStatus(status)
      if (typeof status.batch_execution_enabled === 'boolean') {
        setLlmBatchExecutionEnabled(status.batch_execution_enabled)
      }
      antMessage.success(`批量执行提示已${enabled ? '开启' : '关闭'}`)
      await refreshPromptPolicy()
    } catch (error) {
      antMessage.error((error as Error).message)
      setLlmBatchExecutionEnabled((prev) => !prev)
    } finally {
      setLlmSaving(false)
    }
  }

  async function handleSaveCommandPolicy() {
    setPolicySaving(true)
    try {
      const updatedCommandPolicy = await updateCommandPolicy({
        blocked_patterns: blockedRules,
        executable_patterns: executableRules,
        legality_check_enabled: legalityCheckEnabled,
      })
      const updatedRiskPolicy = await updateRiskPolicy({
        high_risk_patterns: highRiskRules,
        medium_risk_patterns: mediumRiskRules,
      })
      setCommandPolicy(updatedCommandPolicy)
      setBlockedRules(normalizeRules(updatedCommandPolicy.blocked_patterns))
      setExecutableRules(normalizeRules(updatedCommandPolicy.executable_patterns))
      setLegalityCheckEnabled(updatedCommandPolicy.legality_check_enabled)
      setRiskPolicy(updatedRiskPolicy)
      setHighRiskRules(normalizeRules(updatedRiskPolicy.high_risk_patterns))
      setMediumRiskRules(normalizeRules(updatedRiskPolicy.medium_risk_patterns))
      antMessage.success('命令与风险判定规则已更新')
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setPolicySaving(false)
    }
  }

  async function handleResetCommandPolicy() {
    setPolicySaving(true)
    try {
      const updatedCommandPolicy = await resetCommandPolicy()
      const updatedRiskPolicy = await resetRiskPolicy()
      setCommandPolicy(updatedCommandPolicy)
      setBlockedRules(normalizeRules(updatedCommandPolicy.blocked_patterns))
      setExecutableRules(normalizeRules(updatedCommandPolicy.executable_patterns))
      setLegalityCheckEnabled(updatedCommandPolicy.legality_check_enabled)
      setRiskPolicy(updatedRiskPolicy)
      setHighRiskRules(normalizeRules(updatedRiskPolicy.high_risk_patterns))
      setMediumRiskRules(normalizeRules(updatedRiskPolicy.medium_risk_patterns))
      setEditingRule(undefined)
      setEditingRiskRule(undefined)
      antMessage.success('已恢复默认命令与风险规则')
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setPolicySaving(false)
    }
  }

  function handleExportCommandPolicy() {
    const payload = {
      blocked_patterns: blockedRules,
      executable_patterns: executableRules,
      legality_check_enabled: legalityCheckEnabled,
      high_risk_patterns: highRiskRules,
      medium_risk_patterns: mediumRiskRules,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'command-policy.json'
    a.click()
    URL.revokeObjectURL(url)
  }

  async function handleImportCommandPolicy(file?: File) {
    if (!file) return
    try {
      const text = await file.text()
      const parsed = JSON.parse(text) as Partial<CommandPolicy>
      const updatedCommandPolicy = await updateCommandPolicy({
        blocked_patterns: Array.isArray(parsed.blocked_patterns) ? parsed.blocked_patterns : blockedRules,
        executable_patterns: Array.isArray(parsed.executable_patterns)
          ? parsed.executable_patterns
          : executableRules,
        legality_check_enabled:
          typeof parsed.legality_check_enabled === 'boolean' ? parsed.legality_check_enabled : legalityCheckEnabled,
      })
      const parsedRisk = parsed as Partial<RiskPolicy>
      const updatedRiskPolicy = await updateRiskPolicy({
        high_risk_patterns: Array.isArray(parsedRisk.high_risk_patterns)
          ? parsedRisk.high_risk_patterns
          : highRiskRules,
        medium_risk_patterns: Array.isArray(parsedRisk.medium_risk_patterns)
          ? parsedRisk.medium_risk_patterns
          : mediumRiskRules,
      })
      setCommandPolicy(updatedCommandPolicy)
      setBlockedRules(normalizeRules(updatedCommandPolicy.blocked_patterns))
      setExecutableRules(normalizeRules(updatedCommandPolicy.executable_patterns))
      setLegalityCheckEnabled(updatedCommandPolicy.legality_check_enabled)
      setRiskPolicy(updatedRiskPolicy)
      setHighRiskRules(normalizeRules(updatedRiskPolicy.high_risk_patterns))
      setMediumRiskRules(normalizeRules(updatedRiskPolicy.medium_risk_patterns))
      setEditingRule(undefined)
      setEditingRiskRule(undefined)
      antMessage.success('规则导入成功')
    } catch {
      antMessage.error('导入失败，请检查 JSON 格式')
    } finally {
      if (policyImportInputRef.current) {
        policyImportInputRef.current.value = ''
      }
    }
  }

  function appendRule(kind: 'blocked' | 'executable') {
    const raw = kind === 'blocked' ? newBlockedRule : newExecutableRule
    const normalized = normalizeRule(raw)
    if (!normalized) {
      antMessage.warning('规则不能为空')
      return
    }
    if (kind === 'blocked') {
      setBlockedRules((prev) => appendRuleUnique(prev, normalized))
      setNewBlockedRule('')
      return
    }
    setExecutableRules((prev) => appendRuleUnique(prev, normalized))
    setNewExecutableRule('')
  }

  function removeRule(kind: 'blocked' | 'executable', index: number) {
    if (kind === 'blocked') {
      setBlockedRules((prev) => prev.filter((_, idx) => idx !== index))
      return
    }
    setExecutableRules((prev) => prev.filter((_, idx) => idx !== index))
  }

  function moveRule(kind: 'blocked' | 'executable', index: number, direction: -1 | 1) {
    if (kind === 'blocked') {
      setBlockedRules((prev) => moveRuleItem(prev, index, direction))
      return
    }
    setExecutableRules((prev) => moveRuleItem(prev, index, direction))
  }

  function startEditRule(kind: 'blocked' | 'executable', index: number, value: string) {
    setEditingRule({ kind, index, value })
  }

  function cancelEditRule() {
    setEditingRule(undefined)
  }

  function saveEditRule() {
    if (!editingRule) return
    const normalized = normalizeRule(editingRule.value)
    if (!normalized) {
      antMessage.warning('规则不能为空')
      return
    }

    if (editingRule.kind === 'blocked') {
      setBlockedRules((prev) => replaceRuleAt(prev, editingRule.index, normalized))
    } else {
      setExecutableRules((prev) => replaceRuleAt(prev, editingRule.index, normalized))
    }
    setEditingRule(undefined)
  }

  function appendRiskRule(kind: 'high' | 'medium') {
    const raw = kind === 'high' ? newHighRiskRule : newMediumRiskRule
    const normalized = normalizeRule(raw)
    if (!normalized) {
      antMessage.warning('规则不能为空')
      return
    }
    if (kind === 'high') {
      setHighRiskRules((prev) => appendRuleUnique(prev, normalized))
      setNewHighRiskRule('')
      return
    }
    setMediumRiskRules((prev) => appendRuleUnique(prev, normalized))
    setNewMediumRiskRule('')
  }

  function removeRiskRule(kind: 'high' | 'medium', index: number) {
    if (kind === 'high') {
      setHighRiskRules((prev) => prev.filter((_, idx) => idx !== index))
      return
    }
    setMediumRiskRules((prev) => prev.filter((_, idx) => idx !== index))
  }

  function moveRiskRule(kind: 'high' | 'medium', index: number, direction: -1 | 1) {
    if (kind === 'high') {
      setHighRiskRules((prev) => moveRuleItem(prev, index, direction))
      return
    }
    setMediumRiskRules((prev) => moveRuleItem(prev, index, direction))
  }

  function startEditRiskRule(kind: 'high' | 'medium', index: number, value: string) {
    setEditingRiskRule({ kind, index, value })
  }

  function cancelEditRiskRule() {
    setEditingRiskRule(undefined)
  }

  function saveEditRiskRule() {
    if (!editingRiskRule) return
    const normalized = normalizeRule(editingRiskRule.value)
    if (!normalized) {
      antMessage.warning('规则不能为空')
      return
    }
    if (editingRiskRule.kind === 'high') {
      setHighRiskRules((prev) => replaceRuleAt(prev, editingRiskRule.index, normalized))
    } else {
      setMediumRiskRules((prev) => replaceRuleAt(prev, editingRiskRule.index, normalized))
    }
    setEditingRiskRule(undefined)
  }

  async function handleSend(content: string) {
    if (!session?.id) {
      antMessage.warning('请先在连接控制创建会话')
      return
    }

    const activeSessionId = session.id
    setResumedSessionId(activeSessionId)
    const abortController = new AbortController()
    streamAbortRef.current = abortController
    setBusy(true)
    try {
      await streamMessage(activeSessionId, content, (event, payload) => {
        if (event === 'message_ack' && payload.message) {
          const msg = payload.message as ChatMessage
          setMessages((prev) => [...prev, msg])
          setSelectedActivityKey(`msg:${msg.id}`)
        }

        if (event === 'command_completed' && payload.command) {
          const cmd = payload.command as CommandExecution
          setCommands((prev) => upsertCommand(prev, cmd))
          setSelectedActivityKey(`cmd:${cmd.id}`)
        }

        if (event === 'command_blocked' && payload.command) {
          const cmd = payload.command as CommandExecution
          setCommands((prev) => upsertCommand(prev, cmd))
          setSelectedActivityKey(`cmd:${cmd.id}`)
        }

        if (event === 'command_pending_confirmation' && payload.command) {
          const command = payload.command as CommandExecution
          setCommands((prev) => upsertCommand(prev, command))
          setSelectedActivityKey(`cmd:${command.id}`)
        }

        if (event === 'final_summary' && payload.message) {
          const msg = payload.message as ChatMessage
          setMessages((prev) => [...prev, msg])
          setSelectedActivityKey(`msg:${msg.id}`)
        }
        if (event === 'final_summary' && payload.summary) {
          setSummary(payload.summary)
          setSelectedActivityKey('summary:latest')
        }
        if (event === 'session_stopped') {
          antMessage.info('会话已停止')
        }
      }, abortController.signal)
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        antMessage.error((error as Error).message)
      }
    } finally {
      streamAbortRef.current = null
      try {
        if (session?.id === activeSessionId) {
          await Promise.all([refreshTimeline(activeSessionId), refreshServiceTrace(activeSessionId)])
        }
      } catch {
        antMessage.warning('时间线/追踪刷新失败，请手动刷新')
      }
      setBusy(false)
    }
  }

  async function handleSendComposer() {
    if (!draftInput.trim()) {
      antMessage.warning('请输入问题描述')
      return
    }
    const messageContent = draftInput.trim()
    setDraftInput('')
    await handleSend(messageContent)
  }

  async function handleStopCurrentSession() {
    if (!session?.id) return
    setStoppingSession(true)
    try {
      streamAbortRef.current?.abort()
      await stopSession(session.id)
      setResumedSessionId(undefined)
      await Promise.all([refreshTimeline(session.id), refreshServiceTrace(session.id)])
      setBusy(false)
      antMessage.success('当前会话已停止')
    } catch (error) {
      antMessage.error((error as Error).message || '停止会话失败')
    } finally {
      setStoppingSession(false)
    }
  }

  async function handleRestoreSession(sessionId: string, hostHint?: string) {
    const ok = await hydrateSessionById(sessionId, true)
    if (ok) {
      const targetHost = (hostHint || sessionDeviceAddress || '').trim()
      const cached = targetHost ? loadDeviceAuth(targetHost) : undefined
      if (cached && (cached.username || cached.password || cached.api_token)) {
        try {
          await updateSessionCredentials(sessionId, {
            username: cached.username,
            password: cached.password,
            api_token: cached.api_token,
          })
        } catch {
          // ignore auto-rebind errors; user can still re-enter credentials manually
        }
      }
      setResumedSessionId(sessionId)
      setActivePage('workbench')
      antMessage.success('已恢复历史会话，可继续与 AI 对话')
    }
  }

  function handleResumeCurrentSession() {
    if (!session?.id) return
    setResumedSessionId(session.id)
    antMessage.success('当前会话已恢复，可继续发送消息')
  }

  async function refreshTimeline(targetSessionId?: string) {
    const sid = targetSessionId ?? session?.id
    if (!sid) return
    const data = await getTimeline(sid)
    setSession({
      id: data.session.id,
      automation_level: data.session.automation_level,
      operation_mode: data.session.operation_mode,
      status: data.session.status,
      created_at: data.session.created_at,
    })
    setSessionDeviceAddress(data.session.device?.host || sessionDeviceAddress)
    setSessionDeviceName(formatDeviceName(data.session.device?.name))
    setMessages(data.messages)
    setCommands(data.commands)
    setEvidences(data.evidences)
    setSummary(data.summary)
  }

  async function refreshServiceTrace(targetSessionId?: string) {
    const sid = targetSessionId ?? session?.id
    if (!sid) return
    setTraceLoading(true)
    try {
      const data = await getServiceTrace(sid)
      setTraceSteps(data.steps || [])
    } finally {
      setTraceLoading(false)
    }
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

  async function handleConfirmCommandInline(commandId: string, approved: boolean) {
    if (!session?.id) return
    setConfirmingCommandId(commandId)
    try {
      await confirmCommand(session.id, commandId, approved)
      await Promise.all([refreshTimeline(), refreshServiceTrace()])
      if (approved) {
        antMessage.success('已执行高风险命令')
      } else {
        antMessage.info('已拒绝高风险命令')
      }
    } catch (error) {
      antMessage.error((error as Error).message || '确认命令失败')
    } finally {
      setConfirmingCommandId(undefined)
    }
  }

  async function handleContinueFromCard() {
    if (!session?.id || busy) return
    await handleSend('请继续执行下一步，不要结束。基于当前会话继续诊断/查询/配置，必要时输出待执行命令组。')
  }

  function shouldShowContinueAction(summaryData: DiagnosisSummary): boolean {
    if (!summaryData) return false
    if (summaryData.mode === 'error' || summaryData.mode === 'unavailable') return true
    if (summaryData.mode === 'config') {
      const actionText = `${summaryData.query_result || ''} ${summaryData.follow_up_action || ''} ${summaryData.recommendation || ''}`
      if (/(建议|执行|修复|打开|关闭|变更|应用|需要)/.test(actionText) && !/(已完成|已执行|无需|不需要)/.test(actionText)) {
        return true
      }
    }
    if (typeof summaryData.confidence === 'number' && summaryData.confidence < 0.65) return true
    const mergedText = `${summaryData.root_cause || ''} ${summaryData.impact_scope || ''} ${summaryData.recommendation || ''}`
    return /(不确定|未完成|无法|失败|重试)/.test(mergedText)
  }

  function isNearBottom(target: HTMLElement): boolean {
    return target.scrollHeight - target.scrollTop - target.clientHeight <= 24
  }

  function handleChatScroll(event: UIEvent<HTMLElement>) {
    const target = event.currentTarget
    const nearBottom = isNearBottom(target)
    if (nearBottom) {
      if (!autoScrollEnabled) {
        setAutoScrollEnabled(true)
      }
      setShowScrollToBottom(false)
      return
    }
    if (autoScrollEnabled) {
      setAutoScrollEnabled(false)
    }
    setShowScrollToBottom(true)
  }

  function handleResumeAutoScroll() {
    const target = chatLogRef.current
    if (!target) return
    setAutoScrollEnabled(true)
    setShowScrollToBottom(false)
    target.scrollTo({ top: target.scrollHeight, behavior: 'smooth' })
  }

  function handleCommandListScroll(event: UIEvent<HTMLDivElement>) {
    const target = event.currentTarget
    const nearBottom = isNearBottom(target)
    if (nearBottom) {
      if (!commandAutoScrollEnabled) {
        setCommandAutoScrollEnabled(true)
      }
      setShowCommandScrollToBottom(false)
      return
    }
    if (commandAutoScrollEnabled) {
      setCommandAutoScrollEnabled(false)
    }
    setShowCommandScrollToBottom(true)
  }

  function handleResumeCommandAutoScroll() {
    const target = commandListRef.current
    if (!target) return
    setCommandAutoScrollEnabled(true)
    setShowCommandScrollToBottom(false)
    target.scrollTo({ top: target.scrollHeight, behavior: 'smooth' })
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
          <span className="status-chip">Mode {session?.operation_mode ? operationModeLabel(session.operation_mode) : '-'}</span>
          <span className="status-chip">Session {sessionReady ? 'READY' : 'IDLE'}</span>
        </div>
      </header>

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
                  {pendingConfirmMeta && (
                    <div className="pending-confirm-toolbar">
                      <span className="pending-confirm-label">
                        {pendingConfirmMeta.isBatch
                          ? `待确认命令组（${pendingConfirmMeta.total} 条）`
                          : '待确认命令'}
                      </span>
                      <div className="pending-confirm-actions">
                        <Button
                          size="small"
                          type="primary"
                          loading={confirmingCommandId === pendingConfirmMeta.commandId}
                          onClick={() => void handleConfirmCommandInline(pendingConfirmMeta.commandId, true)}
                        >
                          {pendingConfirmMeta.isBatch ? '确认整批执行' : '确认执行'}
                        </Button>
                        <Button
                          size="small"
                          danger
                          disabled={confirmingCommandId === pendingConfirmMeta.commandId}
                          onClick={() => void handleConfirmCommandInline(pendingConfirmMeta.commandId, false)}
                        >
                          {pendingConfirmMeta.isBatch ? '拒绝整批' : '拒绝'}
                        </Button>
                      </div>
                    </div>
                  )}
                  <div className="live-meta-row">
                    <div className="live-meta-item">
                      <span>设备</span>
                      <strong>
                        地址 {sessionDeviceAddress || '-'} 设备名称 {formatDeviceName(sessionDeviceName)}
                      </strong>
                    </div>
                    <div className="live-meta-item">
                      <span>需求</span>
                      <strong>{truncateText(latestUserRequirement, 120)}</strong>
                    </div>
                    <div className="live-meta-item">
                      <span>状态</span>
                      <strong>{truncateText(currentExecutionStatus, 140)}</strong>
                    </div>
                  </div>
                </section>

                <section className="chat-log panel-card" ref={chatLogRef} onScroll={handleChatScroll}>
                  {sessionStopped && (
                    <div className="session-stopped-banner">
                      <span>会话已停止，AI 规划与设备执行已中断。</span>
                      <Button size="small" onClick={handleResumeCurrentSession}>恢复会话</Button>
                    </div>
                  )}
                  {activityCards.length === 0 && (
                    <div className="chat-empty">请先到“连接控制”创建会话，然后回到工作台发起诊断。</div>
                  )}
                  <div className="activity-stream">
                    {activityCards.map((item) => (
                      <article
                        key={item.key}
                        className={`activity-card ${item.kind} ${selectedActivity?.key === item.key ? 'active' : ''}`}
                        onClick={() => {
                          setSelectedActivityKey(item.key)
                          if (item.kind === 'command') setSelectedCommandId(item.command.id)
                        }}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            setSelectedActivityKey(item.key)
                            if (item.kind === 'command') setSelectedCommandId(item.command.id)
                          }
                        }}
                      >
                        <div className="activity-meta">
                          <span className={`activity-kind ${item.kind}`}>{item.label}</span>
                          <span className="activity-time">{formatTime(item.createdAt)}</span>
                        </div>
                        <div className="activity-title">{item.title}</div>
                        {item.kind === 'command' && (
                          <div className="activity-tags">
                            <span className={`cmd-status ${statusClass(item.status)}`}>{item.status}</span>
                            <span className="risk-tag">{item.riskLevel}</span>
                          </div>
                        )}
                        <div
                          className={`activity-preview ${item.kind === 'command' ? 'command-preview' : ''}`}
                          title={item.kind === 'command' ? item.preview : undefined}
                        >
                          {item.preview}
                        </div>
                        {item.kind === 'command' && item.command.status === 'pending_confirm' && (
                          <div className="inline-confirm-strip" onClick={(event) => event.stopPropagation()}>
                            {item.command.batch_id
                              && (pendingBatchMetaByCommandId.get(item.command.id)?.total || 0) > 1 ? (
                                <>
                                  <div className="inline-confirm-text">
                                    该批次共有 {pendingBatchMetaByCommandId.get(item.command.id)?.total} 条命令，确认一次将整批执行。
                                  </div>
                                  <div className="inline-confirm-batch-list">
                                    {(pendingBatchMetaByCommandId.get(item.command.id)?.commands || []).map((command) => (
                                      <code key={command.id}>{command.command}</code>
                                    ))}
                                  </div>
                                </>
                              ) : (
                                <div className="inline-confirm-text">
                                  该命令待人工确认后执行: <code>{item.command.command}</code>
                                </div>
                              )}
                            <div className="inline-confirm-actions">
                              <Button
                                size="small"
                                type="primary"
                                loading={confirmingCommandId === (pendingBatchMetaByCommandId.get(item.command.id)?.commands?.[0]?.id || item.command.id)}
                                onClick={() => void handleConfirmCommandInline(
                                  pendingBatchMetaByCommandId.get(item.command.id)?.commands?.[0]?.id || item.command.id,
                                  true,
                                )}
                              >
                                {(pendingBatchMetaByCommandId.get(item.command.id)?.total || 0) > 1 ? '确认整批执行' : '确认执行'}
                              </Button>
                              <Button
                                size="small"
                                danger
                                disabled={confirmingCommandId === (pendingBatchMetaByCommandId.get(item.command.id)?.commands?.[0]?.id || item.command.id)}
                                onClick={() => void handleConfirmCommandInline(
                                  pendingBatchMetaByCommandId.get(item.command.id)?.commands?.[0]?.id || item.command.id,
                                  false,
                                )}
                              >
                                {(pendingBatchMetaByCommandId.get(item.command.id)?.total || 0) > 1 ? '拒绝整批' : '拒绝'}
                              </Button>
                            </div>
                          </div>
                        )}
                        {item.kind === 'summary' && shouldShowContinueAction(item.summary) && (
                          <div className="summary-action-strip" onClick={(event) => event.stopPropagation()}>
                            <div className="summary-action-text">已生成阶段结论。是否继续执行下一步？</div>
                            <div className="summary-action-buttons">
                              <Button
                                size="small"
                                type="primary"
                                loading={busy}
                                disabled={!sessionReady || busy}
                                onClick={() => void handleContinueFromCard()}
                              >
                                继续执行
                              </Button>
                              <Button
                                size="small"
                                disabled={busy}
                                onClick={() => antMessage.info('已保持当前结论')}
                              >
                                暂不继续
                              </Button>
                            </div>
                          </div>
                        )}
                      </article>
                    ))}
                  </div>
                  {showScrollToBottom && (
                    <button
                      type="button"
                      className="scroll-bottom-btn"
                      onClick={handleResumeAutoScroll}
                      title="滚动到底部并继续自动滚动"
                      aria-label="滚动到底部并继续自动滚动"
                    >
                      <span className="scroll-bottom-line" />
                      <span className="scroll-bottom-arrow">↓</span>
                    </button>
                  )}
                </section>

                <section className="composer panel-card compact-row">
                  <div className="composer-shell">
                    <Input.TextArea
                      value={draftInput}
                      onChange={(event) => setDraftInput(event.target.value)}
                      rows={4}
                      disabled={!sessionReady || busy}
                      className="composer-textarea"
                      placeholder="请输入你的问题或变更要求，例如：检查 Et2 状态并给出下一步"
                    />
                    <div className="composer-footer">
                      <div className="composer-inline-controls">
                        <span className="composer-inline-label">模型</span>
                        <Select
                          size="small"
                          value={llmModelInput}
                          options={MODEL_OPTIONS}
                          onChange={(value) => void handleModelChange(value)}
                          className="composer-model-select"
                          disabled={llmSaving || llmControlsLocked}
                        />
                        <div className="composer-inline-toggle">
                          <span className="composer-inline-label">批量执行</span>
                          <Switch
                            size="small"
                            checked={llmBatchExecutionEnabled}
                            onChange={(checked) => void handleToggleBatchExecution(checked)}
                            disabled={llmSaving || llmControlsLocked}
                          />
                        </div>
                      </div>
                      <div className="composer-actions">
                        <Button
                          danger
                          onClick={() => void handleStopCurrentSession()}
                          disabled={!sessionReady || sessionStopped || (!busy && !stoppingSession)}
                          loading={stoppingSession}
                        >
                          {sessionStopped ? '已停止' : '停止'}
                        </Button>
                        {sessionStopped && (
                          <Button onClick={handleResumeCurrentSession} disabled={!sessionReady || busy || stoppingSession}>
                            恢复
                          </Button>
                        )}
                        <Button
                          type="primary"
                          onClick={() => void handleSendComposer()}
                          disabled={!sessionReady || busy || !draftInput.trim()}
                        >
                          发送
                        </Button>
                      </div>
                    </div>
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
                  <div className="summary-title">已选卡片 / 详情</div>
                  <div className="summary-title">{selectedDetailTitle}</div>
                  <div className="analysis-result-box detail-panel">
                    <pre className="detail-pre">{selectedDetailBody}</pre>
                  </div>
                </section>

                <section className="right-bottom panel-card">
                  <h3>设备终端回显</h3>
                  {pendingConfirmMeta && (
                    <div className="terminal-confirm-strip">
                      <div className="terminal-confirm-title">
                        {pendingConfirmMeta.isBatch
                          ? `待确认命令组（${pendingConfirmMeta.total} 条）`
                          : '待确认命令'}
                      </div>
                      <div className="terminal-confirm-list">
                        {pendingConfirmMeta.commands.map((item) => (
                          <code key={item.id}>{item.command}</code>
                        ))}
                      </div>
                      <div className="terminal-confirm-actions">
                        <Button
                          size="small"
                          type="primary"
                          loading={confirmingCommandId === pendingConfirmMeta.commandId}
                          onClick={() => void handleConfirmCommandInline(pendingConfirmMeta.commandId, true)}
                        >
                          {pendingConfirmMeta.isBatch ? '确认整批执行' : '确认执行'}
                        </Button>
                        <Button
                          size="small"
                          danger
                          disabled={confirmingCommandId === pendingConfirmMeta.commandId}
                          onClick={() => void handleConfirmCommandInline(pendingConfirmMeta.commandId, false)}
                        >
                          {pendingConfirmMeta.isBatch ? '拒绝整批' : '拒绝'}
                        </Button>
                      </div>
                    </div>
                  )}
                  <div
                    className="terminal-grid"
                    ref={terminalGridRef}
                    style={{
                      gridTemplateColumns: `minmax(0, ${terminalSplitRatio * 100}%) 8px minmax(0, ${(1 - terminalSplitRatio) * 100}%)`,
                    }}
                  >
                    <div className="terminal-pane">
                      <div className="summary-title">命令列表</div>
                      <div className="command-list mini" ref={commandListRef} onScroll={handleCommandListScroll}>
                        {commandListRows.length === 0 && <div className="muted">-</div>}
                        {commandListRows.map((item) => (
                          <button
                            type="button"
                            key={item.id}
                            className={`command-row compact ${selectedCommand?.id === item.id ? 'active' : ''}`}
                            onClick={() => setSelectedCommandId(item.id)}
                          >
                            <div className="command-row-grid">
                              <div className="command-meta-inline">
                                <span className="command-step">#{item.step_no}</span>
                                <span className="command-title">{item.title}</span>
                                <span className={`cmd-status ${statusClass(item.status)}`}>{item.status}</span>
                              </div>
                              <code className="command-inline-code" title={item.command}>{item.command}</code>
                            </div>
                          </button>
                        ))}
                        {showCommandScrollToBottom && (
                          <button
                            type="button"
                            className="scroll-bottom-btn command-scroll-btn"
                            onClick={handleResumeCommandAutoScroll}
                            title="滚动到底部并继续自动滚动"
                            aria-label="滚动到底部并继续自动滚动"
                          >
                            <span className="scroll-bottom-line" />
                            <span className="scroll-bottom-arrow">↓</span>
                          </button>
                        )}
                      </div>
                    </div>
                    <div
                      className="terminal-divider"
                      role="separator"
                      aria-orientation="vertical"
                      onMouseDown={(event) => {
                        event.preventDefault()
                        setTerminalResizing(true)
                      }}
                    />
                    <div className="terminal-pane">
                      <div className="summary-title">执行结果</div>
                      <div className="terminal-box">
                        {selectedCommand ? renderCommandOutputBody(selectedCommand) : '-'}
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
                <div className="control-card-stack">
                  <AutomationLevelSelector className="control-card-tight" value={automationLevel} onChange={setAutomationLevel} />
                  <DeviceForm className="control-card-tight" automationLevel={automationLevel} onCreate={handleCreateSession} />
                </div>
              </div>
              <div className="panel-card">
                <h3>连接状态</h3>
                <div className="kv"><span>会话 ID</span><strong>{session?.id || '-'}</strong></div>
                <div className="kv"><span>会话模式</span><strong>{session?.operation_mode ? operationModeLabel(session.operation_mode) : '-'}</strong></div>
                <div className="kv"><span>自动化等级</span><strong>{automationLabel(automationLevel)}</strong></div>
                <div className="kv"><span>LLM</span><strong>{llmStatus?.enabled ? '已启用' : '未启用'}</strong></div>
                <p className="muted section-tip">更多连接策略、凭据托管、设备模板能力预留到后续迭代。</p>
              </div>
            </div>
          )}

          {activePage === 'command_policy' && (
            <div className="page-grid">
              <div className="panel-card policy-overview">
                <div className="policy-overview-head">
                  <div>
                    <h3>命令执行控制中心</h3>
                    <p className="muted">
                      判定顺序：阻断规则/硬阻断 → 模式基线（只读直接拒绝非只读；全自动放行非硬阻断）→ 放行规则覆盖（半自动可放行高风险）→ 其余命令进入人工确认。高风险按可编辑风险词表判定（默认含 clear/shutdown 等）。
                    </p>
                  </div>
                  <div className="policy-switch">
                    <Switch checked={legalityCheckEnabled} onChange={setLegalityCheckEnabled} />
                    <span className="muted">启用可选合法性 pre-check</span>
                  </div>
                </div>
                <div className="policy-stats">
                  <div className="policy-stat-card">
                    <span className="muted">阻断规则</span>
                    <strong>{blockedRules.length}</strong>
                  </div>
                  <div className="policy-stat-card">
                    <span className="muted">放行规则</span>
                    <strong>{executableRules.length}</strong>
                  </div>
                  <div className="policy-stat-card">
                    <span className="muted">高风险规则</span>
                    <strong>{highRiskRules.length}</strong>
                  </div>
                  <div className="policy-stat-card">
                    <span className="muted">中风险规则</span>
                    <strong>{mediumRiskRules.length}</strong>
                  </div>
                  <div className="policy-stat-card">
                    <span className="muted">规则状态</span>
                    <strong>{policyDirty ? '待保存' : '已同步'}</strong>
                  </div>
                </div>
                <div className="policy-actions">
                  <Button loading={policySaving} type="primary" onClick={() => void handleSaveCommandPolicy()}>
                    保存规则
                  </Button>
                  <Button loading={policySaving} onClick={() => void handleResetCommandPolicy()}>
                    恢复默认
                  </Button>
                  <Button onClick={handleExportCommandPolicy}>导出 JSON</Button>
                  <Button onClick={() => policyImportInputRef.current?.click()}>导入 JSON</Button>
                  <input
                    ref={policyImportInputRef}
                    type="file"
                    accept="application/json,.json"
                    style={{ display: 'none' }}
                    onChange={(event) => void handleImportCommandPolicy(event.target.files?.[0])}
                  />
                </div>
              </div>

              <div className="page-grid two-col">
                <div className="panel-card policy-list-card">
                  <div className="policy-list-head">
                    <h3>阻断规则（不可执行）</h3>
                    <Input
                      size="small"
                      value={blockedSearch}
                      onChange={(event) => setBlockedSearch(event.target.value)}
                      placeholder="搜索阻断规则"
                    />
                  </div>
                  <div className="policy-add-row">
                    <Input
                      size="small"
                      value={newBlockedRule}
                      onChange={(event) => setNewBlockedRule(event.target.value)}
                      placeholder="输入新规则，例如 reload"
                      onPressEnter={() => appendRule('blocked')}
                    />
                    <Button size="small" onClick={() => appendRule('blocked')}>追加</Button>
                  </div>
                  <div className="policy-rule-table">
                    <div className="policy-rule-row policy-rule-head">
                      <span>#</span>
                      <span>规则</span>
                      <span>操作</span>
                    </div>
                    {blockedRuleRows.length === 0 && <div className="policy-empty muted">暂无匹配规则</div>}
                    {blockedRuleRows.map((row, idx) => {
                      const isEditing = editingRule?.kind === 'blocked' && editingRule.index === row.index
                      return (
                        <div key={`blocked-${row.index}-${row.value}`} className="policy-rule-row">
                          <span>{row.index + 1}</span>
                          {isEditing ? (
                            <Input
                              size="small"
                              value={editingRule.value}
                              onChange={(event) =>
                                setEditingRule((prev) =>
                                  prev ? { ...prev, value: event.target.value } : prev,
                                )
                              }
                              onPressEnter={saveEditRule}
                            />
                          ) : (
                            <code>{row.value}</code>
                          )}
                          <div className="policy-row-actions">
                            {isEditing ? (
                              <>
                                <Button size="small" type="primary" onClick={saveEditRule}>保存</Button>
                                <Button size="small" onClick={cancelEditRule}>取消</Button>
                              </>
                            ) : (
                              <>
                                <Button size="small" onClick={() => startEditRule('blocked', row.index, row.value)}>编辑</Button>
                                <Button size="small" onClick={() => moveRule('blocked', row.index, -1)} disabled={row.index === 0}>上移</Button>
                                <Button size="small" onClick={() => moveRule('blocked', row.index, 1)} disabled={row.index === blockedRules.length - 1}>下移</Button>
                                <Button size="small" danger onClick={() => removeRule('blocked', row.index)}>删除</Button>
                              </>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <details className="policy-bulk-editor">
                    <summary>批量编辑（每行一条）</summary>
                    <Input.TextArea
                      value={blockedRules.join('\n')}
                      rows={7}
                      onChange={(event) => setBlockedRules(normalizeRules(event.target.value.split('\n')))}
                    />
                  </details>
                </div>

                <div className="panel-card policy-list-card">
                  <div className="policy-list-head">
                    <h3>放行规则（可执行）</h3>
                    <Input
                      size="small"
                      value={executableSearch}
                      onChange={(event) => setExecutableSearch(event.target.value)}
                      placeholder="搜索放行规则"
                    />
                  </div>
                  <div className="policy-add-row">
                    <Input
                      size="small"
                      value={newExecutableRule}
                      onChange={(event) => setNewExecutableRule(event.target.value)}
                      placeholder="输入新规则，例如 show "
                      onPressEnter={() => appendRule('executable')}
                    />
                    <Button size="small" onClick={() => appendRule('executable')}>追加</Button>
                  </div>
                  <div className="policy-rule-table">
                    <div className="policy-rule-row policy-rule-head">
                      <span>#</span>
                      <span>规则</span>
                      <span>操作</span>
                    </div>
                    {executableRuleRows.length === 0 && <div className="policy-empty muted">暂无匹配规则</div>}
                    {executableRuleRows.map((row) => {
                      const isEditing = editingRule?.kind === 'executable' && editingRule.index === row.index
                      return (
                        <div key={`executable-${row.index}-${row.value}`} className="policy-rule-row">
                          <span>{row.index + 1}</span>
                          {isEditing ? (
                            <Input
                              size="small"
                              value={editingRule.value}
                              onChange={(event) =>
                                setEditingRule((prev) =>
                                  prev ? { ...prev, value: event.target.value } : prev,
                                )
                              }
                              onPressEnter={saveEditRule}
                            />
                          ) : (
                            <code>{row.value}</code>
                          )}
                          <div className="policy-row-actions">
                            {isEditing ? (
                              <>
                                <Button size="small" type="primary" onClick={saveEditRule}>保存</Button>
                                <Button size="small" onClick={cancelEditRule}>取消</Button>
                              </>
                            ) : (
                              <>
                                <Button size="small" onClick={() => startEditRule('executable', row.index, row.value)}>编辑</Button>
                                <Button size="small" onClick={() => moveRule('executable', row.index, -1)} disabled={row.index === 0}>上移</Button>
                                <Button size="small" onClick={() => moveRule('executable', row.index, 1)} disabled={row.index === executableRules.length - 1}>下移</Button>
                                <Button size="small" danger onClick={() => removeRule('executable', row.index)}>删除</Button>
                              </>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <details className="policy-bulk-editor">
                    <summary>批量编辑（每行一条）</summary>
                    <Input.TextArea
                      value={executableRules.join('\n')}
                      rows={7}
                      onChange={(event) => setExecutableRules(normalizeRules(event.target.value.split('\n')))}
                    />
                  </details>
                </div>

                <div className="panel-card policy-list-card">
                  <div className="policy-list-head">
                    <h3>高风险判定规则</h3>
                    <Input
                      size="small"
                      value={highRiskSearch}
                      onChange={(event) => setHighRiskSearch(event.target.value)}
                      placeholder="搜索高风险规则"
                    />
                  </div>
                  <div className="policy-add-row">
                    <Input
                      size="small"
                      value={newHighRiskRule}
                      onChange={(event) => setNewHighRiskRule(event.target.value)}
                      placeholder="输入新规则，例如 clear "
                      onPressEnter={() => appendRiskRule('high')}
                    />
                    <Button size="small" onClick={() => appendRiskRule('high')}>追加</Button>
                  </div>
                  <div className="policy-rule-table">
                    <div className="policy-rule-row policy-rule-head">
                      <span>#</span>
                      <span>规则</span>
                      <span>操作</span>
                    </div>
                    {highRiskRuleRows.length === 0 && <div className="policy-empty muted">暂无匹配规则</div>}
                    {highRiskRuleRows.map((row) => {
                      const isEditing = editingRiskRule?.kind === 'high' && editingRiskRule.index === row.index
                      return (
                        <div key={`high-${row.index}-${row.value}`} className="policy-rule-row">
                          <span>{row.index + 1}</span>
                          {isEditing ? (
                            <Input
                              size="small"
                              value={editingRiskRule.value}
                              onChange={(event) =>
                                setEditingRiskRule((prev) =>
                                  prev ? { ...prev, value: event.target.value } : prev,
                                )
                              }
                              onPressEnter={saveEditRiskRule}
                            />
                          ) : (
                            <code>{row.value}</code>
                          )}
                          <div className="policy-row-actions">
                            {isEditing ? (
                              <>
                                <Button size="small" type="primary" onClick={saveEditRiskRule}>保存</Button>
                                <Button size="small" onClick={cancelEditRiskRule}>取消</Button>
                              </>
                            ) : (
                              <>
                                <Button size="small" onClick={() => startEditRiskRule('high', row.index, row.value)}>编辑</Button>
                                <Button size="small" onClick={() => moveRiskRule('high', row.index, -1)} disabled={row.index === 0}>上移</Button>
                                <Button size="small" onClick={() => moveRiskRule('high', row.index, 1)} disabled={row.index === highRiskRules.length - 1}>下移</Button>
                                <Button size="small" danger onClick={() => removeRiskRule('high', row.index)}>删除</Button>
                              </>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <details className="policy-bulk-editor">
                    <summary>批量编辑（每行一条）</summary>
                    <Input.TextArea
                      value={highRiskRules.join('\n')}
                      rows={7}
                      onChange={(event) => setHighRiskRules(normalizeRules(event.target.value.split('\n')))}
                    />
                  </details>
                </div>

                <div className="panel-card policy-list-card">
                  <div className="policy-list-head">
                    <h3>中风险判定规则</h3>
                    <Input
                      size="small"
                      value={mediumRiskSearch}
                      onChange={(event) => setMediumRiskSearch(event.target.value)}
                      placeholder="搜索中风险规则"
                    />
                  </div>
                  <div className="policy-add-row">
                    <Input
                      size="small"
                      value={newMediumRiskRule}
                      onChange={(event) => setNewMediumRiskRule(event.target.value)}
                      placeholder="输入新规则，例如 debug"
                      onPressEnter={() => appendRiskRule('medium')}
                    />
                    <Button size="small" onClick={() => appendRiskRule('medium')}>追加</Button>
                  </div>
                  <div className="policy-rule-table">
                    <div className="policy-rule-row policy-rule-head">
                      <span>#</span>
                      <span>规则</span>
                      <span>操作</span>
                    </div>
                    {mediumRiskRuleRows.length === 0 && <div className="policy-empty muted">暂无匹配规则</div>}
                    {mediumRiskRuleRows.map((row) => {
                      const isEditing = editingRiskRule?.kind === 'medium' && editingRiskRule.index === row.index
                      return (
                        <div key={`medium-${row.index}-${row.value}`} className="policy-rule-row">
                          <span>{row.index + 1}</span>
                          {isEditing ? (
                            <Input
                              size="small"
                              value={editingRiskRule.value}
                              onChange={(event) =>
                                setEditingRiskRule((prev) =>
                                  prev ? { ...prev, value: event.target.value } : prev,
                                )
                              }
                              onPressEnter={saveEditRiskRule}
                            />
                          ) : (
                            <code>{row.value}</code>
                          )}
                          <div className="policy-row-actions">
                            {isEditing ? (
                              <>
                                <Button size="small" type="primary" onClick={saveEditRiskRule}>保存</Button>
                                <Button size="small" onClick={cancelEditRiskRule}>取消</Button>
                              </>
                            ) : (
                              <>
                                <Button size="small" onClick={() => startEditRiskRule('medium', row.index, row.value)}>编辑</Button>
                                <Button size="small" onClick={() => moveRiskRule('medium', row.index, -1)} disabled={row.index === 0}>上移</Button>
                                <Button size="small" onClick={() => moveRiskRule('medium', row.index, 1)} disabled={row.index === mediumRiskRules.length - 1}>下移</Button>
                                <Button size="small" danger onClick={() => removeRiskRule('medium', row.index)}>删除</Button>
                              </>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <details className="policy-bulk-editor">
                    <summary>批量编辑（每行一条）</summary>
                    <Input.TextArea
                      value={mediumRiskRules.join('\n')}
                      rows={7}
                      onChange={(event) => setMediumRiskRules(normalizeRules(event.target.value.split('\n')))}
                    />
                  </details>
                </div>
              </div>
            </div>
          )}

          {activePage === 'sessions' && (
            <div className="page-grid two-col">
              <div className="panel-card">
                <div className="trace-head">
                  <div>
                    <h3>历史会话</h3>
                    <p className="muted">刷新页面后可从此列表恢复任意会话。</p>
                  </div>
                  <Button size="small" onClick={() => void refreshSessionHistory()} disabled={sessionsLoading}>
                    {sessionsLoading ? '刷新中...' : '刷新'}
                  </Button>
                </div>
                <div className="session-history-list">
                  {sessionHistory.length === 0 && <div className="muted">暂无会话</div>}
                  {sessionHistory.map((item) => (
                    <div
                      key={item.id}
                      className={`session-history-item ${session?.id === item.id ? 'active' : ''}`}
                    >
                      <button
                        type="button"
                        className="session-history-open"
                        onClick={() => void handleRestoreSession(item.id, item.host)}
                      >
                        <div className="session-history-main">
                          <strong>{item.host}</strong>
                          <span>设备名称: {formatDeviceName(item.device_name)}</span>
                          <span>{operationModeLabel(item.operation_mode)} / {automationLabel(item.automation_level)}</span>
                        </div>
                        <div className="session-history-meta">
                          <span>{item.id.slice(0, 8)}...</span>
                          <span>{formatTime(item.created_at)}</span>
                        </div>
                      </button>
                      <div className="session-history-actions">
                        <Button size="small" type="primary" onClick={() => void handleRestoreSession(item.id, item.host)}>
                          恢复
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="panel-card">
                <h3>当前会话快照</h3>
                <div className="kv"><span>会话</span><strong>{session?.id || '-'}</strong></div>
                <div className="kv"><span>设备</span><strong>{sessionDeviceAddress || '-'}</strong></div>
                <div className="kv"><span>设备名称</span><strong>{formatDeviceName(sessionDeviceName)}</strong></div>
                <div className="kv"><span>模式</span><strong>{session?.operation_mode ? operationModeLabel(session.operation_mode) : '-'}</strong></div>
                <div className="kv"><span>消息数</span><strong>{messages.length}</strong></div>
                <div className="kv"><span>命令数</span><strong>{commands.length}</strong></div>
                <div className="kv"><span>证据数</span><strong>{evidences.length}</strong></div>
              </div>
            </div>
          )}

          {activePage === 'service_trace' && (
            <div className="page-grid">
              <div className="panel-card">
                <div className="trace-head">
                  <div>
                    <h3>流程追踪</h3>
                    <p className="muted">展示完整业务流程、判定动作与实时激活节点。</p>
                  </div>
                  <Button size="small" onClick={() => void refreshServiceTrace()} disabled={!sessionReady || traceLoading}>
                    {traceLoading ? '刷新中...' : '刷新'}
                  </Button>
                </div>
                <div className="trace-stats">
                  <div className="trace-stat-card">
                    <span className="muted">步骤总数</span>
                    <strong>{traceStats.totalSteps}</strong>
                  </div>
                  <div className="trace-stat-card">
                    <span className="muted">总耗时</span>
                    <strong>{formatDuration(traceStats.totalDurationMs)}</strong>
                  </div>
                  <div className="trace-stat-card">
                    <span className="muted">最慢步骤</span>
                    <strong>{traceStats.slowestTitle || '-'}</strong>
                    <span className="muted">{traceStats.slowestDurationMs !== undefined ? formatDuration(traceStats.slowestDurationMs) : '-'}</span>
                  </div>
                  <div className="trace-stat-card">
                    <span className="muted">当前会话</span>
                    <strong>{session?.id ? `${session.id.slice(0, 8)}...` : '-'}</strong>
                  </div>
                </div>
              </div>

              <div className="panel-card flow-board">
                <div className="trace-head">
                  <div>
                    <h3>业务流程图</h3>
                    <p className="muted">按泳道展示每个动作，当前激活节点会高亮。</p>
                  </div>
                  <span className="status-chip">Active #{selectedTraceStep?.seq_no || '-'}</span>
                </div>
                <div className="flow-lanes">
                  {flowLanes.map((lane) => (
                    <section key={lane.key} className="flow-lane">
                      <div className="flow-lane-head">
                        <strong>{lane.label}</strong>
                        <span>{lane.steps.length} 步</span>
                      </div>
                      <div className="flow-node-list">
                        {lane.steps.length === 0 && <div className="flow-node flow-node-empty">待触发</div>}
                        {lane.steps.map((step) => (
                          <button
                            type="button"
                            key={step.id}
                            className={`flow-node ${activeFlowStepId === step.id ? 'active' : ''} ${selectedTraceStep?.id === step.id ? 'selected' : ''}`}
                            onClick={() => setSelectedTraceStepId(step.id)}
                          >
                            <div className="flow-node-title">#{step.seq_no} {step.title}</div>
                            <div className="flow-node-meta">{traceTypeLabel(step.step_type)} · {step.status}</div>
                          </button>
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
                {selectedTraceStep && (
                  <div className="flow-detail-card">
                    <div className="flow-detail-head">
                      <strong>节点详情 #{selectedTraceStep.seq_no}</strong>
                      <span className={`trace-status ${traceStatusClass(selectedTraceStep.status)}`}>{selectedTraceStep.status}</span>
                    </div>
                    <div className="flow-detail-grid">
                      <div><span>类型</span><strong>{traceTypeLabel(selectedTraceStep.step_type)}</strong></div>
                      <div><span>开始</span><strong>{formatTime(selectedTraceStep.started_at)}</strong></div>
                      <div><span>结束</span><strong>{selectedTraceStep.completed_at ? formatTime(selectedTraceStep.completed_at) : '-'}</strong></div>
                      <div><span>耗时</span><strong>{selectedTraceStep.duration_ms !== undefined ? formatDuration(selectedTraceStep.duration_ms) : '-'}</strong></div>
                    </div>
                    {selectedTraceStep.detail && (
                      <pre className="flow-detail-text">{selectedTraceStep.detail}</pre>
                    )}
                  </div>
                )}
              </div>

              <div className="panel-card trace-list-card">
                <div className="trace-row trace-row-head">
                  <span>#</span>
                  <span>步骤</span>
                  <span>状态</span>
                  <span>开始</span>
                  <span>结束</span>
                  <span>耗时</span>
                </div>
                <div className="trace-list-scroll">
                  {traceSteps.length === 0 && <div className="trace-empty muted">暂无追踪数据。先创建会话并执行一次对话。</div>}
                  {traceSteps.map((step) => {
                    const width = traceStats.maxDurationMs > 0 && step.duration_ms !== undefined
                      ? Math.max(4, Math.round((step.duration_ms / traceStats.maxDurationMs) * 100))
                      : 0
                    return (
                      <div
                        key={step.id}
                        className={`trace-row trace-row-item ${selectedTraceStep?.id === step.id ? 'active' : ''}`}
                        onClick={() => setSelectedTraceStepId(step.id)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            setSelectedTraceStepId(step.id)
                          }
                        }}
                      >
                        <span>{step.seq_no}</span>
                        <div className="trace-step-cell">
                          <div className="trace-step-title">{step.title}</div>
                          <div className="trace-step-meta">{traceTypeLabel(step.step_type)}</div>
                          {step.detail && <div className="trace-step-detail">{step.detail}</div>}
                          {width > 0 && (
                            <div className="trace-bar-wrap">
                              <div className="trace-bar" style={{ width: `${width}%` }} />
                            </div>
                          )}
                        </div>
                        <span className={`trace-status ${traceStatusClass(step.status)}`}>{step.status}</span>
                        <span>{formatTime(step.started_at)}</span>
                        <span>{step.completed_at ? formatTime(step.completed_at) : '-'}</span>
                        <span>{step.duration_ms !== undefined ? formatDuration(step.duration_ms) : '-'}</span>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}

          {activePage === 'learning' && (
            <div className="page-grid two-col">
              <div className="panel-card">
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
            <div className="page-grid ai-settings-layout">
              <div className="panel-card">
                <h3>AI 设置</h3>
                <p className="muted">模型配置统一放到此页，工作台保持诊断专注。</p>
                <div className="kv"><span>状态</span><strong>{llmStatus?.enabled ? '已启用' : '未启用'}</strong></div>
                <div className="kv"><span>主模型</span><strong>{llmStatus?.model || '-'}</strong></div>
                <div className="kv"><span>当前生效模型</span><strong>{llmStatus?.active_model || llmStatus?.model || '-'}</strong></div>
                <div className="kv"><span>自动切换</span><strong>{llmFailoverEnabled ? '开启' : '关闭'}</strong></div>
                <div className="kv"><span>批量执行提示</span><strong>{llmBatchExecutionEnabled ? '开启' : '关闭'}</strong></div>
                <div className="policy-switch" style={{ marginTop: 8 }}>
                  <Switch checked={llmFailoverEnabled} onChange={(checked) => void handleToggleFailover(checked)} disabled={llmSaving} />
                  <span className="muted">模型异常时自动切到下一个候选模型</span>
                </div>
                <div className="policy-switch" style={{ marginTop: 8 }}>
                  <Switch
                    checked={llmBatchExecutionEnabled}
                    onChange={(checked) => void handleToggleBatchExecution(checked)}
                    disabled={llmSaving || llmControlsLocked}
                  />
                  <span className="muted">通过提示词要求 AI 优先输出批量命令组</span>
                </div>
                <div className="kv"><span>不可用原因</span><strong>{formatLlmUnavailableReason(llmStatus?.unavailable_reason)}</strong></div>
                {llmStatus?.last_error && (
                  <div className="danger-command" style={{ marginTop: 8 }}>
                    {llmStatus.last_error}
                  </div>
                )}
                <Input.Password
                  value={apiKeyInput}
                  onChange={(event) => setApiKeyInput(event.target.value)}
                  placeholder="输入 DeepSeek API Key (sk-...)"
                />
                <Select
                  style={{ marginTop: 8 }}
                  value={llmModelInput}
                  options={MODEL_OPTIONS}
                  onChange={(value) => void handleModelChange(value)}
                  disabled={llmSaving || llmControlsLocked}
                />
                <div className="ai-setting-actions">
                  <Button type="primary" loading={llmSaving} onClick={() => void handleSaveApiKey()}>
                    保存 API Key
                  </Button>
                  <Button danger disabled={llmSaving} onClick={() => void handleDeleteApiKey()}>
                    删除已保存 Key
                  </Button>
                </div>
              </div>
              <div className="panel-card placeholder-card">
                <h3>提示词与策略</h3>
                <p className="muted">展示系统当前提供给 AI 的提示词模板（只读）。</p>
                <div className="kv"><span>模型</span><strong>{llmPromptPolicy?.model || llmStatus?.model || '-'}</strong></div>
                <div className="kv"><span>Base URL</span><strong>{llmPromptPolicy?.base_url || llmStatus?.base_url || '-'}</strong></div>
                <div className="prompt-policy-list">
                  {llmPromptPolicy && Object.keys(llmPromptPolicy.prompts || {}).length > 0 ? (
                    Object.entries(llmPromptPolicy.prompts).map(([key, value]) => (
                      <details key={key} className="prompt-policy-item">
                        <summary>{formatPromptKey(key)}</summary>
                        <pre>{value}</pre>
                      </details>
                    ))
                  ) : (
                    <p className="muted">当前系统未返回可展示的提示词模板。</p>
                  )}
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
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

function operationModeLabel(mode: OperationMode): string {
  if (mode === 'diagnosis') return '诊断模式'
  if (mode === 'query') return '查询模式'
  return '配置模式'
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

function formatPromptKey(key: string): string {
  const map: Record<string, string> = {
    next_step_history: '多轮对话决策提示词',
    next_step_default: '默认决策提示词',
    summary_primary: '诊断总结提示词',
    summary_review: '诊断审稿提示词',
    summary_rewrite: '诊断改写提示词',
    runtime_session_header_template: '运行时会话上下文模板',
    runtime_command_result_template: '运行时命令结果模板',
    runtime_finalization_prompt_template: '运行时最终总结模板',
    runtime_batch_confirm_policy: '运行时批量确认策略',
    runtime_output_compaction_policy: '运行时输出压缩策略',
    runtime_permission_precheck_policy: '运行时权限预检策略',
    runtime_final_action_marker_policy: '运行时结论动作词策略',
  }
  return map[key] || key
}

function formatLlmUnavailableReason(reason?: string): string {
  if (!reason) return '-'
  if (reason === 'api_key_missing') return '未配置 API Key'
  if (reason === 'connectivity_error') return '模型服务联机失败'
  if (reason === 'auth_error') return 'API Key 无效或无权限'
  if (reason === 'rate_limit') return '请求频率受限'
  if (reason === 'provider_unavailable') return '模型服务暂不可用'
  if (reason === 'provider_http_error') return '模型服务返回异常状态'
  return reason
}

function resolveModelCandidates(currentModel: string, existing?: string[]): string[] {
  const preferred = (currentModel || '').trim()
  const seed = Array.isArray(existing) && existing.length > 0
    ? existing
    : ['deepseek-chat', 'deepseek-reasoner']
  const out: string[] = []
  const seen = new Set<string>()
  for (const item of [preferred, ...seed]) {
    const text = String(item || '').trim()
    if (!text) continue
    const key = text.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(text)
  }
  return out
}

function renderSummaryBrief(summary: DiagnosisSummary): string {
  if (summary.mode === 'query' || summary.mode === 'config') {
    return summary.query_result || summary.root_cause
  }
  return [summary.root_cause, summary.impact_scope, summary.recommendation].filter(Boolean).join(' | ')
}

function buildActivityCards(
  messages: ChatMessage[],
  commands: CommandExecution[],
  summary?: DiagnosisSummary,
): ActivityCard[] {
  const cards: Array<ActivityCard & { sortAt: number; sortIdx: number }> = []

  for (let index = 0; index < messages.length; index += 1) {
    const msg = messages[index]
    cards.push({
      key: `msg:${msg.id}`,
      kind: 'message',
      createdAt: msg.created_at,
      label: messageRoleLabel(msg.role),
      title: messageTitle(msg),
      preview: truncateText(msg.content, 170),
      message: msg,
      sortAt: parseSortTime(msg.created_at, index),
      sortIdx: index,
    })
  }

  const grouped = groupCommandsForDisplay(commands)
  for (let index = 0; index < grouped.length; index += 1) {
    const row = grouped[index]
    const cmd = row.primary
    const createdAt = cmd.created_at || row.members[0]?.created_at || ''
    cards.push({
      key: `cmd:${cmd.id}`,
      kind: 'command',
      createdAt,
      label: '执行',
      title: `步骤 ${row.stepLabel}: ${row.title}`,
      preview: summarizeCommandRow(row),
      status: row.status,
      riskLevel: riskLabel(row.risk_level),
      command: cmd,
      sortAt: parseSortTime(createdAt, messages.length + (cmd.step_no || index)),
      sortIdx: messages.length + (cmd.step_no || index),
    })
  }

  if (summary) {
    const createdAt = summary.created_at || ''
    cards.push({
      key: 'summary:latest',
      kind: 'summary',
      createdAt,
      label: '结论',
      title: summary.mode === 'query' || summary.mode === 'config' ? '查询结果' : '最终诊断',
      preview: truncateText(renderSummaryBrief(summary), 170),
      summary,
      sortAt: parseSortTime(createdAt, messages.length + commands.length + 1),
      sortIdx: messages.length + commands.length + 1,
    })
  }

  return cards
    .sort((a, b) => {
      if (a.sortAt === b.sortAt) return a.sortIdx - b.sortIdx
      return a.sortAt - b.sortAt
    })
    .map(({ sortAt: _, sortIdx: __, ...rest }) => rest)
}

function renderActivityDetail(activity: ActivityCard): string {
  if (activity.kind === 'message') {
    return activity.message.content
  }

  if (activity.kind === 'command') {
    const cmd = activity.command
    const output = (cmd.output || '').trim()
    const error = (cmd.error || '').trim()
    return [
      `标题: ${cmd.title}`,
      `状态: ${cmd.status}`,
      `风险: ${riskLabel(cmd.risk_level)}`,
      `命令: ${cmd.command}`,
      output ? `输出:\n${output}` : '',
      error ? `错误:\n${error}` : '',
    ]
      .filter(Boolean)
      .join('\n\n')
  }

  const summary = activity.summary
  if (summary.mode === 'query' || summary.mode === 'config') {
    return [
      `模式: ${summary.mode}`,
      `结果: ${summary.query_result || summary.root_cause}`,
      summary.follow_up_action ? `建议: ${summary.follow_up_action}` : '',
    ]
      .filter(Boolean)
      .join('\n\n')
  }
  return [
    `根因: ${summary.root_cause}`,
    `影响范围: ${summary.impact_scope}`,
    `建议动作: ${summary.recommendation}`,
    summary.confidence !== undefined ? `置信度: ${Math.round(summary.confidence * 100)}%` : '',
  ]
    .filter(Boolean)
    .join('\n\n')
}

function messageRoleLabel(role: ChatMessage['role']): string {
  if (role === 'assistant') return 'AI'
  if (role === 'user') return '你'
  return '系统'
}

function messageTitle(msg: ChatMessage): string {
  if (msg.role === 'assistant') return 'AI 响应'
  if (msg.role === 'user') return '用户输入'
  return '系统事件'
}

function summarizeCommandCard(command: CommandExecution): string {
  const headline = command.command
  const result = truncateText(renderCommandOutputBody(command), 180)
  if (result) return `${headline}\n${result}`
  return headline
}

function summarizeCommandRow(row: CommandDisplayRow): string {
  return row.commandText
}

function renderCommandOutputBody(command: Pick<CommandExecution, 'output' | 'error' | 'status'>): string {
  const output = (command.output || '').trim()
  if (output) return output
  const error = (command.error || '').trim()
  if (error) return error
  if (command.status === 'pending_confirm') return '(待确认，尚未执行)'
  if (command.status === 'succeeded') return '(命令已执行，设备未返回文本回显)'
  if (command.status === 'failed') return '(执行失败，设备未返回文本回显)'
  return '(无回显)'
}

function renderCommandRowOutput(row: CommandDisplayRow): string {
  if (row.members.length <= 1) {
    const item = row.members[0]
    return item ? renderCommandOutputBody(item) : '(无回显)'
  }
  const parts: string[] = []
  for (const item of row.members) {
    const body = renderCommandOutputBody(item)
    parts.push(`#${item.step_no} ${item.command}\n${body}`)
  }
  if (parts.length === 0) return '(无回显)'
  return parts.join('\n\n')
}

function renderCommandDisplayRowDetail(row: CommandDisplayRow): string {
  const header = [
    `标题: ${row.title}`,
    `状态: ${row.status}`,
    `风险: ${riskLabel(row.risk_level)}`,
    `步骤: ${row.stepLabel}`,
    `命令组: ${row.commandText}`,
  ].join('\n')
  const body = renderCommandRowOutput(row)
  return `${header}\n\n输出:\n${body}`
}

function groupCommandsForDisplay(commands: CommandExecution[]): CommandDisplayRow[] {
  const rows: CommandDisplayRow[] = []
  const ordered = [...commands].sort((a, b) => a.step_no - b.step_no)
  let index = 0
  while (index < ordered.length) {
    const current = ordered[index]
    if (current.batch_id) {
      const batchId = current.batch_id
      const members: CommandExecution[] = []
      let cursor = index
      while (cursor < ordered.length && ordered[cursor].batch_id === batchId) {
        members.push(ordered[cursor])
        cursor += 1
      }
      rows.push(buildCommandDisplayRow(members))
      index = cursor
      continue
    }
    rows.push(buildCommandDisplayRow([current]))
    index += 1
  }
  return rows
}

function buildCommandDisplayRow(members: CommandExecution[]): CommandDisplayRow {
  const sorted = [...members].sort((a, b) => a.step_no - b.step_no)
  const first = sorted[0]
  const last = sorted[sorted.length - 1]
  const pending = sorted.find((item) => item.status === 'pending_confirm')
  const primary = pending || first
  const stepLabel = first.step_no === last.step_no ? `${first.step_no}` : `${first.step_no}-${last.step_no}`
  const status = summarizeBatchStatus(sorted)
  const risk = summarizeBatchRisk(sorted)
  const title = sorted.length > 1 ? `命令组合（${sorted.length} 条）` : first.title
  const commandText = sorted.map((item) => item.command).join(' ; ')
  return {
    key: first.batch_id ? `batch:${first.batch_id}` : `single:${first.id}`,
    primary,
    members: sorted,
    status,
    risk_level: risk,
    stepLabel,
    title,
    commandText,
  }
}

function summarizeBatchStatus(items: CommandExecution[]): string {
  if (items.some((item) => item.status === 'failed')) return 'failed'
  if (items.some((item) => item.status === 'pending_confirm')) return 'pending_confirm'
  if (items.some((item) => item.status === 'running')) return 'running'
  if (items.some((item) => item.status === 'blocked')) return 'blocked'
  if (items.some((item) => item.status === 'rejected')) return 'rejected'
  if (items.every((item) => item.status === 'succeeded')) return 'succeeded'
  return items[items.length - 1]?.status || 'queued'
}

function summarizeBatchRisk(items: CommandExecution[]): CommandExecution['risk_level'] {
  if (items.some((item) => item.risk_level === 'high')) return 'high'
  if (items.some((item) => item.risk_level === 'medium')) return 'medium'
  return 'low'
}

function truncateText(value: string, limit: number): string {
  if (value.length <= limit) return value
  return `${value.slice(0, limit)}...`
}

function riskLabel(level: CommandExecution['risk_level']): string {
  if (level === 'high') return '高风险'
  if (level === 'medium') return '中风险'
  return '低风险'
}

function parseSortTime(value: string | undefined, fallback: number): number {
  if (!value) return Number.MAX_SAFE_INTEGER - 1000 + fallback
  const ts = Date.parse(value)
  if (Number.isNaN(ts)) return Number.MAX_SAFE_INTEGER - 1000 + fallback
  return ts
}

function getDefaultWorkbenchRightWidth(): number {
  if (typeof window === 'undefined') {
    return 560
  }
  const reserved = 96
  const half = Math.floor((window.innerWidth - reserved) / 2)
  return Math.min(1400, Math.max(300, half))
}

function formatDeviceName(value: string | null | undefined): string {
  const normalized = String(value ?? '').trim()
  return normalized || '-'
}

function loadDeviceAuth(host: string): DeviceAuthRecord | undefined {
  const normalizedHost = String(host || '').trim()
  if (!normalizedHost || typeof window === 'undefined') return undefined
  try {
    const raw = localStorage.getItem(DEVICE_AUTH_CACHE_KEY)
    if (!raw) return undefined
    const parsed = JSON.parse(raw) as Record<string, DeviceAuthRecord>
    const hit = parsed[normalizedHost]
    if (!hit) return undefined
    return {
      username: (hit.username || '').trim() || undefined,
      password: (hit.password || '').trim() || undefined,
      api_token: (hit.api_token || '').trim() || undefined,
      updated_at: hit.updated_at,
    }
  } catch {
    // ignore parse errors
  }
  // Fallback for default lab device so restored sessions can continue without manual re-login.
  if (normalizedHost === '192.168.0.102') {
    return {
      username: 'zhangwei',
      password: 'Admin@123',
      updated_at: new Date().toISOString(),
    }
  }
  return undefined
}

function cacheDeviceAuth(host: string, auth: DeviceAuthRecord): void {
  const normalizedHost = String(host || '').trim()
  if (!normalizedHost || typeof window === 'undefined') return
  const username = String(auth.username || '').trim()
  const password = String(auth.password || '').trim()
  const apiToken = String(auth.api_token || '').trim()
  if (!username && !password && !apiToken) return
  try {
    const raw = localStorage.getItem(DEVICE_AUTH_CACHE_KEY)
    const parsed = raw ? (JSON.parse(raw) as Record<string, DeviceAuthRecord>) : {}
    parsed[normalizedHost] = {
      username: username || undefined,
      password: password || undefined,
      api_token: apiToken || undefined,
      updated_at: new Date().toISOString(),
    }
    localStorage.setItem(DEVICE_AUTH_CACHE_KEY, JSON.stringify(parsed))
  } catch {
    // ignore cache errors
  }
}

function normalizeRule(value: string): string {
  return String(value ?? '').trim()
}

function normalizeRules(values: string[]): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const item of values) {
    const normalized = normalizeRule(item)
    if (!normalized) continue
    const key = normalized.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(normalized)
  }
  return out
}

function appendRuleUnique(values: string[], rule: string): string[] {
  return normalizeRules([...values, rule])
}

function replaceRuleAt(values: string[], index: number, nextValue: string): string[] {
  if (index < 0 || index >= values.length) return values
  const updated = [...values]
  updated[index] = normalizeRule(nextValue)
  return normalizeRules(updated)
}

function moveRuleItem(values: string[], index: number, direction: -1 | 1): string[] {
  const nextIndex = index + direction
  if (index < 0 || index >= values.length) return values
  if (nextIndex < 0 || nextIndex >= values.length) return values
  const clone = [...values]
  const [item] = clone.splice(index, 1)
  clone.splice(nextIndex, 0, item)
  return clone
}

function buildRuleRows(values: string[], keyword: string): Array<{ index: number; value: string }> {
  const normalizedKeyword = keyword.trim().toLowerCase()
  return values
    .map((value, index) => ({ index, value }))
    .filter((row) => !normalizedKeyword || row.value.toLowerCase().includes(normalizedKeyword))
}

function sameRules(left: string[], right: string[]): boolean {
  const a = normalizeRules(left)
  const b = normalizeRules(right)
  if (a.length !== b.length) return false
  for (let idx = 0; idx < a.length; idx += 1) {
    if (a[idx].toLowerCase() !== b[idx].toLowerCase()) return false
  }
  return true
}

function buildTraceStats(steps: ServiceTraceStep[]): {
  totalSteps: number
  totalDurationMs: number
  maxDurationMs: number
  slowestTitle: string
  slowestDurationMs: number | undefined
} {
  let total = 0
  let maxDuration = 0
  let slowestTitle = ''
  let slowestDuration: number | undefined
  for (const step of steps) {
    if (typeof step.duration_ms !== 'number') continue
    total += Math.max(0, step.duration_ms)
    if (step.duration_ms >= maxDuration) {
      maxDuration = step.duration_ms
      slowestDuration = step.duration_ms
      slowestTitle = step.title
    }
  }
  return {
    totalSteps: steps.length,
    totalDurationMs: total,
    maxDurationMs: maxDuration,
    slowestTitle,
    slowestDurationMs: slowestDuration,
  }
}

function resolveActiveFlowStepId(steps: ServiceTraceStep[]): string | undefined {
  if (steps.length === 0) return undefined
  const running = [...steps].reverse().find((step) => step.status === 'running')
  if (running) return running.id
  const pending = [...steps].reverse().find((step) => step.status === 'pending_confirm')
  if (pending) return pending.id
  return steps[steps.length - 1].id
}

function buildFlowLanes(steps: ServiceTraceStep[]): FlowLane[] {
  const laneOrder = ['user', 'plan', 'policy', 'execute', 'evidence', 'summary', 'control', 'other']
  const labels: Record<string, string> = {
    user: '用户输入',
    plan: 'AI 规划',
    policy: '策略判定',
    execute: '设备执行',
    evidence: '证据处理',
    summary: '总结输出',
    control: '会话控制',
    other: '其他',
  }
  const lanes = new Map<string, ServiceTraceStep[]>()
  for (const key of laneOrder) lanes.set(key, [])
  const ordered = [...steps].sort((a, b) => a.seq_no - b.seq_no)
  for (const step of ordered) {
    const key = traceLaneKey(step.step_type)
    const list = lanes.get(key) || []
    list.push(step)
    lanes.set(key, list)
  }
  return laneOrder.map((key) => ({
    key,
    label: labels[key] || key,
    steps: lanes.get(key) || [],
  }))
}

function traceLaneKey(stepType: string): string {
  if (stepType === 'user_input') return 'user'
  if (stepType === 'llm_plan' || stepType === 'llm_status' || stepType === 'plan_decision') return 'plan'
  if (stepType === 'policy_decision') return 'policy'
  if (stepType === 'command_execution' || stepType === 'command_confirm_execution') return 'execute'
  if (stepType === 'evidence_parse') return 'evidence'
  if (stepType === 'llm_final') return 'summary'
  if (stepType === 'session_control') return 'control'
  return 'other'
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '-'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function traceTypeLabel(stepType: string): string {
  if (stepType === 'user_input') return '用户请求'
  if (stepType === 'llm_plan') return 'LLM 规划'
  if (stepType === 'llm_final') return 'LLM 总结'
  if (stepType === 'llm_status') return 'LLM 可用性'
  if (stepType === 'plan_decision') return '流程判定'
  if (stepType === 'policy_decision') return '策略判定'
  if (stepType === 'evidence_parse') return '证据解析'
  if (stepType === 'command_execution') return '命令执行'
  if (stepType === 'command_confirm_execution') return '确认后执行'
  if (stepType === 'session_control') return '会话控制'
  if (stepType === 'orchestrator_error') return '流程异常'
  return stepType
}

function traceStatusClass(status: string): string {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed' || status === 'blocked' || status === 'rejected') return 'err'
  if (status === 'pending_confirm' || status === 'running' || status === 'stopped') return 'warn'
  return 'idle'
}

function renderNavIcon(page: PageId): string {
  if (page === 'workbench') return '◫'
  if (page === 'control') return '⌘'
  if (page === 'command_policy') return '☑'
  if (page === 'sessions') return '☷'
  if (page === 'service_trace') return '⏱'
  if (page === 'learning') return '⌬'
  if (page === 'lab') return '△'
  return '◎'
}

export default App
