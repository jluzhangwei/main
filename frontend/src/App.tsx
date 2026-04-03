import { Button, Input, Select, Switch, message as antMessage } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { UIEvent } from 'react'
import { AutomationLevelSelector } from './components/AutomationLevelSelector'
import { DeviceForm } from './components/DeviceForm'
import { TaskModeSelector } from './components/TaskModeSelector'
import {
  archiveSop,
  approveRunActions,
  configureLlm,
  createRun,
  deleteCommandCapability,
  deleteLlmConfig,
  deleteSop,
  extractSopFromRun,
  exportRunMarkdown,
  getCommandCapability,
  getCommandPolicy,
  getLlmPromptPolicy,
  getLlmStatus,
  getRunTrace,
  getRiskPolicy,
  getSop,
  getRunTimeline,
  listSops,
  listRuns,
  publishSop,
  rejectRunActions,
  reextractSop,
  resetCommandPolicy,
  resetRiskPolicy,
  stopRun,
  streamRunEvents,
  streamRunMessage,
  upsertCommandCapability,
  updateSop,
  v2CreateApiKey,
  v2DeleteApiKey,
  v2GetPermissionTemplates,
  v2ListApiKeys,
  v2RotateApiKey,
  v2UpdateApiKey,
  updateCommandPolicy,
  updateRiskPolicy,
  updateRunCredentials,
  updateRunAutomation,
  resetCommandCapability,
} from './api/client'
import type {
  AutomationLevel,
  ChatMessage,
  CommandCapabilityRule,
  CommandPolicy,
  CommandExecution,
  DiagnosisSummary,
  Evidence,
  LLMPromptPolicy,
  LLMStatus,
  OperationMode,
  RiskPolicy,
  RunSummary,
  ServiceTraceStep,
  SessionResponse,
  SOPArchiveEntry,
  SOPStatus,
  SOPUpsertRequest,
  Timeline,
  V2ApiKey,
  V2JobSummary,
} from './types'

type PageId =
  | 'workbench'
  | 'third_party_keys'
  | 'control'
  | 'command_policy'
  | 'sessions'
  | 'service_trace'
  | 'sop_library'
  | 'learning'
  | 'lab'
  | 'ai_settings'

type PersistedUiState = {
  activePage?: PageId
  rightPanelWidth?: number
  sopListWidth?: number
  sopListDensity?: SopListDensity
  terminalSplitRatio?: number
  statusCollapsed?: boolean
  directionInput?: string
  currentSessionId?: string
  traceListExpanded?: boolean
  flowLayoutMode?: FlowLayoutMode
  activityViewMode?: ActivityViewMode
}

type HistorySessionItem = {
  id: string
  source_id: string
  run_id?: string
  kind: 'single' | 'multi'
  host: string
  device_name?: string
  protocol: 'ssh' | 'telnet' | 'api'
  automation_level: AutomationLevel
  operation_mode: OperationMode
  status: string
  created_at: string
  updated_at?: string
  problem?: string
  device_count?: number
  command_count?: number
  sop_extracted?: boolean
  sop_draft_count?: number
  sop_published_count?: number
  primary_sop_id?: string
}

const UI_STATE_KEY = 'netops_ui_prefs_v1'
const DEVICE_AUTH_CACHE_KEY = 'netops_device_auth_cache_v1'

type DeviceAuthRecord = {
  username?: string
  password?: string
  jump_host?: string
  jump_port?: number
  jump_username?: string
  jump_password?: string
  api_token?: string
  updated_at?: string
}
const NAV_SECTIONS: Array<{ key: string; label: string; items: Array<{ id: PageId; title: string }> }> = [
  {
    key: 'session',
    label: '会话',
    items: [
      { id: 'workbench', title: '诊断工作台' },
      { id: 'control', title: '连接控制' },
      { id: 'sessions', title: '会话历史' },
      { id: 'service_trace', title: '流程追踪' },
    ],
  },
  {
    key: 'control',
    label: '控制',
    items: [
      { id: 'command_policy', title: '命令执行控制' },
      { id: 'ai_settings', title: 'AI 设置' },
      { id: 'third_party_keys', title: '第三方 Key 服务' },
    ],
  },
  {
    key: 'learning',
    label: '迭代',
    items: [
      { id: 'sop_library', title: 'SOP 档案库' },
      { id: 'learning', title: '命令执行纠正' },
      { id: 'lab', title: 'Lab 对抗' },
    ],
  },
]

const NAV_ITEMS = NAV_SECTIONS.flatMap((section) => section.items)

const MODEL_OPTIONS = [
  { value: 'deepseek-chat', label: 'DeepSeek Chat' },
  { value: 'deepseek-reasoner', label: 'DeepSeek Reasoner' },
  { value: 'meta/llama-3.1-70b-instruct', label: 'NVIDIA Llama 3.1 70B' },
  { value: 'gpt-5.3-codex', label: 'GPT-5.3-Codex' },
  { value: 'gpt-5.4', label: 'GPT-5.4' },
]

const SOP_CURRENT_LOGIC_ITEMS = [
  '按用户问题关键词匹配 SOP 档案候选，例如“上次 / 历史 / 闪断 / OSPF”。',
  '命中的 SOP 不会直接执行，而是被整理成 planner_context 注入给 AI。',
  'AI 自主决定是否引用某个 SOP、引用哪一个、以及如何改写成当前设备更合适的命令。',
  '系统只执行 AI 最终返回的命令，不会直接执行 SOP 模板原文。',
  '即使 AI 引用了 SOP，命令仍要经过模式范围、能力规则、风险规则和审批策略。',
  '当 AI 在 title / reason 中明确提到 SOP 名称或 id 时，流程图会记录“AI 引用 SOP 档案”。',
]

const SOP_HARDENING_HINTS = [
  '把“自然语言引用”升级成结构化字段，例如 sop_refs，减少漏记。',
  '给 SOP 增加适用前提、禁用条件、推荐最小命令组和证据目标。',
  '按厂商、版本指纹、场景标签拆得更细，减少 AI 自行改写成本。',
  '给 SOP 增加“引用成功率 / 命中率 / 误用率”统计，便于长期优化。',
]

const SOP_SEMANTIC_ALIAS_GROUPS = [
  {
    label: '接口 / 端口',
    aliases: ['接口', '端口', 'interface', 'port', 'ethernet', 'gigabitethernet', 'et'],
  },
  {
    label: '管理性关闭',
    aliases: ['shutdown', 'admin shutdown', 'admin down', 'administratively down', 'disabled', 'disable'],
  },
  {
    label: '物理断链',
    aliases: ['物理断链', '断链', '链路断开', 'link down', 'physical down', 'los'],
  },
  {
    label: 'OSPF 邻接',
    aliases: ['ospf', '邻接', 'neighbor', 'adjacency'],
  },
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
  | {
      key: string
      kind: 'trace'
      createdAt: string
      label: string
      title: string
      preview: string
      trace: ServiceTraceStep
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
  actor: string
  steps: Array<ServiceTraceStep | null>
  realCount: number
}

type FlowLayoutMode = 'compact' | 'stair'
type ActivityViewMode = 'full' | 'compact'
type SopTab = 'draft' | 'published' | 'archived' | 'logic'
type SopListDensity = 'compact' | 'expanded'

type TraceDetailSection = {
  key: string
  title: string
  body: string
}

type ContinuePreviewItem = {
  key: string
  step_no: number
  title: string
  risk_level: CommandExecution['risk_level']
  status: string
  commandLines: string[]
}

type ContinuePreview = {
  source: 'pending' | 'none'
  items: ContinuePreviewItem[]
  totalCommands: number
}

type ContinueExecutionCommand = {
  id: string
  step_no: number
  title: string
  risk_level: CommandExecution['risk_level']
  command: string
  status: string
}

type ContinueExecutionState = {
  active: boolean
  baselineStepNo: number
  plannedCommands: ContinuePreviewItem[]
  observedCommands: ContinueExecutionCommand[]
}

type SendOptions = {
  continueExecution?: {
    baselineStepNo: number
  }
}

type MultiSessionConfig = {
  hosts: string[]
  protocol: 'ssh' | 'telnet' | 'api'
  operation_mode: OperationMode
  username?: string
  password?: string
  jump_host?: string
  jump_port?: number
  jump_username?: string
  jump_password?: string
  api_token?: string
}

const V3_DEFAULT_PERMISSIONS = [
  'job.read',
  'job.write',
  'command.execute',
  'command.approve',
  'policy.write',
  'audit.read',
]

function App() {
  const [automationLevel, setAutomationLevel] = useState<AutomationLevel>('assisted')
  const [operationMode, setOperationMode] = useState<OperationMode>('diagnosis')
  const [session, setSession] = useState<SessionResponse | null>(null)
  const [sessionRuntimeKind, setSessionRuntimeKind] = useState<'single' | 'multi'>('single')
  const [multiSessionConfig, setMultiSessionConfig] = useState<MultiSessionConfig | null>(null)
  const [multiSessionActiveJobId, setMultiSessionActiveJobId] = useState<string | undefined>(undefined)
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
  const [nvidiaApiKeyInput, setNvidiaApiKeyInput] = useState('')
  const [llmModelInput, setLlmModelInput] = useState('deepseek-chat')
  const [llmFailoverEnabled, setLlmFailoverEnabled] = useState(true)
  const [llmBatchExecutionEnabled, setLlmBatchExecutionEnabled] = useState(true)
  const [llmSaving, setLlmSaving] = useState(false)
  const [v3ApiKeyName, setV3ApiKeyName] = useState('ops-admin')
  const [v3ApiKeyPermissions, setV3ApiKeyPermissions] = useState(V3_DEFAULT_PERMISSIONS.join(','))
  const [v3ApiKeyLoading, setV3ApiKeyLoading] = useState(false)
  const [v3ApiKeys, setV3ApiKeys] = useState<V2ApiKey[]>([])
  const [v3LastCreatedSecret, setV3LastCreatedSecret] = useState('')
  const [v3JobsLoading, setV3JobsLoading] = useState(false)
  const [v3Jobs, setV3Jobs] = useState<V2JobSummary[]>([])
  const [v3SelectedJobId, setV3SelectedJobId] = useState<string | undefined>(undefined)
  const [v3PermissionTemplates, setV3PermissionTemplates] = useState<Record<string, string[]>>({})
  const [v3SelectedTemplateName, setV3SelectedTemplateName] = useState<string | undefined>(undefined)
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
  const [policyTab, setPolicyTab] = useState<'blocked' | 'executable' | 'high' | 'medium'>('blocked')
  const [editingRule, setEditingRule] = useState<
    { kind: 'blocked' | 'executable'; index: number; value: string } | undefined
  >(undefined)
  const [editingRiskRule, setEditingRiskRule] = useState<
    { kind: 'high' | 'medium'; index: number; value: string } | undefined
  >(undefined)
  const [commandCapabilityRules, setCommandCapabilityRules] = useState<CommandCapabilityRule[]>([])
  const [capabilityLoading, setCapabilityLoading] = useState(false)
  const [capabilitySaving, setCapabilitySaving] = useState(false)
  const [capabilityVersionFilter, setCapabilityVersionFilter] = useState('')
  const [capabilityVersionInput, setCapabilityVersionInput] = useState('')
  const [capabilityCommandSearch, setCapabilityCommandSearch] = useState('')
  const [capabilityCommandInput, setCapabilityCommandInput] = useState('')
  const [capabilityActionInput, setCapabilityActionInput] = useState<'rewrite' | 'block'>('rewrite')
  const [capabilityRewriteInput, setCapabilityRewriteInput] = useState('')
  const [capabilityReasonInput, setCapabilityReasonInput] = useState('')
  const [editingCapabilityId, setEditingCapabilityId] = useState<string | undefined>(undefined)
  const [sopLoading, setSopLoading] = useState(false)
  const [sopSaving, setSopSaving] = useState(false)
  const [sopTab, setSopTab] = useState<SopTab>('draft')
  const [sopDrafts, setSopDrafts] = useState<SOPArchiveEntry[]>([])
  const [sopPublished, setSopPublished] = useState<SOPArchiveEntry[]>([])
  const [sopArchived, setSopArchived] = useState<SOPArchiveEntry[]>([])
  const [selectedSopId, setSelectedSopId] = useState<string | undefined>(undefined)
  const [selectedSop, setSelectedSop] = useState<SOPArchiveEntry | null>(null)
  const [sopEditor, setSopEditor] = useState<SOPUpsertRequest | null>(null)
  const [extractingSopHistoryId, setExtractingSopHistoryId] = useState<string | undefined>(undefined)
  const policyImportInputRef = useRef<HTMLInputElement | null>(null)

  const [activePage, setActivePage] = useState<PageId>('workbench')
  const [statusCollapsed, setStatusCollapsed] = useState(false)
  const [rightPanelWidth, setRightPanelWidth] = useState(getDefaultWorkbenchRightWidth)
  const [sopListWidth, setSopListWidth] = useState(420)
  const [sopListDensity, setSopListDensity] = useState<SopListDensity>('compact')
  const [resizing, setResizing] = useState(false)
  const [sopResizing, setSopResizing] = useState(false)
  const [terminalSplitRatio, setTerminalSplitRatio] = useState(0.5)
  const [terminalResizing, setTerminalResizing] = useState(false)
  const [directionInput, setDirectionInput] = useState('')
  const [draftInput, setDraftInput] = useState('')
  const [sessionDeviceAddress, setSessionDeviceAddress] = useState('')
  const [sessionDeviceName, setSessionDeviceName] = useState('')
  const [sessionVersionSignature, setSessionVersionSignature] = useState('')
  const [sessionHistory, setSessionHistory] = useState<HistorySessionItem[]>([])
  const [selectedHistorySessionId, setSelectedHistorySessionId] = useState<string | undefined>(undefined)
  const [selectedHistorySnapshot, setSelectedHistorySnapshot] = useState<Timeline | null>(null)
  const [selectedHistorySnapshotTrace, setSelectedHistorySnapshotTrace] = useState<ServiceTraceStep[]>([])
  const [historySnapshotLoading, setHistorySnapshotLoading] = useState(false)
  const historySnapshotCacheRef = useRef<Record<string, Timeline>>({})
  const historySnapshotTraceCacheRef = useRef<Record<string, ServiceTraceStep[]>>({})
  const historySnapshotRequestRef = useRef(0)
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [initialSessionId, setInitialSessionId] = useState<string | undefined>(undefined)
  const [bootstrapped, setBootstrapped] = useState(false)
  const [selectedCommandId, setSelectedCommandId] = useState<string | undefined>(undefined)
  const [selectedActivityKey, setSelectedActivityKey] = useState<string | undefined>(undefined)
  const [traceSteps, setTraceSteps] = useState<ServiceTraceStep[]>([])
  const [traceLoading, setTraceLoading] = useState(false)
  const [selectedTraceStepId, setSelectedTraceStepId] = useState<string | undefined>(undefined)
  const [traceListExpanded, setTraceListExpanded] = useState(false)
  const [flowLayoutMode, setFlowLayoutMode] = useState<FlowLayoutMode>('stair')
  const [activityViewMode, setActivityViewMode] = useState<ActivityViewMode>('full')
  const [tracePlaybackActive, setTracePlaybackActive] = useState(false)
  const [continueExecutionState, setContinueExecutionState] = useState<ContinueExecutionState | null>(null)
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const chatLogRef = useRef<HTMLElement | null>(null)
  const [commandAutoScrollEnabled, setCommandAutoScrollEnabled] = useState(true)
  const [showCommandScrollToBottom, setShowCommandScrollToBottom] = useState(false)
  const commandListRef = useRef<HTMLDivElement | null>(null)
  const traceListScrollRef = useRef<HTMLDivElement | null>(null)
  const tracePlaybackTimerRef = useRef<number | null>(null)
  const traceSeqRef = useRef(0)
  const terminalGridRef = useRef<HTMLDivElement | null>(null)
  const streamAbortRef = useRef<AbortController | null>(null)
  const traceEventAbortRef = useRef<AbortController | null>(null)
  const multiJobAbortRef = useRef<{ aborted: boolean; jobId?: string } | null>(null)

  const sessionReady = useMemo(() => Boolean(session?.id), [session])
  const commandDisplayRows = useMemo(() => groupCommandsForDisplay(commands), [commands])
  const commandListRows = useMemo(
    () => [...commands].sort((a, b) => a.step_no - b.step_no),
    [commands],
  )
  const continuePreview = useMemo<ContinuePreview>(
    () => buildContinuePreview(commands, summary),
    [commands, summary],
  )
  const latestAiDecision = useMemo(
    () => extractLatestAiDecision(traceSteps),
    [traceSteps],
  )
  const activityCards = useMemo(
    () => filterActivityCards(buildActivityCards(messages, commands, summary, traceSteps), activityViewMode),
    [messages, commands, summary, traceSteps, activityViewMode],
  )

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
  const flowLanes = useMemo(() => buildFlowLanes(traceSteps, flowLayoutMode), [traceSteps, flowLayoutMode])
  const traceLaneCounts = useMemo(() => buildTraceLaneCounts(traceSteps), [traceSteps])
  const flowRows = useMemo(() => {
    if (flowLayoutMode !== 'stair') return []
    const rowCount = flowLanes.reduce((max, lane) => Math.max(max, lane.steps.length), 0)
    const rows: Array<Array<{ lane: FlowLane; step: ServiceTraceStep | null }>> = []
    for (let row = 0; row < rowCount; row += 1) {
      rows.push(
        flowLanes.map((lane) => ({
          lane,
          step: lane.steps[row] || null,
        })),
      )
    }
    return rows
  }, [flowLanes, flowLayoutMode])
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
  const selectedTraceDetailSections = useMemo(
    () => (selectedTraceStep ? buildTraceDetailSections(selectedTraceStep, traceSteps) : []),
    [selectedTraceStep, traceSteps],
  )
  const activeFlowLaneKey = useMemo(() => {
    if (!selectedTraceStep) return undefined
    return traceLaneKey(selectedTraceStep)
  }, [selectedTraceStep])
  const sortedTraceSteps = useMemo(
    () => sortTraceStepsByOrder(traceSteps),
    [traceSteps],
  )
  const selectedTraceIndex = useMemo(() => {
    if (!selectedTraceStep) return -1
    return sortedTraceSteps.findIndex((item) => item.id === selectedTraceStep.id)
  }, [selectedTraceStep, sortedTraceSteps])
  const previousTraceStep = useMemo(() => {
    if (selectedTraceIndex <= 0) return undefined
    return sortedTraceSteps[selectedTraceIndex - 1]
  }, [selectedTraceIndex, sortedTraceSteps])
  const nextTraceStep = useMemo(() => {
    if (selectedTraceIndex < 0 || selectedTraceIndex >= sortedTraceSteps.length - 1) return undefined
    return sortedTraceSteps[selectedTraceIndex + 1]
  }, [selectedTraceIndex, sortedTraceSteps])
  const llmControlsLocked = useMemo(() => busy || Boolean(confirmingCommandId), [busy, confirmingCommandId])

  const selectedActivity = useMemo(() => {
    if (activityCards.length === 0) return undefined
    if (!selectedActivityKey) return activityCards[activityCards.length - 1]
    return activityCards.find((item) => item.key === selectedActivityKey) || activityCards[activityCards.length - 1]
  }, [activityCards, selectedActivityKey])
  const selectedHistoryItem = useMemo(
    () => sessionHistory.find((item) => item.id === selectedHistorySessionId),
    [sessionHistory, selectedHistorySessionId],
  )
  const currentSopItems = useMemo(() => {
    if (sopTab === 'draft') return sopDrafts
    if (sopTab === 'published') return sopPublished
    if (sopTab === 'archived') return sopArchived
    return []
  }, [sopArchived, sopDrafts, sopPublished, sopTab])
  const traceTargetSessionId = useMemo(() => {
    if (selectedHistorySessionId && sessionHistory.some((item) => item.id === selectedHistorySessionId)) {
      return selectedHistorySessionId
    }
    if (session?.id) return session.id
    return sessionHistory[0]?.id
  }, [selectedHistorySessionId, sessionHistory, session?.id])
  const traceTargetHistoryItem = useMemo(
    () => sessionHistory.find((item) => item.id === traceTargetSessionId),
    [sessionHistory, traceTargetSessionId],
  )
  const liveTraceRunId = useMemo(() => {
    if (!session?.id) return undefined
    if (!busy && !stoppingSession) return undefined
    if (activePage !== 'workbench' && !(activePage === 'service_trace' && traceTargetSessionId === session.id)) {
      return undefined
    }
    return resolveUnifiedRunId({
      targetId: session.id,
      sessionHistory,
      activeSessionId: session.id,
      sessionRuntimeKind,
      multiSessionActiveJobId,
    })
  }, [activePage, busy, multiSessionActiveJobId, session?.id, sessionHistory, sessionRuntimeKind, stoppingSession, traceTargetSessionId])
  const traceSessionOptions = useMemo(() => {
    const options: Array<{ value: string; label: string }> = []
    const seen = new Set<string>()
    for (const item of sessionHistory) {
      if (!item.id || seen.has(item.id)) continue
      seen.add(item.id)
      const mode = operationModeLabel(item.operation_mode)
      const kind = item.kind === 'multi' ? '多设备协同' : '单设备会话'
      const label = `${kind} · ${item.host} · ${mode} · ${item.source_id.slice(0, 8)}...`
      options.push({ value: item.id, label })
    }
    if (session?.id && !seen.has(session.id)) {
      options.unshift({
        value: session.id,
        label: `当前会话 · ${sessionDeviceAddress || '-'} · ${operationModeLabel(session.operation_mode)} · ${session.id.slice(0, 8)}...`,
      })
    }
    return options
  }, [sessionHistory, session?.id, session?.operation_mode, sessionDeviceAddress])
  const selectedHistorySnapshotView = useMemo<Timeline | null>(() => {
    if (selectedHistorySnapshot && (!selectedHistorySessionId || selectedHistorySnapshot.session.id === selectedHistorySessionId)) {
      return selectedHistorySnapshot
    }
    if (selectedHistorySessionId && session?.id === selectedHistorySessionId) {
      return {
        session: {
          id: session.id,
          automation_level: session.automation_level,
          operation_mode: session.operation_mode,
          status: session.status,
          created_at: session.created_at,
          device: {
            host: sessionDeviceAddress || selectedHistoryItem?.host || '-',
            name: sessionDeviceName || selectedHistoryItem?.device_name || undefined,
            protocol: selectedHistoryItem?.protocol || 'ssh',
            version_signature: sessionVersionSignature || undefined,
          },
        },
        messages,
        commands,
        evidences,
        summary,
      }
    }
    return null
  }, [
    selectedHistorySnapshot,
    selectedHistorySessionId,
    session,
    sessionDeviceAddress,
    selectedHistoryItem,
    sessionDeviceName,
    sessionVersionSignature,
    messages,
    commands,
    evidences,
    summary,
  ])
  const snapshotMessages = selectedHistorySnapshotView?.messages || []
  const snapshotSummary = selectedHistorySnapshotView?.summary
  const snapshotLatestUserQuestion = useMemo(
    () => extractLatestMessageByRole(snapshotMessages, 'user') || '-',
    [snapshotMessages],
  )
  const snapshotLatestConclusion = useMemo(
    () => summarizeSessionConclusion(snapshotSummary),
    [snapshotSummary],
  )
  const snapshotLatestRecommendation = useMemo(
    () => summarizeSessionRecommendation(snapshotSummary),
    [snapshotSummary],
  )
  const snapshotUpdatedAt = useMemo(
    () => computeSessionLastUpdatedAt(selectedHistorySnapshotView),
    [selectedHistorySnapshotView],
  )
  const snapshotTraceView = useMemo<ServiceTraceStep[]>(() => {
    if (selectedHistorySessionId && selectedHistorySnapshotTrace.length > 0) {
      return selectedHistorySnapshotTrace
    }
    if (selectedHistorySessionId && session?.id === selectedHistorySessionId) {
      return traceSteps
    }
    return selectedHistorySnapshotTrace
  }, [selectedHistorySessionId, selectedHistorySnapshotTrace, session?.id, traceSteps])
  const snapshotSopCandidateHits = useMemo(
    () => snapshotTraceView.filter((step) => step.step_type === 'sop_candidates_generated').length,
    [snapshotTraceView],
  )
  const snapshotSopReferencedCount = useMemo(
    () => snapshotTraceView.filter((step) => step.step_type === 'sop_referenced_by_ai').length,
    [snapshotTraceView],
  )
  const snapshotPrimarySopId = selectedHistoryItem?.primary_sop_id
  const v3SelectedJobSummary = useMemo(
    () => v3Jobs.find((item) => item.id === v3SelectedJobId),
    [v3Jobs, v3SelectedJobId],
  )
  const recentMultiJobItems = useMemo(
    () => [...v3Jobs].sort((left, right) => Date.parse(String(right.created_at || '')) - Date.parse(String(left.created_at || ''))).slice(0, 6),
    [v3Jobs],
  )

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
    if (selectedActivity) return renderActivityDetail(selectedActivity, traceSteps)
    if (summary) return renderSummaryBrief(summary)
    return '等待 AI 诊断输出...'
  }, [selectedActivity, selectedCommandRow, summary, traceSteps])

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
  const capabilityRows = useMemo(() => {
    const signature = capabilityVersionFilter.trim().toLowerCase()
    const commandKeyword = capabilityCommandSearch.trim().toLowerCase()
    return commandCapabilityRules.filter((item) => {
      const versionMatch = !signature || (item.version_signature || '').toLowerCase().includes(signature)
      const commandMatch = !commandKeyword || (item.command_key || '').toLowerCase().includes(commandKeyword)
      return versionMatch && commandMatch
    })
  }, [commandCapabilityRules, capabilityVersionFilter, capabilityCommandSearch])
  const capabilityEffectiveVersion = useMemo(() => {
    const explicit = capabilityVersionFilter.trim()
    if (explicit) return explicit
    const sessionSig = sessionVersionSignature.trim()
    if (sessionSig) return sessionSig
    const unique = Array.from(
      new Set(
        commandCapabilityRules
          .map((item) => String(item.version_signature || '').trim())
          .filter(Boolean),
      ),
    )
    if (unique.length === 1) return unique[0]
    return ''
  }, [capabilityVersionFilter, sessionVersionSignature, commandCapabilityRules])
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
      if (typeof parsed.sopListWidth === 'number' && Number.isFinite(parsed.sopListWidth)) {
        setSopListWidth(Math.min(640, Math.max(320, parsed.sopListWidth)))
      }
      if (parsed.sopListDensity === 'compact' || parsed.sopListDensity === 'expanded') {
        setSopListDensity(parsed.sopListDensity)
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
      if (typeof parsed.traceListExpanded === 'boolean') {
        setTraceListExpanded(parsed.traceListExpanded)
      }
      if (parsed.flowLayoutMode === 'compact' || parsed.flowLayoutMode === 'stair') {
        setFlowLayoutMode(parsed.flowLayoutMode)
      }
      if (parsed.activityViewMode === 'compact' || parsed.activityViewMode === 'full') {
        setActivityViewMode(parsed.activityViewMode)
      }
    } catch {
      // ignore local storage parse errors
    }
  }, [])

  useEffect(() => {
    const payload: PersistedUiState = {
      activePage,
      rightPanelWidth,
      sopListWidth,
      sopListDensity,
      terminalSplitRatio,
      statusCollapsed,
      directionInput,
      currentSessionId: session?.id,
      traceListExpanded,
      flowLayoutMode,
      activityViewMode,
    }
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(payload))
  }, [activePage, rightPanelWidth, sopListWidth, sopListDensity, terminalSplitRatio, statusCollapsed, directionInput, session?.id, traceListExpanded, flowLayoutMode, activityViewMode])

  useEffect(() => {
    if (commands.length === 0) {
      setSelectedCommandId(undefined)
      return
    }
    setSelectedCommandId(commands[commands.length - 1].id)
  }, [commands])

  useEffect(() => {
    if (activePage !== 'third_party_keys') return
    void refreshV3ApiKeys()
    void refreshV3PermissionTemplates()
  }, [activePage])

  useEffect(() => {
    if (!sessionVersionSignature) return
    if (capabilityVersionFilter.trim()) return
    setCapabilityVersionFilter(sessionVersionSignature)
  }, [sessionVersionSignature, capabilityVersionFilter])

  useEffect(() => {
    if (activePage !== 'learning') return
    void refreshCommandCapabilityRules()
  }, [activePage])

  useEffect(() => {
    if (activePage !== 'sop_library') return
    void refreshSops()
  }, [activePage, sopTab])

  useEffect(() => {
    if (activePage !== 'service_trace') return
    if (session?.id) {
      setSelectedHistorySessionId(session.id)
    }
  }, [activePage, session?.id])

  useEffect(() => {
    if (activePage !== 'service_trace') return
    void refreshSessionHistory()
  }, [activePage])

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
    if (activePage !== 'service_trace') return
    if (!selectedTraceStepId) return
    const container = traceListScrollRef.current
    if (!container) return
    const escaped = selectedTraceStepId.replace(/"/g, '\\"')
    const target = container.querySelector<HTMLElement>(`[data-trace-list-step-id="${escaped}"]`)
    if (!target) return
    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [activePage, selectedTraceStepId, traceSteps])

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
    if (sessionHistory.length === 0) {
      setSelectedHistorySessionId(undefined)
      setSelectedHistorySnapshot(null)
      return
    }
    if (selectedHistorySessionId && sessionHistory.some((item) => item.id === selectedHistorySessionId)) {
      return
    }
    if (session?.id && sessionHistory.some((item) => item.id === session.id)) {
      setSelectedHistorySessionId(session.id)
      return
    }
    setSelectedHistorySessionId(sessionHistory[0].id)
  }, [sessionHistory, selectedHistorySessionId, session?.id])

  useEffect(() => {
    if (!selectedHistorySessionId) {
      setSelectedHistorySnapshot(null)
      setHistorySnapshotLoading(false)
      return
    }
    void loadHistorySnapshot(selectedHistorySessionId, false)
  }, [selectedHistorySessionId])

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
    if (!sopResizing) return

    const onMove = (event: MouseEvent) => {
      const min = 320
      const max = Math.max(420, Math.min(720, window.innerWidth - 420))
      setSopListWidth(Math.max(min, Math.min(max, event.clientX - 120)))
    }

    const onUp = () => setSopResizing(false)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)

    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [sopResizing])

  useEffect(() => {
    if (activePage !== 'service_trace') return
    if (!traceTargetSessionId) return
    void refreshServiceTraceForTarget(traceTargetSessionId, true)
  }, [activePage, traceTargetSessionId])

  useEffect(() => {
    traceSeqRef.current = getMaxTraceSeqNo(traceSteps)
  }, [traceSteps])

  useEffect(() => {
    traceEventAbortRef.current?.abort()
    if (!liveTraceRunId) return undefined

    const abortController = new AbortController()
    traceEventAbortRef.current = abortController
    const currentSessionId = session?.id
    const bindToTarget = activePage === 'service_trace' && traceTargetSessionId === currentSessionId
    const ownerSessionId = bindToTarget ? traceTargetSessionId : undefined
    const fromSeq = traceSeqRef.current

    setTraceLoading(true)
    void streamRunEvents(
      resolveV3ApiKey(),
      liveTraceRunId,
      fromSeq,
      (event, payload) => {
        if (event === 'trace_step') {
          const step = payload as ServiceTraceStep
          const normalized = ownerSessionId ? { ...step, session_id: ownerSessionId } : step
          setTraceSteps((prev) => mergeTraceSteps(prev, [normalized]))
          setTraceLoading(false)
          return
        }
        if (event === 'completed') {
          setTraceLoading(false)
        }
      },
      abortController.signal,
    )
      .catch(async (error) => {
        if ((error as Error).name === 'AbortError') return
        if (ownerSessionId) {
          await refreshServiceTraceForTarget(ownerSessionId, true)
          return
        }
        if (currentSessionId) {
          await refreshServiceTrace(currentSessionId)
        }
      })
      .finally(() => {
        if (traceEventAbortRef.current === abortController) {
          traceEventAbortRef.current = null
        }
        setTraceLoading(false)
      })

    return () => {
      abortController.abort()
      if (traceEventAbortRef.current === abortController) {
        traceEventAbortRef.current = null
      }
    }
  }, [activePage, liveTraceRunId, session?.id, traceTargetSessionId])

  useEffect(() => {
    return () => {
      traceEventAbortRef.current?.abort()
      if (tracePlaybackTimerRef.current !== null) {
        window.clearTimeout(tracePlaybackTimerRef.current)
        tracePlaybackTimerRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!tracePlaybackActive || activePage !== 'service_trace') {
      if (tracePlaybackTimerRef.current !== null) {
        window.clearTimeout(tracePlaybackTimerRef.current)
        tracePlaybackTimerRef.current = null
      }
      return
    }
    if (sortedTraceSteps.length <= 1) {
      setTracePlaybackActive(false)
      return
    }
    const currentIndex = selectedTraceIndex >= 0 ? selectedTraceIndex : 0
    if (currentIndex >= sortedTraceSteps.length - 1) {
      setTracePlaybackActive(false)
      return
    }
    const currentStep = sortedTraceSteps[currentIndex]
    const nextStep = sortedTraceSteps[currentIndex + 1]
    const delayMs = computeTracePlaybackDelay(currentStep, nextStep)
    tracePlaybackTimerRef.current = window.setTimeout(() => {
      jumpToTraceStep(nextStep.id)
    }, delayMs)
    return () => {
      if (tracePlaybackTimerRef.current !== null) {
        window.clearTimeout(tracePlaybackTimerRef.current)
        tracePlaybackTimerRef.current = null
      }
    }
  }, [tracePlaybackActive, activePage, sortedTraceSteps, selectedTraceIndex])

  useEffect(() => {
    if (activePage !== 'service_trace') return

    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const tagName = String(target?.tagName || '').toLowerCase()
      const isEditable = Boolean(
        target?.isContentEditable
        || tagName === 'input'
        || tagName === 'textarea'
        || tagName === 'select',
      )
      if (isEditable) return

      if (event.key === 'ArrowLeft' && previousTraceStep) {
        event.preventDefault()
        jumpToTraceStep(previousTraceStep.id)
        return
      }
      if (event.key === 'ArrowRight' && nextTraceStep) {
        event.preventDefault()
        jumpToTraceStep(nextTraceStep.id)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activePage, previousTraceStep, nextTraceStep])

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
        const updated = await updateRunAutomation(resolveV3ApiKey(), toUnifiedSingleRunId(session.id), automationLevel)
        if (canceled) return
        setSession(updated)
        antMessage.success(`命令执行控制等级已切换为 ${automationLabel(updated.automation_level)}`)
      } catch {
        if (canceled) return
        antMessage.error('命令执行控制等级切换失败，已恢复原设置')
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

      await refreshCommandCapabilityRules()

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
      const runs = await listRuns(resolveV3ApiKey(), {
        offset: 0,
        limit: 200,
      }).then((payload) => payload.items).catch(() => [] as RunSummary[])
      const historyItems = buildHistorySessionItemsFromRuns(runs)
      const multiJobs = runs
        .filter((item) => item.kind === 'multi')
        .map((item) => mapRunToV2JobSummary(item))
      setV3Jobs(multiJobs)
      setV3SelectedJobId((prev) => {
        if (prev && multiJobs.some((item) => item.id === prev)) return prev
        return multiJobs[0]?.id
      })
      setSessionHistory(historyItems)
      return historyItems
    } catch {
      setV3Jobs([])
      setSessionHistory([])
      return [] as HistorySessionItem[]
    } finally {
      setSessionsLoading(false)
    }
  }

  async function refreshCommandCapabilityRules(versionSignature?: string) {
    setCapabilityLoading(true)
    try {
      const rows = await getCommandCapability({ version_signature: versionSignature || undefined })
      setCommandCapabilityRules(rows)
    } catch {
      setCommandCapabilityRules([])
    } finally {
      setCapabilityLoading(false)
    }
  }

  async function refreshSops(targetTab: SopTab = sopTab) {
    if (targetTab === 'logic') return
    setSopLoading(true)
    try {
      const status = targetTab as SOPStatus
      const payload = await listSops(resolveV3ApiKey(), status)
      if (targetTab === 'draft') setSopDrafts(payload.items)
      if (targetTab === 'published') setSopPublished(payload.items)
      if (targetTab === 'archived') setSopArchived(payload.items)

      const nextItems = payload.items
      const nextSelectedId = nextItems.some((item) => item.id === selectedSopId) ? selectedSopId : nextItems[0]?.id
      setSelectedSopId(nextSelectedId)
      if (nextSelectedId) {
        const detail = await getSop(resolveV3ApiKey(), nextSelectedId)
        setSelectedSop(detail)
        setSopEditor(toSopEditor(detail))
      } else {
        setSelectedSop(null)
        setSopEditor(null)
      }
    } catch (error) {
      if (targetTab === 'draft') setSopDrafts([])
      if (targetTab === 'published') setSopPublished([])
      if (targetTab === 'archived') setSopArchived([])
      setSelectedSop(null)
      setSopEditor(null)
      antMessage.error((error as Error).message || '加载 SOP 档案失败')
    } finally {
      setSopLoading(false)
    }
  }

  async function handleSelectSop(sopId: string) {
    setSelectedSopId(sopId)
    setSopLoading(true)
    try {
      const detail = await getSop(resolveV3ApiKey(), sopId)
      setSelectedSop(detail)
      setSopEditor(toSopEditor(detail))
    } catch (error) {
      antMessage.error((error as Error).message || '加载 SOP 详情失败')
    } finally {
      setSopLoading(false)
    }
  }

  async function resolveSopForHistoryItem(item: HistorySessionItem): Promise<{ id: string; tab: SopTab } | null> {
    if (item.primary_sop_id) {
      return {
        id: item.primary_sop_id,
        tab: item.sop_published_count ? 'published' : 'draft',
      }
    }
    const candidateSourceIds = [
      item.source_id,
      item.run_id,
      parseV2HistoryJobId(item.id),
      item.id,
    ]
      .map((value) => String(value || '').trim())
      .filter(Boolean)
    const statuses: SOPStatus[] = ['published', 'draft', 'archived']
    for (const status of statuses) {
      const payload = await listSops(resolveV3ApiKey(), status)
      const matched = payload.items.find((entry) =>
        (entry.source_run_ids || []).some((sourceId) => candidateSourceIds.includes(String(sourceId || '').trim())),
      )
      if (matched) {
        return {
          id: matched.id,
          tab: (matched.status || status) as SopTab,
        }
      }
    }
    return null
  }

  async function handleExtractSop(item: HistorySessionItem) {
    const hasExtractedSop = Boolean(item.primary_sop_id || item.sop_extracted || item.sop_draft_count || item.sop_published_count)
    if (hasExtractedSop) {
      let target = await resolveSopForHistoryItem(item)
      if (!target) {
        const refreshedHistory = await refreshSessionHistory()
        const refreshedItem = refreshedHistory.find((entry) => entry.id === item.id || entry.run_id === item.run_id || entry.source_id === item.source_id)
        if (refreshedItem) {
          target = await resolveSopForHistoryItem(refreshedItem)
        }
      }
      if (!target) {
        antMessage.warning('该历史会话已提取 SOP，但暂未定位到对应档案。')
        return
      }
      setActivePage('sop_library')
      setSopTab(target.tab)
      setSelectedSopId(target.id)
      await handleSelectSop(target.id)
      return
    }
    const runId = item.run_id || resolveUnifiedRunId({
      targetId: item.id,
      sessionHistory,
      activeSessionId: session?.id,
      sessionRuntimeKind,
      multiSessionActiveJobId,
    })
    if (!runId) {
      antMessage.warning('当前历史记录缺少统一运行 ID，暂时无法提取 SOP')
      return
    }
    setExtractingSopHistoryId(item.id)
    try {
      const draft = await extractSopFromRun(resolveV3ApiKey(), {
        run_id: runId,
        force: item.sop_extracted,
      })
      antMessage.success(`已生成 SOP 草稿：${draft.name}`)
      setActivePage('sop_library')
      setSopTab('draft')
      await refreshSessionHistory()
      await refreshSops('draft')
      setSelectedSopId(draft.id)
      setSelectedSop(draft)
      setSopEditor(toSopEditor(draft))
    } catch (error) {
      antMessage.error((error as Error).message || '提取 SOP 失败')
    } finally {
      setExtractingSopHistoryId(undefined)
    }
  }

  async function handleSaveSop() {
    if (!selectedSop || !sopEditor) return
    setSopSaving(true)
    try {
      const saved = await updateSop(resolveV3ApiKey(), selectedSop.id, sopEditor)
      antMessage.success(selectedSop.status === 'published' ? '已从发布版本生成新的草稿版本' : 'SOP 草稿已保存')
      const nextTab = (saved.status || 'draft') as SopTab
      setSopTab(nextTab)
      await refreshSops(nextTab)
      setSelectedSopId(saved.id)
      setSelectedSop(saved)
      setSopEditor(toSopEditor(saved))
    } catch (error) {
      antMessage.error((error as Error).message || '保存 SOP 失败')
    } finally {
      setSopSaving(false)
    }
  }

  async function handlePublishSop() {
    if (!selectedSop) return
    setSopSaving(true)
    try {
      const published = await publishSop(resolveV3ApiKey(), selectedSop.id)
      antMessage.success('SOP 已发布，并开始参与 AI 候选匹配')
      setSopTab('published')
      await refreshSops('published')
      setSelectedSopId(published.id)
      setSelectedSop(published)
      setSopEditor(toSopEditor(published))
      await refreshSessionHistory()
    } catch (error) {
      antMessage.error((error as Error).message || '发布 SOP 失败')
    } finally {
      setSopSaving(false)
    }
  }

  async function handleArchiveSop() {
    if (!selectedSop) return
    setSopSaving(true)
    try {
      const archived = await archiveSop(resolveV3ApiKey(), selectedSop.id)
      antMessage.success('SOP 已归档，不再参与运行时匹配')
      setSopTab('archived')
      await refreshSops('archived')
      setSelectedSopId(archived.id)
      setSelectedSop(archived)
      setSopEditor(toSopEditor(archived))
      await refreshSessionHistory()
    } catch (error) {
      antMessage.error((error as Error).message || '归档 SOP 失败')
    } finally {
      setSopSaving(false)
    }
  }

  async function handleReextractSop() {
    if (!selectedSop) return
    const confirmed = window.confirm('将基于原始来源会话重新提炼，并生成新的 draft 版本，是否继续？')
    if (!confirmed) return
    setSopSaving(true)
    try {
      const draft = await reextractSop(resolveV3ApiKey(), selectedSop.id)
      antMessage.success('已重新提炼并生成新的 SOP 草稿')
      setSopTab('draft')
      await refreshSops('draft')
      setSelectedSopId(draft.id)
      setSelectedSop(draft)
      setSopEditor(toSopEditor(draft))
      await refreshSessionHistory()
    } catch (error) {
      antMessage.error((error as Error).message || '重新提炼 SOP 失败')
    } finally {
      setSopSaving(false)
    }
  }

  async function handleDeleteSop() {
    if (!selectedSop) return
    const confirmed = window.confirm(`确定删除 SOP「${selectedSop.name}」吗？此操作不可恢复。`)
    if (!confirmed) return
    setSopSaving(true)
    try {
      await deleteSop(resolveV3ApiKey(), selectedSop.id)
      antMessage.success('SOP 已删除')
      await refreshSops()
      await refreshSessionHistory()
    } catch (error) {
      antMessage.error((error as Error).message || '删除 SOP 失败')
    } finally {
      setSopSaving(false)
    }
  }

  function parseV3PermissionsInput(raw: string): string[] {
    return String(raw || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
  }

  function resolveV3ApiKey(): string {
    // Built-in UI reaches /v2/* through the trusted internal channel; user-managed API keys
    // on the "第三方 Key 服务" page are only for external systems and scripts.
    return ''
  }

  async function refreshV3Jobs() {
    setV3JobsLoading(true)
    try {
      await refreshSessionHistory()
    } finally {
      setV3JobsLoading(false)
    }
  }

  async function refreshV3ApiKeys() {
    const key = resolveV3ApiKey()
    setV3ApiKeyLoading(true)
    try {
      const payload = await v2ListApiKeys(key)
      setV3ApiKeys(payload)
    } catch (error) {
      setV3ApiKeys([])
      antMessage.error((error as Error).message || '加载 API Key 列表失败')
    } finally {
      setV3ApiKeyLoading(false)
    }
  }

  async function refreshV3PermissionTemplates() {
    const key = resolveV3ApiKey()
    try {
      const payload = await v2GetPermissionTemplates(key)
      setV3PermissionTemplates(payload.templates || {})
    } catch (error) {
      setV3PermissionTemplates({})
      antMessage.error((error as Error).message || '加载权限模板失败')
    }
  }

  function parseControlHostList(raw: string): string[] {
    return String(raw || '')
      .split(/[\n,; ]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  }

  async function handleCopyText(value: string, successMessage = '已复制') {
    const text = String(value || '').trim()
    if (!text) {
      antMessage.info('无可复制内容')
      return
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        const el = document.createElement('textarea')
        el.value = text
        document.body.appendChild(el)
        el.select()
        document.execCommand('copy')
        document.body.removeChild(el)
      }
      antMessage.success(successMessage)
    } catch {
      antMessage.error('复制失败，请手动复制')
    }
  }

  async function handleV3CreateApiKey() {
    const name = v3ApiKeyName.trim()
    if (!name) {
      antMessage.warning('请输入 API Key 名称')
      return
    }
    const permissions = parseV3PermissionsInput(v3ApiKeyPermissions)
    if (permissions.length === 0) {
      antMessage.warning('请至少配置一个权限标签')
      return
    }
    setV3ApiKeyLoading(true)
    try {
      const created = await v2CreateApiKey({
        name,
        permissions,
      })
      setV3LastCreatedSecret(created.api_key)
      antMessage.success('第三方 API Key 创建成功')
      await refreshV3ApiKeys()
    } catch (error) {
      antMessage.error((error as Error).message || '创建 API Key 失败')
    } finally {
      setV3ApiKeyLoading(false)
    }
  }

  function handleV3ApplyPermissionTemplate(name: string, perms: string[]) {
    setV3SelectedTemplateName(name)
    setV3ApiKeyPermissions(perms.join(','))
    if (!v3ApiKeyName.trim() || v3ApiKeyName.trim() === 'ops-admin') {
      setV3ApiKeyName(name)
    }
    antMessage.success(`已应用权限模板：${name}`)
  }

  async function handleV3DeleteApiKey(keyId: string) {
    const key = resolveV3ApiKey()
    if (!window.confirm('确认删除该 API Key 吗？')) return
    setV3ApiKeyLoading(true)
    try {
      await v2DeleteApiKey(key, keyId)
      await refreshV3ApiKeys()
      antMessage.success('API Key 已删除')
    } catch (error) {
      antMessage.error((error as Error).message || '删除 API Key 失败')
    } finally {
      setV3ApiKeyLoading(false)
    }
  }

  async function handleV3ToggleApiKey(keyId: string, enabled: boolean) {
    const key = resolveV3ApiKey()
    setV3ApiKeyLoading(true)
    try {
      await v2UpdateApiKey(key, keyId, {
        enabled,
        disabled_reason: enabled ? undefined : 'manually_disabled',
      })
      await refreshV3ApiKeys()
    } catch (error) {
      antMessage.error((error as Error).message || '更新 API Key 状态失败')
    } finally {
      setV3ApiKeyLoading(false)
    }
  }

  async function handleV3RotateApiKey(item: V2ApiKey) {
    const key = resolveV3ApiKey()
    if (!window.confirm(`确认轮换 Key「${item.name}」吗？旧 Key 将立即失效。`)) return
    setV3ApiKeyLoading(true)
    try {
      const rotated = await v2RotateApiKey(key, item.id, {
        name: item.name,
        permissions: item.permissions,
      })
      setV3LastCreatedSecret(rotated.api_key)
      await refreshV3ApiKeys()
      antMessage.success('Key 已轮换，新的完整 Key 已显示在上方，请立即复制')
    } catch (error) {
      antMessage.error((error as Error).message || '轮换 API Key 失败')
    } finally {
      setV3ApiKeyLoading(false)
    }
  }

  async function hydrateSessionById(sessionId: string, silent = false) {
    try {
      const historyItem = sessionHistory.find((item) => item.id === sessionId)
      const data = await buildTimelineSnapshotFromRun(historyItem?.run_id || toUnifiedSingleRunId(sessionId), sessionId)
      historySnapshotCacheRef.current[sessionId] = data
      const restoredSession: SessionResponse = {
        id: data.session.id,
        automation_level: data.session.automation_level,
        operation_mode: data.session.operation_mode,
        status: data.session.status,
        created_at: data.session.created_at,
      }
      setSession(restoredSession)
      setSessionRuntimeKind(historyItem?.kind === 'multi' ? 'multi' : 'single')
      setMultiSessionConfig(null)
      setMultiSessionActiveJobId(historyItem?.kind === 'multi' ? historyItem.source_id : undefined)
      setAutomationLevel(restoredSession.automation_level)
      setOperationMode(restoredSession.operation_mode)
      setMessages(data.messages)
      setCommands(data.commands)
      setEvidences(data.evidences)
      setSummary(data.summary)
      setContinueExecutionState(null)
      setSessionDeviceAddress(data.session.device?.host || '')
      setSessionDeviceName(formatDeviceName(data.session.device?.name))
      setSessionVersionSignature(String(data.session.device?.version_signature || '').trim())
      setSelectedHistorySessionId(sessionId)
      setSelectedHistorySnapshot(data)
      await refreshServiceTraceForTarget(sessionId)
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
    jump_host?: string
    jump_port?: number
    jump_username?: string
    jump_password?: string
    api_token?: string
    automation_level: AutomationLevel
  }) {
    try {
      const hosts = parseControlHostList(payload.host)
      const normalizedHosts = hosts.length > 0 ? hosts : [String(payload.host || '').trim()].filter(Boolean)
      if (normalizedHosts.length > 1) {
        for (const host of normalizedHosts) {
          cacheDeviceAuth(host, {
            username: payload.username,
            password: payload.password,
            jump_host: payload.jump_host,
            jump_port: payload.jump_port,
            jump_username: payload.jump_username,
            jump_password: payload.jump_password,
            api_token: payload.api_token,
          })
        }
        const virtualSessionId = `multi-${Date.now()}`
        const virtualSession: SessionResponse = {
          id: virtualSessionId,
          automation_level: payload.automation_level,
          operation_mode: payload.operation_mode,
          status: 'open',
          created_at: new Date().toISOString(),
        }
        setSession(virtualSession)
        setSessionRuntimeKind('multi')
        setMultiSessionConfig({
          hosts: normalizedHosts,
          protocol: payload.protocol,
          operation_mode: payload.operation_mode,
          username: payload.username,
          password: payload.password,
          jump_host: payload.jump_host,
          jump_port: payload.jump_port,
          jump_username: payload.jump_username,
          jump_password: payload.jump_password,
          api_token: payload.api_token,
        })
        setMultiSessionActiveJobId(undefined)
        setAutomationLevel(virtualSession.automation_level)
        setOperationMode(virtualSession.operation_mode)
        setMessages([])
        setCommands([])
        setEvidences([])
        setSummary(undefined)
        setContinueExecutionState(null)
        setTraceSteps([])
        setSessionDeviceAddress(normalizedHosts.join(', '))
        setSessionDeviceName(`多设备协同(${normalizedHosts.length})`)
        setSessionVersionSignature('')
        setDraftInput('')
        antMessage.success(`多设备协同会话已创建 (${normalizedHosts.length} 台)`)
        setActivePage('workbench')
        return
      }

      const host = normalizedHosts[0] || String(payload.host || '').trim()
      cacheDeviceAuth(host, {
        username: payload.username,
        password: payload.password,
        jump_host: payload.jump_host,
        jump_port: payload.jump_port,
        jump_username: payload.jump_username,
        jump_password: payload.jump_password,
        api_token: payload.api_token,
      })
      const run = await createRun(resolveV3ApiKey(), {
        automation_level: payload.automation_level,
        operation_mode: payload.operation_mode,
        devices: [
          {
            host,
            protocol: payload.protocol,
            username: payload.username,
            password: payload.password,
            jump_host: payload.jump_host,
            jump_port: payload.jump_port,
            jump_username: payload.jump_username,
            jump_password: payload.jump_password,
            api_token: payload.api_token,
          },
        ],
      })
      const resp: SessionResponse = {
        id: run.source_id,
        automation_level: run.automation_level,
        operation_mode: run.operation_mode,
        status: run.status,
        created_at: run.created_at,
      }
      setSession(resp)
      setSessionRuntimeKind('single')
      setMultiSessionConfig(null)
      setMultiSessionActiveJobId(undefined)
      setAutomationLevel(resp.automation_level)
      setOperationMode(resp.operation_mode)
      setMessages([])
      setCommands([])
      setEvidences([])
      setSummary(undefined)
      setContinueExecutionState(null)
      setTraceSteps([])
      setSessionDeviceAddress(host)
      setSessionDeviceName('-')
      setSessionVersionSignature('')
      setDraftInput('')
      await refreshSessionHistory()
      antMessage.success(`会话已创建: ${resp.id}`)
      setActivePage('workbench')
    } catch (error) {
      antMessage.error((error as Error).message || '创建会话失败，请检查后端服务状态')
    }
  }

  async function handleSaveApiKey() {
    const primaryKey = apiKeyInput.trim()
    const nvidiaKey = nvidiaApiKeyInput.trim()
    if (!primaryKey && !nvidiaKey) {
      antMessage.warning('请至少输入一个 API Key')
      return
    }

    setLlmSaving(true)
    try {
      const status = await configureLlm({
        apiKey: primaryKey || undefined,
        nvidiaApiKey: nvidiaKey || undefined,
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
      setNvidiaApiKeyInput('')
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
      setNvidiaApiKeyInput('')
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

  function beginEditCapability(rule: CommandCapabilityRule) {
    setEditingCapabilityId(rule.id)
    setCapabilityVersionFilter(rule.version_signature || capabilityVersionFilter)
    setCapabilityVersionInput(rule.version_signature || '')
    setCapabilityCommandInput(rule.command_key)
    setCapabilityActionInput(rule.action)
    setCapabilityRewriteInput(rule.rewrite_to || '')
    setCapabilityReasonInput(rule.reason_text || '')
  }

  function resetCapabilityEditor() {
    setEditingCapabilityId(undefined)
    setCapabilityVersionInput('')
    setCapabilityCommandInput('')
    setCapabilityActionInput('rewrite')
    setCapabilityRewriteInput('')
    setCapabilityReasonInput('')
  }

  async function handleSaveCapabilityRule() {
    const versionSignature = capabilityVersionInput.trim() || capabilityEffectiveVersion.trim()
    const commandKey = capabilityCommandInput.trim()
    if (!versionSignature) {
      antMessage.warning('请先输入版本指纹')
      return
    }
    if (!commandKey) {
      antMessage.warning('请输入命令')
      return
    }
    if (capabilityActionInput === 'rewrite' && !capabilityRewriteInput.trim()) {
      antMessage.warning('rewrite 模式必须提供替代命令')
      return
    }

    setCapabilitySaving(true)
    try {
      await upsertCommandCapability({
        id: editingCapabilityId,
        scope_type: 'version',
        version_signature: versionSignature,
        protocol: 'ssh',
        command_key: commandKey,
        action: capabilityActionInput,
        rewrite_to: capabilityActionInput === 'rewrite' ? capabilityRewriteInput.trim() : undefined,
        reason_text: capabilityReasonInput.trim() || undefined,
        source: 'manual',
        enabled: true,
      })
      await refreshCommandCapabilityRules(capabilityVersionFilter.trim() || undefined)
      resetCapabilityEditor()
      antMessage.success('学习规则已保存')
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setCapabilitySaving(false)
    }
  }

  async function handleToggleCapabilityEnabled(rule: CommandCapabilityRule, enabled: boolean) {
    setCapabilitySaving(true)
    try {
      await upsertCommandCapability({
        id: rule.id,
        scope_type: rule.scope_type,
        host: rule.host,
        protocol: rule.protocol,
        command_key: rule.command_key,
        action: rule.action,
        version_signature: rule.version_signature,
        rewrite_to: rule.rewrite_to,
        reason_code: rule.reason_code,
        reason_text: rule.reason_text,
        source: rule.source,
        enabled,
      })
      await refreshCommandCapabilityRules(capabilityVersionFilter.trim() || undefined)
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setCapabilitySaving(false)
    }
  }

  async function handleDeleteCapabilityRule(ruleId: string) {
    setCapabilitySaving(true)
    try {
      await deleteCommandCapability(ruleId)
      await refreshCommandCapabilityRules(capabilityVersionFilter.trim() || undefined)
      if (editingCapabilityId === ruleId) {
        resetCapabilityEditor()
      }
      antMessage.success('学习规则已删除')
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setCapabilitySaving(false)
    }
  }

  async function handleResetCapabilityRules() {
    const versionSignature = capabilityVersionFilter.trim()
    setCapabilitySaving(true)
    try {
      const result = await resetCommandCapability({ version_signature: versionSignature || undefined })
      await refreshCommandCapabilityRules(versionSignature || undefined)
      resetCapabilityEditor()
      antMessage.success(`已清空规则 ${result.removed} 条，剩余 ${result.remaining} 条`)
    } catch (error) {
      antMessage.error((error as Error).message)
    } finally {
      setCapabilitySaving(false)
    }
  }

  function toV2JobMode(mode: OperationMode): 'diagnosis' | 'inspection' | 'repair' {
    if (mode === 'query') return 'inspection'
    if (mode === 'config') return 'repair'
    return 'diagnosis'
  }

  function buildMultiProblemStatement(userContent: string): string {
    const recent = messages.slice(-8).map((item) => `${item.role === 'user' ? '用户' : item.role === 'assistant' ? 'AI' : '系统'}: ${item.content}`)
    const summaryHint = summary ? `上轮结论: ${renderSummaryBrief(summary)}` : ''
    const contextBlock = [summaryHint, ...recent].filter(Boolean).join('\n')
    if (!contextBlock) return userContent
    return `当前用户请求: ${userContent}\n\n会话上下文:\n${contextBlock}`
  }

  function buildMultiSessionDevices(config: MultiSessionConfig): Array<Record<string, unknown>> {
    return config.hosts.map((host) => ({
      host,
      protocol: config.protocol,
      username: config.username,
      password: config.password,
      jump_host: config.jump_host,
      jump_port: config.jump_port,
      jump_username: config.jump_username,
      jump_password: config.jump_password,
      api_token: config.api_token,
    }))
  }

  async function monitorMultiJobUntilPauseOrDone(jobId: string): Promise<'pending' | 'done' | 'stopped'> {
    const key = resolveV3ApiKey()
    const runId = toUnifiedMultiRunId(jobId)
    let rounds = 0
    while (rounds < 240) {
      if (multiJobAbortRef.current?.aborted) {
        return 'stopped'
      }
      const runTimeline = await getRunTimeline(key, runId)
      const timeline = runTimeline.timeline
      setV3SelectedJobId(jobId)
      setMessages(timeline.messages || [])
      setCommands(timeline.commands || [])
      setEvidences(timeline.evidences || [])
      setTraceSteps(runTimeline.service_trace?.steps || [])
      if ((timeline.commands || []).length > 0) {
        const lastCommand = timeline.commands[timeline.commands.length - 1]
        setSelectedCommandId(lastCommand.id)
        setSelectedActivityKey(`cmd:${lastCommand.id}`)
      }

      if ((runTimeline.run.pending_actions || 0) > 0) {
        const pendingCount = runTimeline.run.pending_actions || 0
        const pendingSummary = timeline.summary || summary
        if (pendingSummary) {
          setSummary(pendingSummary)
        }
        setMessages((prev) => {
          const markerId = `multi-pending-${jobId}`
          if (prev.some((item) => item.id === markerId)) return prev
          return [
            ...prev,
            {
              id: markerId,
              role: 'assistant',
              content: `已生成 ${pendingCount} 组待确认命令，请在卡片中确认后继续执行。`,
              created_at: new Date().toISOString(),
            },
          ]
        })
        setSelectedActivityKey('summary:latest')
        return 'pending'
      }

      if (runTimeline.run.status === 'completed' || runTimeline.run.status === 'failed' || runTimeline.run.status === 'cancelled') {
        const finalSummary = timeline.summary || summary
        if (!finalSummary) {
          return 'done'
        }
        setSummary(finalSummary)
        setMessages((prev) => {
          const markerId = `multi-final-${jobId}`
          if (prev.some((item) => item.id === markerId)) return prev
          return [
            ...prev,
            {
              id: markerId,
              role: 'assistant',
              content: renderSummaryBrief(finalSummary),
              created_at: new Date().toISOString(),
            },
          ]
        })
        setSelectedActivityKey('summary:latest')
        return 'done'
      }
      rounds += 1
      await sleepMs(1200)
    }
    return 'stopped'
  }

  async function handleSendMulti(content: string) {
    if (!session?.id || !multiSessionConfig) {
      antMessage.warning('请先创建多设备协同会话')
      return
    }
    const userMessage: ChatMessage = {
      id: `local-user-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, userMessage])
    setSelectedActivityKey(`msg:${userMessage.id}`)
    setBusy(true)
    setContinueExecutionState(null)
    try {
      const key = resolveV3ApiKey()
      const requestId = `workbench-multi-${Date.now()}`
      const created = await createRun(
        key,
        {
          name: requestId,
          problem: buildMultiProblemStatement(content),
          automation_level: automationLevel,
          operation_mode: multiSessionConfig.operation_mode,
          topology_mode: 'hybrid',
          max_gap_seconds: 300,
          max_device_concurrency: Math.min(50, Math.max(2, multiSessionConfig.hosts.length)),
          execution_policy: 'stop_on_failure',
          devices: buildMultiSessionDevices(multiSessionConfig),
        },
        requestId,
      )
      setMultiSessionActiveJobId(created.source_id)
      setV3SelectedJobId(created.source_id)
      multiJobAbortRef.current = { aborted: false, jobId: created.source_id }
      const state = await monitorMultiJobUntilPauseOrDone(created.source_id)
      if (state === 'done' || state === 'stopped') {
        setMultiSessionActiveJobId(undefined)
      }
      await refreshV3Jobs()
      await refreshSessionHistory()
    } catch (error) {
      antMessage.error((error as Error).message || '多设备协同执行失败')
    } finally {
      setBusy(false)
      multiJobAbortRef.current = null
    }
  }

  async function handleSend(content: string, options?: SendOptions) {
    if (!session?.id) {
      antMessage.warning('请先在连接控制创建会话')
      return
    }

    if (sessionRuntimeKind === 'multi') {
      await handleSendMulti(content)
      return
    }

    const continueBaselineStepNo = options?.continueExecution?.baselineStepNo
    if (continueBaselineStepNo === undefined) {
      setContinueExecutionState(null)
    }

    const activeSessionId = session.id
    setResumedSessionId(activeSessionId)
    const abortController = new AbortController()
    streamAbortRef.current = abortController
    setBusy(true)
    try {
      await streamRunMessage(resolveV3ApiKey(), toUnifiedSingleRunId(activeSessionId), content, (event, payload) => {
        if (event === 'message_ack' && payload.message) {
          const msg = payload.message as ChatMessage
          setMessages((prev) => [...prev, msg])
          setSelectedActivityKey(`msg:${msg.id}`)
        }

        if (event === 'command_completed' && payload.command) {
          const cmd = payload.command as CommandExecution
          setCommands((prev) => upsertCommand(prev, cmd))
          setSelectedActivityKey(`cmd:${cmd.id}`)
          if (typeof continueBaselineStepNo === 'number' && cmd.step_no > continueBaselineStepNo) {
            setContinueExecutionState((prev) => upsertContinueExecutionState(prev, cmd, continueBaselineStepNo))
          }
        }

        if (event === 'command_blocked' && payload.command) {
          const cmd = payload.command as CommandExecution
          setCommands((prev) => upsertCommand(prev, cmd))
          setSelectedActivityKey(`cmd:${cmd.id}`)
          if (typeof continueBaselineStepNo === 'number' && cmd.step_no > continueBaselineStepNo) {
            setContinueExecutionState((prev) => upsertContinueExecutionState(prev, cmd, continueBaselineStepNo))
          }
        }

        if (event === 'command_pending_confirmation' && payload.command) {
          const command = payload.command as CommandExecution
          setCommands((prev) => upsertCommand(prev, command))
          setSelectedActivityKey(`cmd:${command.id}`)
          if (typeof continueBaselineStepNo === 'number' && command.step_no > continueBaselineStepNo) {
            setContinueExecutionState((prev) => upsertContinueExecutionState(prev, command, continueBaselineStepNo))
          }
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
      if (typeof continueBaselineStepNo === 'number') {
        setContinueExecutionState((prev) => (prev ? { ...prev, active: false } : prev))
      }
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
    if (sessionRuntimeKind === 'multi') {
      setStoppingSession(true)
      const activeJobId = multiSessionActiveJobId
      try {
        if (multiJobAbortRef.current) {
          multiJobAbortRef.current.aborted = true
        }
        setMultiSessionActiveJobId(undefined)
        setBusy(false)
        setMessages((prev) => [
          ...prev,
          {
            id: `multi-stop-${Date.now()}`,
            role: 'system',
            content: '多设备协同已手动停止。',
            created_at: new Date().toISOString(),
          },
        ])
        if (activeJobId) {
          await stopRun(resolveV3ApiKey(), toUnifiedMultiRunId(activeJobId))
        }
        antMessage.success('当前多设备协同已停止')
      } catch (error) {
        antMessage.warning((error as Error).message || '停止请求已发出，请稍后刷新确认状态')
      } finally {
        setStoppingSession(false)
      }
      return
    }
    setStoppingSession(true)
    try {
      streamAbortRef.current?.abort()
      setBusy(false)
      setResumedSessionId(undefined)
      setContinueExecutionState(null)
      await stopRun(resolveV3ApiKey(), toUnifiedSingleRunId(session.id))
      await Promise.all([refreshTimeline(session.id), refreshServiceTrace(session.id)])
      setBusy(false)
      antMessage.success('当前会话已停止')
    } catch (error) {
      antMessage.warning((error as Error).message || '停止请求已发出，请稍后刷新确认状态')
    } finally {
      setStoppingSession(false)
    }
  }

  async function handleRestoreSession(sessionId: string, hostHint?: string) {
    const v2JobId = parseV2HistoryJobId(sessionId)
    if (v2JobId) {
      await handleRestoreV2HistoryJob(v2JobId)
      return
    }
    const ok = await hydrateSessionById(sessionId, true)
    if (ok) {
      const targetHost = (hostHint || sessionDeviceAddress || '').trim()
      const cached = targetHost ? loadDeviceAuth(targetHost) : undefined
      if (
        cached
        && (cached.username
          || cached.password
          || cached.jump_host
          || cached.jump_username
          || cached.jump_password
          || cached.api_token)
      ) {
        try {
          await updateRunCredentials(resolveV3ApiKey(), toUnifiedSingleRunId(sessionId), {
            username: cached.username,
            password: cached.password,
            jump_host: cached.jump_host,
            jump_port: cached.jump_port,
            jump_username: cached.jump_username,
            jump_password: cached.jump_password,
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

  async function handleRestoreV2HistoryJob(jobId: string) {
    try {
      const timeline = await buildTimelineSnapshotFromRun(toUnifiedMultiRunId(jobId), toV2HistorySessionId(jobId))
      const hosts = splitHostsFromSnapshot(timeline)
      const operationMode = timeline.session.operation_mode
      const historySessionId = timeline.session.id
      setV3SelectedJobId(jobId)
      setSession({
        id: historySessionId,
        automation_level: timeline.session.automation_level,
        operation_mode: operationMode,
        status: timeline.session.status,
        created_at: timeline.session.created_at,
      })
      setSessionRuntimeKind('multi')
      setMultiSessionConfig({
        hosts,
        protocol: 'ssh',
        operation_mode: operationMode,
      })
      setMultiSessionActiveJobId(jobId)
      setAutomationLevel(timeline.session.automation_level)
      setOperationMode(operationMode)
      setMessages(timeline.messages)
      setCommands(timeline.commands)
      setEvidences(timeline.evidences)
      setSummary(timeline.summary)
      setContinueExecutionState(null)
      setSessionDeviceAddress(timeline.session.device?.host || hosts.join(', ') || '-')
      setSessionDeviceName(formatDeviceName(timeline.session.device?.name))
      setSessionVersionSignature(String(timeline.session.device?.version_signature || '').trim())
      setDraftInput('')
      setResumedSessionId(historySessionId)
      setActivePage('workbench')
      historySnapshotCacheRef.current[historySessionId] = timeline
      antMessage.success('已恢复多设备历史任务')
    } catch (error) {
      antMessage.error((error as Error).message || '恢复多设备历史任务失败')
    }
  }

  function handleSelectHistorySession(sessionId: string) {
    if (!sessionId) return
    if (selectedHistorySessionId === sessionId) {
      void loadHistorySnapshot(sessionId, true)
      return
    }
    setSelectedHistorySessionId(sessionId)
  }

  async function loadHistorySnapshot(sessionId: string, forceRefresh: boolean) {
    const target = String(sessionId || '').trim()
    if (!target) return
    const historyItem = sessionHistory.find((item) => item.id === target)
    const runId = historyItem?.run_id
    const v2JobId = parseV2HistoryJobId(target)
    const cached = historySnapshotCacheRef.current[target]
    const cachedTrace = historySnapshotTraceCacheRef.current[target]
    if (cached && !forceRefresh) {
      setSelectedHistorySnapshot(cached)
      setSelectedHistorySnapshotTrace(cachedTrace || [])
      setHistorySnapshotLoading(false)
      return
    }
    const requestId = historySnapshotRequestRef.current + 1
    historySnapshotRequestRef.current = requestId
    setHistorySnapshotLoading(true)
    if (!cached || forceRefresh) {
      setSelectedHistorySnapshot((prev) => (prev && prev.session.id === target ? prev : null))
      setSelectedHistorySnapshotTrace([])
    }
    try {
      const resolvedRunId = runId || (v2JobId ? toUnifiedMultiRunId(v2JobId) : toUnifiedSingleRunId(target))
      const [data, traceData] = await Promise.all([
        buildTimelineSnapshotFromRun(resolvedRunId, target),
        getRunTrace(resolveV3ApiKey(), resolvedRunId),
      ])
      if (historySnapshotRequestRef.current !== requestId) return
      historySnapshotCacheRef.current[target] = data
      historySnapshotTraceCacheRef.current[target] = (traceData.steps || []).map((item) => ({ ...item, session_id: target }))
      setSelectedHistorySnapshot(data)
      setSelectedHistorySnapshotTrace(historySnapshotTraceCacheRef.current[target])
    } catch {
      if (historySnapshotRequestRef.current !== requestId) return
      setSelectedHistorySnapshot(null)
      setSelectedHistorySnapshotTrace([])
    } finally {
      if (historySnapshotRequestRef.current !== requestId) return
      setHistorySnapshotLoading(false)
    }
  }

  async function buildTimelineSnapshotFromRun(runId: string, historySessionId?: string): Promise<Timeline> {
    const runTimeline = await getRunTimeline(resolveV3ApiKey(), runId)
    const timeline = runTimeline.timeline
    if (!historySessionId || timeline.session.id === historySessionId) {
      return timeline
    }
    return {
      ...timeline,
      session: {
        ...timeline.session,
        id: historySessionId,
      },
      messages: timeline.messages.map((item) => ({ ...item })),
      commands: timeline.commands.map((item) => ({ ...item, session_id: historySessionId })),
      evidences: timeline.evidences.map((item) => ({ ...item, session_id: historySessionId })),
      summary: timeline.summary,
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
    const runId = resolveUnifiedRunId({
      targetId: sid,
      sessionHistory,
      activeSessionId: session?.id,
      sessionRuntimeKind,
      multiSessionActiveJobId,
    })
    if (!runId) return
    const data = (await getRunTimeline(resolveV3ApiKey(), runId)).timeline
    setSession({
      id: data.session.id,
      automation_level: data.session.automation_level,
      operation_mode: data.session.operation_mode,
      status: data.session.status,
      created_at: data.session.created_at,
    })
    setOperationMode(data.session.operation_mode)
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
    const runId = resolveUnifiedRunId({
      targetId: sid,
      sessionHistory,
      activeSessionId: session?.id,
      sessionRuntimeKind,
      multiSessionActiveJobId,
    })
    if (!runId) return
    setTraceLoading(true)
    try {
      const data = await getRunTrace(resolveV3ApiKey(), runId)
      setTraceSteps(data.steps || [])
    } finally {
      setTraceLoading(false)
    }
  }

  async function refreshServiceTraceForTarget(targetId?: string, silent = true) {
    const sid = String(targetId || '').trim()
    if (!sid) {
      setTraceSteps([])
      return
    }
    setTraceLoading(true)
    try {
      const runId = resolveUnifiedRunId({
        targetId: sid,
        sessionHistory,
        activeSessionId: session?.id,
        sessionRuntimeKind,
        multiSessionActiveJobId,
      })
      if (!runId) {
        setTraceSteps([])
        return
      }
      const data = await getRunTrace(resolveV3ApiKey(), runId)
      setTraceSteps((data.steps || []).map((item) => ({ ...item, session_id: sid })))
    } catch (error) {
      setTraceSteps([])
      if (!silent) {
        antMessage.error((error as Error).message || '加载流程追踪失败')
      }
    } finally {
      setTraceLoading(false)
    }
  }

  async function handleExport() {
    if (!session?.id) return
    const runId = resolveUnifiedRunId({
      targetId: session.id,
      sessionHistory,
      activeSessionId: session.id,
      sessionRuntimeKind,
      multiSessionActiveJobId,
    })
    if (!runId) return
    const content = await exportRunMarkdown(resolveV3ApiKey(), runId)
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
    const v2ActionGroupId = parseV2ActionGroupCommandId(commandId)
    if (v2ActionGroupId && multiSessionActiveJobId) {
      setConfirmingCommandId(commandId)
      setBusy(true)
      try {
        const key = resolveV3ApiKey()
        multiJobAbortRef.current = { aborted: false, jobId: multiSessionActiveJobId }
        const actionIds = (pendingConfirmMeta?.commands || [])
          .map((item) => parseV2ActionGroupCommandId(item.id))
          .filter((item): item is string => Boolean(item))
        const targetIds = actionIds.length > 0 ? actionIds : [v2ActionGroupId]
        if (approved) {
          await approveRunActions(key, toUnifiedMultiRunId(multiSessionActiveJobId), targetIds, 'workbench-confirm')
          const state = await monitorMultiJobUntilPauseOrDone(multiSessionActiveJobId)
          if (state === 'done' || state === 'stopped') {
            setMultiSessionActiveJobId(undefined)
          }
          antMessage.success(targetIds.length > 1 ? '已确认并执行命令组' : '已确认执行命令组')
        } else {
          await rejectRunActions(key, toUnifiedMultiRunId(multiSessionActiveJobId), targetIds, 'workbench-reject')
          const state = await monitorMultiJobUntilPauseOrDone(multiSessionActiveJobId)
          if (state === 'done' || state === 'stopped') {
            setMultiSessionActiveJobId(undefined)
          }
          antMessage.info(targetIds.length > 1 ? '已拒绝命令组' : '已拒绝命令组')
        }
      } catch (error) {
        antMessage.error((error as Error).message || '命令组确认失败')
      } finally {
        multiJobAbortRef.current = null
        setConfirmingCommandId(undefined)
        setBusy(false)
      }
      return
    }
    setConfirmingCommandId(commandId)
    try {
      if (approved) {
        await approveRunActions(resolveV3ApiKey(), toUnifiedSingleRunId(session.id), [commandId], 'workbench-confirm')
      } else {
        await rejectRunActions(resolveV3ApiKey(), toUnifiedSingleRunId(session.id), [commandId], 'workbench-reject')
      }
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
    const baselineStepNo = getMaxStepNo(commands)
    const plannedCommands = continuePreview.items
    setContinueExecutionState({
      active: true,
      baselineStepNo,
      plannedCommands,
      observedCommands: [],
    })
    await handleSend(
      '请继续执行下一步，不要结束。基于当前会话和当前任务模式继续，必要时输出待执行命令组。',
      { continueExecution: { baselineStepNo } },
    )
  }

  async function handleContinuePrimaryAction() {
    if (continuePreview.source === 'pending' && pendingConfirmMeta?.commandId) {
      await handleConfirmCommandInline(pendingConfirmMeta.commandId, true)
      return
    }
    await handleContinueFromCard()
  }

  function shouldShowContinueAction(summaryData: DiagnosisSummary): boolean {
    if (!summaryData) return false
    if (latestAiDecision === 'final') return false
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

  function jumpToTraceStep(stepId: string) {
    if (!stepId) return
    setSelectedTraceStepId(stepId)
    window.requestAnimationFrame(() => {
      const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
        ? CSS.escape(stepId)
        : stepId.replace(/"/g, '\\"')
      const flowNode = document.querySelector(`.flow-node[data-trace-step-id="${escaped}"]`) as HTMLElement | null
      const listNode = document.querySelector(`.trace-row-item[data-trace-step-id="${escaped}"]`) as HTMLElement | null
      const target = flowNode || listNode
      if (!target) return
      target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
    })
  }

  function handleToggleTracePlayback() {
    if (tracePlaybackActive) {
      setTracePlaybackActive(false)
      return
    }
    if (sortedTraceSteps.length <= 1) {
      antMessage.info('步骤不足，无法播放')
      return
    }
    if (selectedTraceIndex < 0 || selectedTraceIndex >= sortedTraceSteps.length - 1) {
      jumpToTraceStep(sortedTraceSteps[0].id)
    }
    setTracePlaybackActive(true)
  }

  return (
    <div className="noc-root">
      <header className="brand-bar">
        <div className="brand-left">
          <img className="brand-logo" src="/infra-logo.png" alt="Infra Logo" />
          <div>
            <h1>NetOps AI V2</h1>
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
          {NAV_SECTIONS.map((section) => (
            <div key={section.key} className="rail-section">
              <div className="rail-section-label">{section.label}</div>
              <div className="rail-section-items">
                {section.items.map((item) => (
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
              </div>
            </div>
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
                          if (item.kind === 'trace') setSelectedTraceStepId(item.trace.id)
                        }}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            setSelectedActivityKey(item.key)
                            if (item.kind === 'command') setSelectedCommandId(item.command.id)
                            if (item.kind === 'trace') setSelectedTraceStepId(item.trace.id)
                          }
                        }}
                      >
                        <div className="activity-meta">
                          <span className={`activity-kind ${item.kind}`}>{item.label}</span>
                          <span className="activity-time">{item.kind === 'trace' ? formatTraceTime(item.createdAt) : formatTime(item.createdAt)}</span>
                        </div>
                        <div className="activity-title">{item.title}</div>
                        {item.kind === 'command' && (
                          <div className="activity-tags">
                            <span className={`cmd-status ${statusClass(item.status)}`}>{item.status}</span>
                            <span className="risk-tag">{item.riskLevel}</span>
                            {commandConstraintLabel(item.command) && (
                              <span className={`constraint-tag ${constraintTagClass(item.command)}`}>
                                {commandConstraintLabel(item.command)}
                              </span>
                            )}
                          </div>
                        )}
                        {item.kind === 'trace' && (
                          <div className="activity-tags">
                            <span className={`cmd-status ${traceStatusClass(item.trace.status)}`}>{item.trace.status}</span>
                            <span className="risk-tag">{traceTypeLabel(item.trace.step_type)}</span>
                          </div>
                        )}
                        <div
                          className={`activity-preview ${item.kind === 'command' ? 'command-preview' : ''}`}
                          title={item.kind === 'command' || item.kind === 'trace' ? item.preview : undefined}
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
                            <div className="summary-plan-strip">
                              <div className="summary-plan-head">
                                <span>继续执行前命令序列</span>
                                <span className="summary-plan-source">
                                  {continuePreview.source === 'pending'
                                    ? '来源: 待确认命令'
                                    : '来源: 待继续后生成'}
                                </span>
                              </div>
                              {continuePreview.items.length > 0 ? (
                                <div className="summary-plan-list">
                                  {continuePreview.items.map((entry) => (
                                    <div key={entry.key} className="summary-plan-item">
                                      <div className="summary-plan-item-meta">
                                        <span>{`#${entry.step_no}`}</span>
                                        <span>{truncateText(entry.title, 72)}</span>
                                        <span>{riskLabel(entry.risk_level)}</span>
                                        <span>{entry.status}</span>
                                      </div>
                                      <div className="summary-plan-item-cmds">
                                        {entry.commandLines.map((commandText, index) => (
                                          <code key={`${entry.key}-${index}`}>{commandText}</code>
                                        ))}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <div className="summary-plan-empty">当前未收到可直接执行命令，点击后将由 AI 生成下一步命令。</div>
                              )}
                            </div>
                            <div className="summary-action-buttons">
                              <Button
                                size="small"
                                type="primary"
                                loading={
                                  continuePreview.source === 'pending' && pendingConfirmMeta?.commandId
                                    ? confirmingCommandId === pendingConfirmMeta.commandId
                                    : Boolean(continueExecutionState?.active)
                                }
                                disabled={!sessionReady || busy}
                                onClick={() => void handleContinuePrimaryAction()}
                              >
                                {continuePreview.source === 'pending'
                                  ? ((pendingConfirmMeta?.isBatch ?? false) ? '确认命令组并继续' : '确认执行并继续')
                                  : (continueExecutionState?.active ? '继续执行中...' : '继续执行')}
                              </Button>
                              <Button
                                size="small"
                                disabled={busy}
                                onClick={() => antMessage.info('已保持当前结论')}
                              >
                                暂不继续
                              </Button>
                            </div>
                            {continueExecutionState && (
                              <div className={`summary-progress-strip ${continueExecutionState.active ? 'running' : ''}`}>
                                <div className="summary-progress-head">
                                  <span>
                                    {continueExecutionState.active ? '正在继续执行，命令流如下：' : '本轮继续执行命令流：'}
                                  </span>
                                  <span className="summary-progress-count">
                                    {continueExecutionState.observedCommands.length} 条
                                  </span>
                                </div>
                                {continueExecutionState.observedCommands.length > 0 ? (
                                  <div className="summary-progress-list">
                                    {continueExecutionState.observedCommands.map((entry) => (
                                      <div key={entry.id} className="summary-progress-item">
                                        <span className={`cmd-status ${statusClass(entry.status)}`}>{entry.status}</span>
                                        <div className="summary-progress-item-main">
                                          <div className="summary-progress-item-meta">
                                            <span>{`#${entry.step_no}`}</span>
                                            <span>{truncateText(entry.title, 72)}</span>
                                            <span>{riskLabel(entry.risk_level)}</span>
                                          </div>
                                          <code>{entry.command}</code>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                ) : continueExecutionState.plannedCommands.length > 0 ? (
                                  <div className="summary-progress-planned">
                                    {continueExecutionState.plannedCommands.map((entry) => (
                                      <div key={entry.key} className="summary-progress-group">
                                        <div className="summary-progress-group-meta">
                                          <span>{`#${entry.step_no}`}</span>
                                          <span>{truncateText(entry.title, 72)}</span>
                                          <span>{riskLabel(entry.risk_level)}</span>
                                          <span>{entry.status}</span>
                                        </div>
                                        <div className="summary-progress-group-cmds">
                                          {entry.commandLines.map((commandText, index) => (
                                            <code key={`${entry.key}-planned-${index}`}>{commandText}</code>
                                          ))}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <div className="summary-progress-empty">等待 AI 返回命令并开始执行...</div>
                                )}
                              </div>
                            )}
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
                        <div className="composer-inline-toggle">
                          <span className="composer-inline-label">精简模式</span>
                          <Switch
                            size="small"
                            checked={activityViewMode === 'compact'}
                            onChange={(checked) => setActivityViewMode(checked ? 'compact' : 'full')}
                          />
                        </div>
                      </div>
                      <div className="composer-actions">
                        <Button
                          danger
                          onClick={() => void handleStopCurrentSession()}
                          disabled={
                            !sessionReady
                            || sessionStopped
                            || (
                              !busy
                              && !stoppingSession
                              && !(sessionRuntimeKind === 'multi' && !!multiSessionActiveJobId)
                            )
                          }
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
                    {selectedActivity?.kind === 'command' && selectedCommandRow ? (
                      <div className="detail-command-panel">
                        <div className="detail-meta-block">
                          <div className="detail-meta-row"><span>标题</span><strong>{selectedCommandRow.title}</strong></div>
                          <div className="detail-meta-row"><span>步骤</span><strong>{selectedCommandRow.stepLabel}</strong></div>
                          <div className="detail-meta-row"><span>状态</span><strong>{selectedCommandRow.status}</strong></div>
                          <div className="detail-meta-row"><span>风险</span><strong>{riskLabel(selectedCommandRow.risk_level)}</strong></div>
                          {summarizeCommandConstraintLabels(selectedCommandRow.members) && (
                            <div className="detail-meta-row">
                              <span>约束</span>
                              <strong>{summarizeCommandConstraintLabels(selectedCommandRow.members)}</strong>
                            </div>
                          )}
                        </div>
                        <div className="detail-seq-title">命令序列（按执行顺序）</div>
                        <div className="detail-seq-list">
                          {[...selectedCommandRow.members]
                            .sort((a, b) => a.step_no - b.step_no)
                            .map((item, index) => (
                              <div key={item.id} className="detail-seq-row">
                                <span className="detail-seq-order">{index + 1}.</span>
                                <span className="detail-seq-step">#{item.step_no}</span>
                                <span className={`cmd-status ${statusClass(item.status)}`}>{item.status}</span>
                                <code className="detail-command-code" title={item.command}>{item.command}</code>
                              </div>
                            ))}
                        </div>
                        <div className="detail-seq-title">输出</div>
                        <pre className="detail-pre">{renderCommandRowOutput(selectedCommandRow)}</pre>
                      </div>
                    ) : (
                      <pre className="detail-pre">{selectedDetailBody}</pre>
                    )}
                  </div>
                </section>

                <section className="right-bottom panel-card">
                  <h3>设备终端回显</h3>
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

          {activePage === 'third_party_keys' && (
            <div className="page-grid keyhub-layout">
              <div className="panel-card v3-card keyhub-main-card">
                <div className="policy-overview-head">
                  <div>
                    <h3>第三方 Key 服务中心</h3>
                    <p className="muted">创建和管理提供给第三方系统的 API Key。完整 Key 仅在创建或轮换后展示一次。</p>
                  </div>
                  <div className="policy-actions">
                    <Button size="small" loading={v3ApiKeyLoading} onClick={() => void refreshV3ApiKeys()}>
                      刷新
                    </Button>
                  </div>
                </div>
                <div className="v3-grid-3 keyhub-create-row">
                  <Input
                    size="small"
                    value={v3ApiKeyName}
                    onChange={(event) => setV3ApiKeyName(event.target.value)}
                    placeholder="新 Key 名称（例如：noc-operator）"
                  />
                  <Input
                    size="small"
                    value={v3ApiKeyPermissions}
                    onChange={(event) => setV3ApiKeyPermissions(event.target.value)}
                    placeholder="权限标签，逗号分隔（如 job.read,job.write）"
                  />
                  <Button
                    size="small"
                    type="primary"
                    loading={v3ApiKeyLoading}
                    onClick={() => void handleV3CreateApiKey()}
                  >
                    创建第三方 Key
                  </Button>
                </div>
                <div className="v3-template-box">
                  <div className="keyhub-section-head">
                    <strong>最小权限模板</strong>
                    <span className="muted">用于快速填写常见第三方接入权限组合。</span>
                  </div>
                  {Object.keys(v3PermissionTemplates).length === 0 ? (
                    <div className="muted">-</div>
                  ) : (
                    <div className="v3-template-list">
                      {Object.entries(v3PermissionTemplates).map(([name, perms]) => (
                        <button
                          key={name}
                          type="button"
                          className={`v3-template-item keyhub-template-item ${v3SelectedTemplateName === name ? 'active' : ''}`}
                          onClick={() => handleV3ApplyPermissionTemplate(name, perms)}
                        >
                          <strong>{name}</strong>
                          <code>{perms.join(', ')}</code>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {v3LastCreatedSecret && (
                  <div className="v3-secret-box keyhub-secret-box">
                    <span className="muted">最新创建或轮换后的完整 Key（仅展示一次，请立即复制给第三方）。列表中显示的前缀仅用于识别，不可直接用于鉴权。</span>
                    <div className="v3-secret-actions">
                      <code>{v3LastCreatedSecret}</code>
                      <Button size="small" onClick={() => void handleCopyText(v3LastCreatedSecret, 'Key 已复制')}>
                        复制 Key
                      </Button>
                    </div>
                  </div>
                )}
                <div className="policy-rule-table v3-table keyhub-table">
                  <div className="keyhub-head">
                    <span>Key 信息</span>
                    <span>状态 / 管理</span>
                  </div>
                  {v3ApiKeys.length === 0 && <div className="policy-empty muted">暂无 API Key</div>}
                  {v3ApiKeys.map((item) => (
                    <div key={item.id} className="keyhub-row">
                      <div className="keyhub-meta">
                        <div className="keyhub-meta-head">
                          <strong>{item.name}</strong>
                          <span className="meta-pill">{item.key_prefix}</span>
                        </div>
                        <div className="keyhub-permission-chips">
                          {(item.permissions.length ? item.permissions : ['*']).map((permission) => (
                            <span key={`${item.id}-${permission}`} className="keyhub-permission-chip">
                              {permission}
                            </span>
                          ))}
                        </div>
                        <span className="muted">前缀仅用于识别；完整 Key 仅在创建或轮换后展示一次。</span>
                      </div>
                      <div className="v3-row-actions keyhub-actions">
                        <span className={`status-chip ${item.enabled ? 'ok' : 'warn'}`}>{item.enabled ? '启用' : '停用'}</span>
                        <Button size="small" onClick={() => void handleV3RotateApiKey(item)}>轮换 Key</Button>
                        <Switch
                          size="small"
                          checked={item.enabled}
                          onChange={(checked) => void handleV3ToggleApiKey(item.id, checked)}
                        />
                        <Button size="small" danger onClick={() => void handleV3DeleteApiKey(item.id)}>删除</Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="panel-card v3-card">
                <h3>第三方接入说明</h3>
                <p className="muted">内置诊断工作台和会话历史通过受信任 UI 通道访问统一 Run API，不需要你在前端手动配置 API Key。这里的 Key 仅用于共享给第三方系统、自动化脚本或测试客户端。</p>
                <div className="v3-template-box">
                  <div className="v3-template-item keyhub-info-item">
                    <strong>统一入口</strong>
                    <code>POST /api/runs, GET /api/runs, GET /api/runs/{'{runId}'}, GET /api/runs/{'{runId}'}/timeline</code>
                  </div>
                  <div className="v3-template-item keyhub-info-item">
                    <strong>鉴权方式</strong>
                    <code>X-API-Key: your_real_key_here</code>
                  </div>
                  <div className="v3-template-item keyhub-info-item">
                    <strong>客户端提示</strong>
                    <code>统一 Python 客户端支持 --host/--hosts、--problem/--question；未传 --api-key 时会优先自动创建临时 Key。</code>
                  </div>
                  <div className="v3-template-item keyhub-info-item">
                    <strong>建议</strong>
                    <code>按系统/团队分配独立 Key，权限最小化，定期轮换；正式环境建议固定 Key，不依赖临时 Key。</code>
                  </div>
                </div>
                <pre className="detail-pre keyhub-doc-pre">{`# 直接调用统一 Run API
curl -sS -X POST 'http://127.0.0.1:8000/api/runs' \\
  -H 'X-API-Key: your_real_key_here' \\
  -H 'Content-Type: application/json' \\
  -d '{
    "problem":"多设备异常关联分析",
    "operation_mode":"diagnosis",
    "automation_level":"assisted",
    "devices":[{"host":"192.168.0.88","protocol":"ssh","username":"***","password":"***"}]
  }'

# 使用统一 Python 客户端
./.venv/bin/python scripts/unified_diag_client.py \\
  --base-url http://127.0.0.1:8000 \\
  --host 192.168.0.102 \\
  --username zhangwei \\
  --password Admin@123 \\
  --problem "接口 Eth2 接口 disable问题" \\
  --stream-events \\
  --timeout 120`}</pre>
              </div>
            </div>
          )}

          {activePage === 'control' && (
            <div className="page-grid control-layout">
              <div className="panel-card control-main-panel">
                <h3>连接控制</h3>
                <p className="muted">统一入口：输入一个地址=单设备，多地址（逗号/空格/换行分隔）=多设备协同。</p>
                <div className="control-card-stack">
                  <AutomationLevelSelector className="control-card-tight" value={automationLevel} onChange={setAutomationLevel} />
                  <TaskModeSelector className="control-card-tight" value={operationMode} onChange={setOperationMode} />
                  <DeviceForm
                    className="control-card-tight"
                    automationLevel={automationLevel}
                    operationMode={operationMode}
                    onCreate={handleCreateSession}
                  />
                </div>
              </div>
              <div className="panel-card control-status-panel">
                <h3>连接状态</h3>
                <p className="muted">这里集中展示当前连接会话、模型可用性和最近多设备协同状态，方便在同一页确认连接是否进入可诊断状态。</p>
                <div className="control-status-grid">
                  <div className="control-kv-card"><span>会话 ID</span><strong>{session?.id || '-'}</strong></div>
                  <div className="control-kv-card"><span>会话模式</span><strong>{session?.operation_mode ? operationModeLabel(session.operation_mode) : '-'}</strong></div>
                  <div className="control-kv-card"><span>命令执行控制等级</span><strong>{automationLabel(automationLevel)}</strong></div>
                  <div className="control-kv-card"><span>LLM</span><strong>{llmStatus?.enabled ? '已启用' : '未启用'}</strong></div>
                  <div className="control-kv-card"><span>协同任务 ID</span><strong>{v3SelectedJobSummary?.id || '-'}</strong></div>
                  <div className="control-kv-card"><span>协同任务阶段</span><strong>{v3SelectedJobSummary ? `${v3SelectedJobSummary.status} / ${v3SelectedJobSummary.phase}` : '-'}</strong></div>
                  <div className="control-kv-card control-kv-card-wide"><span>待审批命令组</span><strong>{v3SelectedJobSummary?.pending_action_groups ?? '-'}</strong></div>
                </div>
                <div className="trace-head control-status-history-head">
                  <div>
                    <h3 style={{ marginBottom: 4 }}>近期多设备协同</h3>
                    <p className="muted">不再单独拆一页，最近任务直接在统一入口下查看和恢复。</p>
                  </div>
                  <Button size="small" onClick={() => void refreshV3Jobs()} disabled={v3JobsLoading}>
                    {v3JobsLoading ? '刷新中...' : '刷新'}
                  </Button>
                </div>
                <div className="session-history-list control-status-history-list">
                  {recentMultiJobItems.length === 0 && <div className="muted">暂无多设备协同任务</div>}
                  {recentMultiJobItems.map((job) => (
                    <div
                      key={job.id}
                      className={`session-history-item ${v3SelectedJobId === job.id ? 'selected' : ''}`}
                    >
                      <div className="session-history-open">
                        <div className="session-history-main">
                          <strong>{job.name || `协同任务 ${job.id.slice(0, 8)}...`}</strong>
                          <span>{`设备 ${job.device_count || 0} 台 / 命令 ${job.command_count || 0} 条`}</span>
                          <span>{`${job.status} / ${job.phase}`}</span>
                          <span>{truncateText(job.problem || '-', 80)}</span>
                        </div>
                        <div className="session-history-meta">
                          <span>{job.id.slice(0, 8)}...</span>
                          <span>{formatTime(job.created_at)}</span>
                        </div>
                      </div>
                      <div className="session-history-actions">
                        <Button size="small" onClick={() => void handleRestoreV2HistoryJob(job.id)}>
                          恢复到工作台
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
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
                      判定顺序：先看阻断规则/硬阻断；再按控制等级执行（极低风险仅只读，中风险可执行自动执行低/中风险并对高风险要求确认，高风险可执行自动执行非阻断命令）；最后由放行规则覆盖确认策略。高风险由可编辑风险词表判定（默认含 clear/shutdown 等）。
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
                    <span className="muted">规则状态（本地草稿 vs 服务端）</span>
                    <strong>{policyDirty ? '待保存（有改动未提交）' : '已同步（与服务端一致）'}</strong>
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

              <div className="panel-card policy-tab-shell">
                <div className="policy-tab-bar">
                  <Button
                    size="small"
                    type={policyTab === 'blocked' ? 'primary' : 'default'}
                    onClick={() => setPolicyTab('blocked')}
                  >
                    阻断规则
                  </Button>
                  <Button
                    size="small"
                    type={policyTab === 'executable' ? 'primary' : 'default'}
                    onClick={() => setPolicyTab('executable')}
                  >
                    放行规则
                  </Button>
                  <Button
                    size="small"
                    type={policyTab === 'high' ? 'primary' : 'default'}
                    onClick={() => setPolicyTab('high')}
                  >
                    高风险
                  </Button>
                  <Button
                    size="small"
                    type={policyTab === 'medium' ? 'primary' : 'default'}
                    onClick={() => setPolicyTab('medium')}
                  >
                    中风险
                  </Button>
                </div>

                <div className="policy-tab-body">
                  {policyTab === 'blocked' && (
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
                  )}

                  {policyTab === 'executable' && (
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
                  )}

                  {policyTab === 'high' && (
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
                  )}

                  {policyTab === 'medium' && (
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
                  )}
                </div>
              </div>

            </div>
          )}

          {activePage === 'sessions' && (
            <div className="page-grid two-col sessions-layout">
              <div className="panel-card session-history-panel">
                <div className="trace-head">
                  <div>
                    <h3>历史记录</h3>
                    <p className="muted">包含单设备会话与多设备协同，刷新后可恢复。</p>
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
                      className={`session-history-item ${session?.id === item.id ? 'active' : ''} ${selectedHistorySessionId === item.id ? 'selected' : ''}`}
                      onClick={() => handleSelectHistorySession(item.id)}
                      onDoubleClick={() => void handleRestoreSession(item.id, item.host)}
                    >
                      <div className="session-history-open">
                        <div className="session-history-main compact">
                          <strong>{item.kind === 'multi' ? `多设备协同 · ${item.host}` : item.host}</strong>
                          <span>{formatDeviceName(item.device_name)}</span>
                          <span>{operationModeLabel(item.operation_mode)} / {automationLabel(item.automation_level)}</span>
                          {item.kind === 'multi' && (
                            <span>{`设备 ${item.device_count || 0}`}</span>
                          )}
                          {item.kind === 'multi' && item.problem && (
                            <span>{`问题: ${truncateText(item.problem, 48)}`}</span>
                          )}
                          {item.sop_draft_count ? <span className="meta-pill">草稿 {item.sop_draft_count}</span> : null}
                          {item.sop_published_count ? <span className="meta-pill">已发布 {item.sop_published_count}</span> : null}
                        </div>
                        <div className="session-history-meta">
                          <span>{item.source_id.slice(0, 8)}...</span>
                          <span>{formatTime(item.created_at)}</span>
                        </div>
                      </div>
                      <div className="session-history-actions">
                        <Button
                          size="small"
                          onClick={() => void handleExtractSop(item)}
                          type={(item.primary_sop_id || item.sop_extracted || item.sop_draft_count || item.sop_published_count) ? 'default' : 'primary'}
                          loading={extractingSopHistoryId === item.id}
                          className={(item.primary_sop_id || item.sop_extracted || item.sop_draft_count || item.sop_published_count) ? 'history-sop-link-btn' : undefined}
                          disabled={Boolean(extractingSopHistoryId && extractingSopHistoryId !== item.id)}
                          title={(item.primary_sop_id || item.sop_extracted || item.sop_draft_count || item.sop_published_count) ? '该历史会话已提取 SOP，点击查看对应档案' : '从该历史会话提取 SOP 草稿'}
                        >
                          {(item.primary_sop_id || item.sop_extracted || item.sop_draft_count || item.sop_published_count) ? '查看 SOP' : '提取 SOP'}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="panel-card session-snapshot-panel">
                <h3>当前会话快照</h3>
                {historySnapshotLoading && <div className="muted">快照加载中...</div>}
                <div className="kv"><span>已选会话</span><strong>{selectedHistoryItem?.source_id || selectedHistorySessionId || session?.id || '-'}</strong></div>
                <div className="kv"><span>设备</span><strong>{selectedHistorySnapshotView?.session.device.host || selectedHistoryItem?.host || sessionDeviceAddress || '-'}</strong></div>
                <div className="kv"><span>设备名称</span><strong>{formatDeviceName(selectedHistorySnapshotView?.session.device.name || selectedHistoryItem?.device_name || sessionDeviceName)}</strong></div>
                <div className="kv"><span>模式</span><strong>{selectedHistorySnapshotView?.session.operation_mode ? operationModeLabel(selectedHistorySnapshotView.session.operation_mode) : (selectedHistoryItem?.operation_mode ? operationModeLabel(selectedHistoryItem.operation_mode) : '-')}</strong></div>
                <div className="kv"><span>自动化等级</span><strong>{selectedHistorySnapshotView?.session.automation_level ? automationLabel(selectedHistorySnapshotView.session.automation_level) : (selectedHistoryItem?.automation_level ? automationLabel(selectedHistoryItem.automation_level) : '-')}</strong></div>
                <div className="kv"><span>状态</span><strong>{selectedHistoryItem?.kind === 'multi' ? (selectedHistoryItem.status || '-') : (selectedHistorySnapshotView?.session.status || selectedHistoryItem?.status || '-')}</strong></div>
                <div className="kv"><span>创建时间</span><strong>{formatTime(selectedHistorySnapshotView?.session.created_at || selectedHistoryItem?.created_at || '')}</strong></div>
                <div className="kv"><span>最近活动</span><strong>{formatTime(snapshotUpdatedAt || '')}</strong></div>
                <div className="kv"><span>消息数</span><strong>{selectedHistorySnapshotView?.messages.length ?? 0}</strong></div>
                <div className="kv"><span>命令数</span><strong>{selectedHistorySnapshotView?.commands.length ?? 0}</strong></div>
                <div className="kv"><span>证据数</span><strong>{selectedHistorySnapshotView?.evidences.length ?? 0}</strong></div>
                <div className="kv"><span>SOP 候选命中</span><strong>{snapshotSopCandidateHits > 0 ? `是（${snapshotSopCandidateHits} 次）` : '否'}</strong></div>
                <div className="kv"><span>AI 已引用 SOP</span><strong>{snapshotSopReferencedCount > 0 ? `是（${snapshotSopReferencedCount} 次）` : '否'}</strong></div>
                <div className="kv"><span>关联 SOP</span><strong>{snapshotPrimarySopId || '-'}</strong></div>
                <div className="snapshot-text-block">
                  <span>用户问题</span>
                  <p>{snapshotLatestUserQuestion}</p>
                </div>
                <div className="snapshot-text-block">
                  <span>最终结论</span>
                  <p>{snapshotLatestConclusion}</p>
                </div>
                <div className="snapshot-text-block">
                  <span>建议动作</span>
                  <p>{snapshotLatestRecommendation}</p>
                </div>
              </div>
            </div>
          )}

          {activePage === 'service_trace' && (
            <div className="page-grid service-trace-layout">
              <div className="panel-card">
                <div className="trace-head">
                  <div>
                    <h3>流程追踪</h3>
                    <p className="muted">展示完整业务流程、判定动作与实时激活节点。</p>
                  </div>
                  <div className="trace-head-actions">
                    <Select
                      size="small"
                      style={{ minWidth: 360 }}
                      placeholder="选择会话/协同记录"
                      value={traceTargetSessionId}
                      options={traceSessionOptions}
                      onChange={(value) => setSelectedHistorySessionId(String(value))}
                    />
                    <Button size="small" onClick={() => void refreshServiceTraceForTarget(traceTargetSessionId, false)} disabled={!traceTargetSessionId || traceLoading}>
                      {traceLoading ? '刷新中...' : '刷新'}
                    </Button>
                  </div>
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
                    <span className="muted">当前追踪源</span>
                    <strong>{traceTargetHistoryItem?.source_id ? `${traceTargetHistoryItem.source_id.slice(0, 8)}...` : (traceTargetSessionId ? `${traceTargetSessionId.slice(0, 8)}...` : '-')}</strong>
                  </div>
                </div>
              </div>

              <div className="panel-card flow-board">
                <div className="trace-head">
                  <div>
                    <h3>业务流程图</h3>
                    <p className="muted">按泳道展示每个动作；点击节点可区分前序/后续步骤，并查看约束判定来源。</p>
                  </div>
                  <div className="flow-head-right">
                    <span className="status-chip">Active #{selectedTraceStep?.seq_no || '-'}</span>
                    <button
                      type="button"
                      className="flow-legend flow-legend-btn mode-toggle"
                      onClick={() => setFlowLayoutMode((prev) => (prev === 'stair' ? 'compact' : 'stair'))}
                      title={flowLayoutMode === 'stair' ? '切换到紧贴模式' : '切换到阶梯模式'}
                    >
                      {flowLayoutMode === 'stair' ? '阶梯模式' : '紧贴模式'}
                    </button>
                    <button
                      type="button"
                      className="flow-legend flow-legend-btn upstream"
                      disabled={!previousTraceStep}
                      onClick={() => previousTraceStep && jumpToTraceStep(previousTraceStep.id)}
                      title={previousTraceStep ? `跳到前序步骤 #${previousTraceStep.seq_no}` : '无前序步骤'}
                    >
                      ⬆️ 前序
                    </button>
                    <button
                      type="button"
                      className="flow-legend flow-legend-btn downstream"
                      disabled={!nextTraceStep}
                      onClick={() => nextTraceStep && jumpToTraceStep(nextTraceStep.id)}
                      title={nextTraceStep ? `跳到后续步骤 #${nextTraceStep.seq_no}` : '无后续步骤'}
                    >
                      后续 ⬇️
                    </button>
                    <button
                      type="button"
                      className={`flow-legend flow-legend-btn play ${tracePlaybackActive ? 'active' : ''}`}
                      disabled={sortedTraceSteps.length <= 1}
                      onClick={handleToggleTracePlayback}
                      title={tracePlaybackActive ? '停止自动播放' : '按步骤耗时自动播放后续步骤'}
                    >
                      {tracePlaybackActive ? '■ 停止' : '▶ Play'}
                    </button>
                  </div>
                </div>
                {flowLayoutMode === 'stair' ? (
                  <div className="flow-lanes">
                    <div
                      className="flow-matrix"
                      style={{ gridTemplateColumns: `repeat(${Math.max(1, flowLanes.length)}, minmax(0, 1fr))` }}
                    >
                      {flowLanes.map((lane) => (
                        <div
                          key={`head-${lane.key}`}
                          className={`flow-lane-head-cell ${activeFlowLaneKey === lane.key ? 'active' : ''}`}
                        >
                          <div className="flow-lane-head-main">
                            <strong>{lane.label}</strong>
                            <span className={`flow-actor-pill ${traceActorClass(lane.actor)}`}>{lane.actor}</span>
                          </div>
                          <span>{traceLaneCounts[lane.key] ?? lane.realCount} 步</span>
                        </div>
                      ))}

                      {flowRows.length === 0 && (
                        <div className="flow-matrix-empty">暂无流程步骤，等待会话执行后展示。</div>
                      )}

                      {flowRows.map((row, rowIndex) =>
                        row.map(({ lane, step }) => {
                          if (!step) {
                            return (
                              <div
                                key={`gap-${lane.key}-${rowIndex}`}
                                className="flow-node flow-node-gap"
                                aria-hidden="true"
                              />
                            )
                          }
                          return (
                            <button
                              type="button"
                              key={step.id}
                              data-trace-step-id={step.id}
                              className={`flow-node ${activeFlowStepId === step.id ? 'active' : ''} ${selectedTraceStep?.id === step.id ? 'selected' : ''} ${traceNodeRelationClass(step, selectedTraceStep)}`}
                              onClick={() => setSelectedTraceStepId(step.id)}
                              title={[
                                `#${step.seq_no} ${formatTraceStepTitle(step)}`,
                                `${traceStepActorLabel(step.step_type)} · ${traceTypeLabel(step.step_type)} · ${step.status}`,
                                traceConstraintLabel(step) ? `约束判定 · ${traceConstraintLabel(step)}` : '',
                              ].filter(Boolean).join('\n')}
                            >
                              <div className="flow-node-line">
                                <div className="flow-node-title">
                                  <span className={`flow-actor-pill ${traceActorClass(traceStepActorLabel(step.step_type))}`}>
                                    {traceStepActorLabel(step.step_type)}
                                  </span>
                                  <span className="flow-node-title-text">#{step.seq_no} {formatTraceStepTitle(step)}</span>
                                </div>
                                <div className="flow-node-type">{traceTypeLabel(step.step_type)}</div>
                                <div className="flow-node-constraint">{traceConstraintLabel(step) || '-'}</div>
                                <span className={`trace-status compact ${traceStatusClass(step.status)}`}>{step.status}</span>
                              </div>
                            </button>
                          )
                        }),
                      )}
                    </div>
                  </div>
                ) : (
                  <div
                    className="flow-lanes compact"
                    style={{ gridTemplateColumns: `repeat(${Math.max(1, flowLanes.length)}, minmax(0, 1fr))` }}
                  >
                    {flowLanes.map((lane) => (
                      <section key={lane.key} className="flow-lane">
                        <div className={`flow-lane-head ${activeFlowLaneKey === lane.key ? 'active' : ''}`}>
                          <div className="flow-lane-head-main">
                            <strong>{lane.label}</strong>
                            <span className={`flow-actor-pill ${traceActorClass(lane.actor)}`}>{lane.actor}</span>
                          </div>
                          <span>{traceLaneCounts[lane.key] ?? lane.realCount} 步</span>
                        </div>
                        <div className="flow-node-list">
                          {lane.realCount === 0 && <div className="flow-node flow-node-empty">待触发</div>}
                          {lane.steps
                            .filter((step): step is ServiceTraceStep => Boolean(step))
                            .map((step) => (
                              <button
                                type="button"
                                key={step.id}
                                data-trace-step-id={step.id}
                                className={`flow-node ${activeFlowStepId === step.id ? 'active' : ''} ${selectedTraceStep?.id === step.id ? 'selected' : ''} ${traceNodeRelationClass(step, selectedTraceStep)}`}
                                onClick={() => setSelectedTraceStepId(step.id)}
                                title={[
                                  `#${step.seq_no} ${formatTraceStepTitle(step)}`,
                                  `${traceStepActorLabel(step.step_type)} · ${traceTypeLabel(step.step_type)} · ${step.status}`,
                                  traceConstraintLabel(step) ? `约束判定 · ${traceConstraintLabel(step)}` : '',
                                ].filter(Boolean).join('\n')}
                              >
                                <div className="flow-node-line">
                                  <div className="flow-node-title">
                                    <span className={`flow-actor-pill ${traceActorClass(traceStepActorLabel(step.step_type))}`}>
                                      {traceStepActorLabel(step.step_type)}
                                    </span>
                                    <span className="flow-node-title-text">#{step.seq_no} {formatTraceStepTitle(step)}</span>
                                  </div>
                                  <div className="flow-node-type">{traceTypeLabel(step.step_type)}</div>
                                  <div className="flow-node-constraint">{traceConstraintLabel(step) || '-'}</div>
                                  <span className={`trace-status compact ${traceStatusClass(step.status)}`}>{step.status}</span>
                                </div>
                              </button>
                            ))}
                        </div>
                      </section>
                    ))}
                  </div>
                )}
              </div>

              <div className="trace-bottom-grid">
                <div className="panel-card trace-detail-panel">
                  <div className="trace-bottom-head">
                    <strong>节点详情</strong>
                    {selectedTraceStep && <span className="status-chip">#{selectedTraceStep.seq_no}</span>}
                  </div>
                  {selectedTraceStep ? (
                    <div className="flow-detail-card">
                      <div className="flow-detail-head">
                        <strong>节点详情 #{selectedTraceStep.seq_no}</strong>
                        <div className="flow-detail-head-actions">
                          <span className={`trace-status ${traceStatusClass(selectedTraceStep.status)}`}>{selectedTraceStep.status}</span>
                        </div>
                      </div>
                      <div className="flow-detail-grid">
                        <div><span>类型</span><strong>{traceTypeLabel(selectedTraceStep.step_type)}</strong></div>
                        <div><span>约束判定</span><strong>{traceConstraintLabel(selectedTraceStep) || '-'}</strong></div>
                        <div><span>开始</span><strong>{formatTraceTime(selectedTraceStep.started_at)}</strong></div>
                        <div><span>结束</span><strong>{selectedTraceStep.completed_at ? formatTraceTime(selectedTraceStep.completed_at) : '-'}</strong></div>
                        <div><span>耗时</span><strong>{selectedTraceStep.duration_ms !== undefined ? formatDuration(selectedTraceStep.duration_ms) : '-'}</strong></div>
                      </div>
                      {selectedTraceDetailSections.length > 0 ? (
                        <div className="flow-detail-sections">
                          {selectedTraceDetailSections.map((section) => (
                            <section key={section.key} className="flow-detail-section">
                              <div className="flow-detail-section-title">{section.title}</div>
                              <pre className="flow-detail-text">{section.body}</pre>
                            </section>
                          ))}
                        </div>
                      ) : (
                        <pre className="flow-detail-text">{selectedTraceStep.detail || '(无详细信息)'}</pre>
                      )}
                    </div>
                  ) : (
                    <div className="trace-empty muted">暂无选中节点</div>
                  )}
                </div>

                <div className="panel-card trace-steps-panel">
                  <div className="trace-bottom-head">
                    <strong>步骤明细</strong>
                    <span className="muted">{traceSteps.length} 条</span>
                  </div>
                  <div className="trace-row trace-row-head">
                    <span>#</span>
                    <span>步骤</span>
                    <span>状态</span>
                    <span>开始</span>
                    <span>结束</span>
                    <span>耗时</span>
                  </div>
                  <div className="trace-list-scroll" ref={traceListScrollRef}>
                    {traceSteps.length === 0 && <div className="trace-empty muted">暂无追踪数据。先创建会话并执行一次对话。</div>}
                    {traceSteps.map((step) => {
                      const width = traceStats.maxDurationMs > 0 && step.duration_ms !== undefined
                        ? Math.max(4, Math.round((step.duration_ms / traceStats.maxDurationMs) * 100))
                        : 0
                      return (
                        <div
                          key={step.id}
                          data-trace-step-id={step.id}
                          data-trace-list-step-id={step.id}
                          className={`trace-row trace-row-item ${selectedTraceStep?.id === step.id ? 'active' : ''} ${traceNodeRelationClass(step, selectedTraceStep)}`}
                          onClick={() => jumpToTraceStep(step.id)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault()
                              jumpToTraceStep(step.id)
                            }
                          }}
                        >
                          <span>{step.seq_no}</span>
                          <div className="trace-step-cell">
                            <div className="trace-step-title">{formatTraceStepTitle(step)}</div>
                            <div className="trace-step-meta">
                              {traceStepActorLabel(step.step_type)} · {traceTypeLabel(step.step_type)}
                            </div>
                            {step.detail && <div className="trace-step-detail">{step.detail}</div>}
                            {width > 0 && (
                              <div className="trace-bar-wrap">
                                <div className="trace-bar" style={{ width: `${width}%` }} />
                              </div>
                            )}
                          </div>
                          <span className={`trace-status ${traceStatusClass(step.status)}`}>{step.status}</span>
                          <span>{formatTraceTime(step.started_at)}</span>
                          <span>{step.completed_at ? formatTraceTime(step.completed_at) : '-'}</span>
                          <span>{step.duration_ms !== undefined ? formatDuration(step.duration_ms) : '-'}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            </div>
          )}

          {activePage === 'sop_library' && (
            <div className="learning-layout">
              <div className="panel-card learning-sop-card">
                <div className="learning-toolbar-main">
                  <div>
                    <h3>SOP 档案库</h3>
                    <p className="muted learning-subtitle">SOP 是旁挂知识库，不插入主线诊断 UI。历史会话可手动触发 “AI 提取 SOP”，草稿审核发布后才会在后台作为 AI 可选参考参与匹配。</p>
                  </div>
                  <div className="capability-toolbar-actions">
                    <Button size="small" onClick={() => void refreshSops()} disabled={sopLoading}>
                      {sopLoading ? '刷新中...' : '刷新'}
                    </Button>
                  </div>
                </div>
                <div className="learning-sop-banner">
                  工作台不会展示 SOP 候选摘要。发布后的 SOP 仅在后台作为 AI 的可选参考，不会自动执行，也不会替代 AI 的证据判断闭环。
                </div>

                <div className="sop-tab-row">
                  {[
                    { key: 'draft', label: `草稿 (${sopDrafts.length})` },
                    { key: 'published', label: `已发布 (${sopPublished.length})` },
                    { key: 'archived', label: `已归档 (${sopArchived.length})` },
                    { key: 'logic', label: '说明 / 当前逻辑' },
                  ].map((tab) => (
                    <button
                      key={tab.key}
                      type="button"
                      className={`sop-tab-button ${sopTab === tab.key ? 'active' : ''}`}
                      onClick={() => setSopTab(tab.key as SopTab)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {sopTab === 'logic' ? (
                  <div className="sop-logic-grid">
                    <section className="panel-card sop-logic-card">
                      <div className="sop-logic-head">
                        <h4>当前调用逻辑</h4>
                        <span className="meta-pill">旁挂知识系统</span>
                      </div>
                      <ol className="sop-logic-list">
                        {SOP_CURRENT_LOGIC_ITEMS.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ol>
                    </section>
                    <section className="panel-card sop-logic-card">
                      <div className="sop-logic-head">
                        <h4>可强化方向</h4>
                        <span className="meta-pill">后续增强</span>
                      </div>
                      <ol className="sop-logic-list">
                        {SOP_HARDENING_HINTS.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ol>
                    </section>
                    <section className="panel-card sop-logic-card">
                      <div className="sop-logic-head">
                        <h4>语义别名匹配</h4>
                        <span className="meta-pill">系统内置</span>
                      </div>
                      <p className="muted">当前触发词、适用前提、不适用条件会先做语义别名匹配，再做命中判断。下面这些同类表达会被系统视为一组。</p>
                      <div className="sop-alias-grid">
                        {SOP_SEMANTIC_ALIAS_GROUPS.map((group) => (
                          <div key={group.label} className="sop-alias-card">
                            <strong>{group.label}</strong>
                            <div className="sop-alias-tags">
                              {group.aliases.map((alias) => (
                                <span key={alias} className="meta-pill">{alias}</span>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  </div>
                ) : (
                  <div className="sop-library-grid" style={{ gridTemplateColumns: `${sopListWidth}px 10px minmax(0, 1fr)` }}>
                    <section className="panel-card sop-list-panel">
                      <div className="learning-sop-section-head compact">
                        <strong>{sopTab === 'draft' ? '草稿列表' : sopTab === 'published' ? '已发布列表' : '已归档列表'}</strong>
                        <div className="sop-list-head-actions">
                          <span className="muted">{currentSopItems.length} 条</span>
                          <div className="sop-density-toggle" role="tablist" aria-label="SOP列表密度">
                            <button
                              type="button"
                              className={sopListDensity === 'compact' ? 'active' : ''}
                              onClick={() => setSopListDensity('compact')}
                            >
                              紧凑
                            </button>
                            <button
                              type="button"
                              className={sopListDensity === 'expanded' ? 'active' : ''}
                              onClick={() => setSopListDensity('expanded')}
                            >
                              展开
                            </button>
                          </div>
                        </div>
                      </div>
                      <div className={`learning-sop-list ${sopListDensity === 'compact' ? 'is-compact' : 'is-expanded'}`}>
                        {currentSopItems.length === 0 && <div className="policy-empty muted">当前分组暂无 SOP 记录</div>}
                        {currentSopItems.map((entry) => (
                          <button
                            key={entry.id}
                            type="button"
                            className={`sop-record-item ${selectedSopId === entry.id ? 'active' : ''} ${sopListDensity === 'compact' ? 'compact' : 'expanded'}`}
                            onClick={() => void handleSelectSop(entry.id)}
                          >
                            <div className="sop-record-row">
                              <div className="sop-record-main compact">
                                <strong>{entry.name}</strong>
                                <span className="meta-pill">v{entry.version}</span>
                                <span>{`命 ${entry.matched_count || 0}`}</span>
                                <span>{`引 ${entry.referenced_count || 0}`}</span>
                                <span>{`成 ${entry.success_count || 0}`}</span>
                              </div>
                            </div>
                            {sopListDensity === 'expanded' ? (
                              <span className="muted">{truncateText(entry.id, 42)}</span>
                            ) : null}
                            {sopListDensity === 'expanded' ? (
                              <span className="muted">{truncateText(entry.summary, 96)}</span>
                            ) : null}
                          </button>
                        ))}
                      </div>
                    </section>

                    <div
                      className="sop-drag-divider"
                      role="separator"
                      aria-orientation="vertical"
                      onMouseDown={() => setSopResizing(true)}
                    />

                    <section className="panel-card sop-detail-panel">
                      {!selectedSop || !sopEditor ? (
                        <div className="policy-empty muted">请选择左侧 SOP 记录查看详情与审核内容。</div>
                      ) : (
                        <div className="sop-detail-stack">
                          <div className="sop-detail-head">
                            <div>
                              <h4>{selectedSop.name}</h4>
                              <p className="muted">{selectedSop.id} · 状态 {selectedSop.status || sopTab}</p>
                            </div>
                            <div className="capability-toolbar-actions">
                              {sopTab === 'draft' && <Button size="small" type="primary" className="sop-action-btn sop-action-btn-primary" loading={sopSaving} onClick={() => void handlePublishSop()}>发布</Button>}
                              {sopTab === 'published' && <Button size="small" className="sop-action-btn" loading={sopSaving} onClick={() => void handleSaveSop()}>编辑并生成草稿</Button>}
                              {(sopTab === 'draft' || sopTab === 'published') && <Button size="small" className="sop-action-btn" loading={sopSaving} onClick={() => void handleArchiveSop()}>归档</Button>}
                              <Button size="small" className="sop-action-btn" loading={sopSaving} onClick={() => void handleReextractSop()}>重新提炼</Button>
                              {sopTab !== 'published' && <Button size="small" danger className="sop-action-btn sop-action-btn-danger" loading={sopSaving} onClick={() => void handleDeleteSop()}>删除</Button>}
                            </div>
                          </div>

                          <section className="sop-section-card">
                            <div className="sop-section-head">
                              <strong>基础信息</strong>
                              <span className="muted">描述这条 SOP 是什么、从哪次会话沉淀而来。</span>
                            </div>
                            <div className="sop-detail-grid">
                              <label className="sop-field">
                                <span>SOP 名称</span>
                                <Input value={sopEditor.name} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, name: event.target.value } : prev)} />
                                <small className="muted">这条 SOP 的主标题，建议直接写成问题类型或排查主题。</small>
                              </label>
                              <label className="sop-field">
                                <span>来源会话 ID</span>
                                <Input value={(sopEditor.source_run_ids || []).join(', ')} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, source_run_ids: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">记录这条 SOP 是从哪些历史会话提炼而来，便于回溯证据来源。</small>
                              </label>
                              <label className="sop-field">
                                <span>适用场景摘要</span>
                                <Input.TextArea rows={3} value={sopEditor.summary} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, summary: event.target.value } : prev)} />
                                <small className="muted">一句话说明这条 SOP 解决什么问题、适用于什么场景。</small>
                              </label>
                              <label className="sop-field">
                                <span>AI 使用提示</span>
                                <Input.TextArea rows={3} value={sopEditor.usage_hint} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, usage_hint: event.target.value } : prev)} />
                                <small className="muted">告诉 AI 何时优先参考这条 SOP，以及调用时要注意什么。</small>
                              </label>
                            </div>
                          </section>

                          <section className="sop-section-card">
                            <div className="sop-section-head">
                              <strong>匹配条件</strong>
                              <span className="muted">决定 AI 何时可能参考这条 SOP。</span>
                            </div>
                            <div className="sop-detail-grid">
                              <label className="sop-field">
                                <span>问题触发词</span>
                                <Input.TextArea rows={2} value={joinTagInput(sopEditor.trigger_keywords)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, trigger_keywords: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">用于按用户问题匹配候选 SOP，建议填写故障关键词而不是整句原话。</small>
                              </label>
                              <label className="sop-field">
                                <span>厂商 / 平台标签</span>
                                <Input.TextArea rows={2} value={joinTagInput(sopEditor.vendor_tags)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, vendor_tags: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">限定适用厂商或命令家族，例如 `huawei`、`arista`。</small>
                              </label>
                              <label className="sop-field">
                                <span>版本指纹匹配</span>
                                <Input.TextArea rows={2} value={joinTagInput(sopEditor.version_signatures)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, version_signatures: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">限定具体软硬件版本或指纹片段，减少跨版本误用。</small>
                              </label>
                              <label className="sop-field">
                                <span>目标证据</span>
                                <Input.TextArea rows={2} value={joinTagInput(sopEditor.evidence_goals)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, evidence_goals: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">执行这条 SOP 后，理想情况下应该验证到哪些关键信号。</small>
                              </label>
                              <label className="sop-field">
                                <span>适用前提</span>
                                <Input.TextArea rows={3} value={joinTagInput(sopEditor.preconditions)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, preconditions: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">列出调用这条 SOP 前必须满足的条件，例如设备类型、权限、对象已知等。</small>
                              </label>
                              <label className="sop-field">
                                <span>不适用条件</span>
                                <Input.TextArea rows={3} value={joinTagInput(sopEditor.anti_conditions)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, anti_conditions: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">列出不应调用这条 SOP 的场景，避免 AI 在错误上下文中引用。</small>
                              </label>
                            </div>
                            <div className="sop-alias-inline-note">
                              <div className="sop-alias-inline-head">
                                <strong>语义别名匹配</strong>
                                <span className="muted">触发词 / 适用前提 / 不适用条件会按下列同义词一起判断。</span>
                              </div>
                              <div className="sop-alias-tags">
                                {SOP_SEMANTIC_ALIAS_GROUPS.map((group) => (
                                  <span key={group.label} className="meta-pill">{`${group.label}: ${group.aliases.join(' / ')}`}</span>
                                ))}
                              </div>
                            </div>
                          </section>

                          <section className="sop-section-card">
                            <div className="sop-section-head">
                              <strong>命令策略</strong>
                              <span className="muted">建议 AI 优先参考的最小命令组与补充证据。</span>
                            </div>
                            <div className="sop-detail-grid">
                              <label className="sop-field sop-field-span-2">
                                <span>建议最小命令组</span>
                                <Input.TextArea rows={4} value={joinCommandTemplatesInput(sopEditor.command_templates)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, command_templates: parseCommandTemplatesInput(event.target.value) } : prev)} />
                                <small className="muted">AI 参考这条 SOP 时优先考虑的最小排查命令组。每行格式：vendor | cmd1 ; cmd2 ; cmd3</small>
                              </label>
                              <label className="sop-field">
                                <span>备选查询命令</span>
                                <Input.TextArea rows={2} value={joinTagInput(sopEditor.fallback_commands)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, fallback_commands: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">当主命令组不兼容、权限不足或输出过大时，可供 AI 退一步参考的备选查询命令。</small>
                              </label>
                              <label className="sop-field">
                                <span>预期证据信号</span>
                                <Input.TextArea rows={2} value={joinTagInput(sopEditor.expected_findings)} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, expected_findings: parseTagInput(event.target.value) } : prev)} />
                                <small className="muted">调用后通常会看到的结果，用来帮助 AI 判断是否命中预期路径。</small>
                              </label>
                            </div>
                          </section>

                          <section className="sop-section-card">
                            <div className="sop-section-head">
                              <strong>审核信息</strong>
                              <span className="muted">沉淀过程中的人工补充和复核备注。</span>
                            </div>
                            <div className="sop-detail-grid">
                              <label className="sop-field sop-field-span-2">
                                <span>审核备注</span>
                                <Input.TextArea rows={3} value={sopEditor.review_notes || ''} onChange={(event) => setSopEditor((prev) => prev ? { ...prev, review_notes: event.target.value } : prev)} />
                                <small className="muted">记录人工复核时的注意事项、风险提醒或后续待完善点。</small>
                              </label>
                            </div>
                          </section>

                          <div className="sop-metrics-grid">
                            <div className="policy-stat-card"><span className="muted">命中</span><strong>{selectedSop.matched_count || 0}</strong></div>
                            <div className="policy-stat-card"><span className="muted">引用</span><strong>{selectedSop.referenced_count || 0}</strong></div>
                            <div className="policy-stat-card"><span className="muted">成功</span><strong>{selectedSop.success_count || 0}</strong></div>
                            <div className="policy-stat-card"><span className="muted">模型</span><strong>{selectedSop.generated_by_model || '-'}</strong></div>
                          </div>

                          {sopTab !== 'published' && (
                            <div className="sop-detail-actions">
                              <Button type="primary" loading={sopSaving} onClick={() => void handleSaveSop()}>
                                保存
                              </Button>
                            </div>
                          )}
                        </div>
                      )}
                    </section>
                  </div>
                )}
              </div>
            </div>
          )}

          {activePage === 'learning' && (
            <div className="learning-layout">
              <div className="panel-card learning-toolbar-card">
                <div className="learning-toolbar-main">
                  <div>
                    <h3>命令执行纠正（版本级：失败阻断 / 替代改写）</h3>
                    <p className="muted learning-subtitle">按版本指纹维护命令替代与阻断规则，避免重复试错；这部分是系统执行前的能力修正，不等同于 SOP 档案。</p>
                  </div>
                  <div className="capability-toolbar-actions">
                    <Button size="small" onClick={() => void refreshCommandCapabilityRules()} disabled={capabilityLoading}>
                      {capabilityLoading ? '刷新中...' : '刷新'}
                    </Button>
                    <Button size="small" danger loading={capabilitySaving} onClick={() => void handleResetCapabilityRules()}>
                      清空筛选范围
                    </Button>
                  </div>
                </div>
              </div>
              <div className="panel-card learning-table-card">
                <div className="capability-table">
                  <div className="capability-row capability-filter-row">
                    <span />
                    <div className="capability-filter-cell">
                      <span className="learning-filter-label">版本指纹</span>
                      <Input
                        size="small"
                        value={capabilityVersionFilter}
                        onChange={(event) => setCapabilityVersionFilter(event.target.value)}
                        placeholder="版本指纹搜索，例如 huawei|ne40e|8.180"
                      />
                    </div>
                    <div className="capability-filter-cell">
                      <span className="learning-filter-label">命令</span>
                      <Input
                        size="small"
                        value={capabilityCommandSearch}
                        onChange={(event) => setCapabilityCommandSearch(event.target.value)}
                        placeholder="命令搜索，例如 show interface"
                      />
                    </div>
                    <span />
                    <span />
                    <span />
                    <span />
                    <span />
                    <span />
                    <span />
                  </div>
                  <div className="capability-row capability-head">
                    <span>#</span>
                    <span>版本指纹</span>
                    <span>命令</span>
                    <span>动作</span>
                    <span>替代</span>
                    <span>来源</span>
                    <span>命中</span>
                    <span>最近命中</span>
                    <span>状态</span>
                    <span>操作</span>
                  </div>
                  <div className="capability-row capability-add-row">
                    <span>{editingCapabilityId ? '编' : '+'}</span>
                    <Input
                      size="small"
                      value={capabilityVersionInput}
                      onChange={(event) => setCapabilityVersionInput(event.target.value)}
                      placeholder={capabilityEffectiveVersion ? `默认 ${capabilityEffectiveVersion}` : '输入版本指纹（必填）'}
                    />
                    <Input
                      size="small"
                      value={capabilityCommandInput}
                      onChange={(event) => setCapabilityCommandInput(event.target.value)}
                      placeholder="命令（例如 show inventory）"
                    />
                    <Select
                      size="small"
                      value={capabilityActionInput}
                      options={[
                        { value: 'rewrite', label: 'rewrite' },
                        { value: 'block', label: 'block' },
                      ]}
                      onChange={(value) => setCapabilityActionInput(value)}
                    />
                    <Input
                      size="small"
                      value={capabilityRewriteInput}
                      disabled={capabilityActionInput !== 'rewrite'}
                      onChange={(event) => setCapabilityRewriteInput(event.target.value)}
                      placeholder="替代命令（rewrite_to）"
                    />
                    <span>manual</span>
                    <span>-</span>
                    <span>-</span>
                    <span>启用</span>
                    <div className="policy-row-actions capability-add-actions">
                      <span className="capability-switch-slot" aria-hidden="true" />
                      <Button size="small" type="primary" loading={capabilitySaving} onClick={() => void handleSaveCapabilityRule()}>
                        {editingCapabilityId ? '更新' : '新增'}
                      </Button>
                      <Button size="small" onClick={resetCapabilityEditor} disabled={capabilitySaving}>
                        清空
                      </Button>
                    </div>
                  </div>
                  {capabilityRows.length === 0 && <div className="policy-empty muted">暂无学习规则</div>}
                  {capabilityRows.map((rule, index) => (
                    <div key={rule.id} className={`capability-row ${editingCapabilityId === rule.id ? 'active' : ''}`}>
                      <span>{index + 1}</span>
                      <code className="capability-version-cell" title={rule.version_signature || '-'}>{rule.version_signature || '-'}</code>
                      <code title={rule.command_key}>{rule.command_key}</code>
                      <span>{rule.action}</span>
                      <code title={rule.rewrite_to || '-'}>{rule.rewrite_to || '-'}</code>
                      <span>{rule.source}</span>
                      <span>{rule.hit_count}</span>
                      <span>{rule.last_hit_at ? formatTime(rule.last_hit_at) : '-'}</span>
                      <span>{rule.enabled ? '启用' : '停用'}</span>
                      <div className="policy-row-actions">
                        <Switch
                          size="small"
                          checked={rule.enabled}
                          onChange={(checked) => void handleToggleCapabilityEnabled(rule, checked)}
                        />
                        <Button size="small" onClick={() => beginEditCapability(rule)}>编辑</Button>
                        <Button size="small" danger onClick={() => void handleDeleteCapabilityRule(rule.id)}>删除</Button>
                      </div>
                    </div>
                  ))}
                </div>
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
              <div className="panel-card ai-settings-panel">
                <h3>AI 设置</h3>
                <p className="muted">模型配置统一放到此页，工作台保持诊断专注。</p>
                <div className="kv"><span>状态</span><strong>{llmStatus?.enabled ? '已启用' : '未启用'}</strong></div>
                <div className="kv"><span>主模型</span><strong>{llmStatus?.model || '-'}</strong></div>
                <div className="kv"><span>当前生效模型</span><strong>{llmStatus?.active_model || llmStatus?.model || '-'}</strong></div>
                <div className="kv"><span>NVIDIA Key</span><strong>{llmStatus?.nvidia_enabled ? '已配置' : '未配置'}</strong></div>
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
                <Input.Password
                  style={{ marginTop: 8 }}
                  value={nvidiaApiKeyInput}
                  onChange={(event) => setNvidiaApiKeyInput(event.target.value)}
                  placeholder="输入 NVIDIA API Key"
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
              <div className="panel-card ai-prompt-panel">
                <h3>提示词与策略</h3>
                <p className="muted">展示系统当前提供给 AI 的提示词模板（只读）。</p>
                <div className="prompt-policy-meta-grid">
                  <div className="prompt-policy-meta-card">
                    <span>模型</span>
                    <strong>{llmPromptPolicy?.model || llmStatus?.model || '-'}</strong>
                  </div>
                  <div className="prompt-policy-meta-card">
                    <span>Base URL</span>
                    <strong title={llmPromptPolicy?.base_url || llmStatus?.base_url || '-'}>{llmPromptPolicy?.base_url || llmStatus?.base_url || '-'}</strong>
                  </div>
                  <div className="prompt-policy-meta-card">
                    <span>NVIDIA Base URL</span>
                    <strong title={llmPromptPolicy?.nvidia_base_url || llmStatus?.nvidia_base_url || '-'}>{llmPromptPolicy?.nvidia_base_url || llmStatus?.nvidia_base_url || '-'}</strong>
                  </div>
                </div>
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
  if (level === 'read_only') return '极低风险'
  if (level === 'assisted') return '中风险可执行'
  return '高风险可执行'
}

function operationModeLabel(mode: OperationMode): string {
  if (mode === 'diagnosis') return '诊断排障'
  if (mode === 'query') return '状态查询'
  return '⚠️ 配置变更'
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return '-'
  }
}

function formatTraceTime(ts: string): string {
  try {
    const date = new Date(ts)
    return `${date.toLocaleTimeString('zh-CN', { hour12: false })}.${String(date.getMilliseconds()).padStart(3, '0')}`
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
    runtime_mode_scope_policy: '运行时模式范围策略',
    runtime_capability_precheck_policy: '运行时命令能力预检策略',
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
    : ['deepseek-chat', 'deepseek-reasoner', 'meta/llama-3.1-70b-instruct']
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
  traceSteps: ServiceTraceStep[] = [],
): ActivityCard[] {
  const cards: Array<ActivityCard & { sortAt: number; sortIdx: number }> = []
  const tracePlanIterations = new Set<number>()
  for (const step of traceSteps) {
    if (step.step_type !== 'llm_plan') continue
    const iteration = extractTraceIteration(step.title)
    if (iteration !== undefined) tracePlanIterations.add(iteration)
  }

  for (let index = 0; index < messages.length; index += 1) {
    const msg = messages[index]
    if (msg.role === 'assistant') {
      const iteration = extractPlanIterationFromMessage(msg.content)
      if (iteration !== undefined && tracePlanIterations.has(iteration)) {
        continue
      }
    }
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

  for (let index = 0; index < traceSteps.length; index += 1) {
    const step = traceSteps[index]
    if (!shouldDisplayTraceAsActivity(step)) continue
    const createdAt = step.started_at || step.completed_at || ''
    cards.push({
      key: `trace:${step.id}`,
      kind: 'trace',
      createdAt,
      label: activityTraceLabel(step),
      title: activityTraceTitle(step),
      preview: activityTracePreview(step),
      trace: step,
      sortAt: parseSortTime(createdAt, messages.length + commands.length + step.seq_no + index),
      sortIdx: messages.length + commands.length + step.seq_no + index,
    })
  }

  if (summary) {
    const createdAt = summary.created_at || ''
    const latestExistingSortAt = cards.reduce((latest, card) => Math.max(latest, card.sortAt), Number.MIN_SAFE_INTEGER)
    cards.push({
      key: 'summary:latest',
      kind: 'summary',
      createdAt,
      label: '结论',
      title: summary.mode === 'query' || summary.mode === 'config' ? '查询结果' : '最终诊断',
      preview: truncateText(renderSummaryBrief(summary), 170),
      summary,
      sortAt: Math.max(parseSortTime(createdAt, messages.length + commands.length + 1), latestExistingSortAt + 1),
      sortIdx: Number.MAX_SAFE_INTEGER - 1,
    })
  }

  return cards
    .sort((a, b) => {
      if (a.sortAt === b.sortAt) return a.sortIdx - b.sortIdx
      return a.sortAt - b.sortAt
    })
    .map(({ sortAt: _, sortIdx: __, ...rest }) => rest)
}

function filterActivityCards(cards: ActivityCard[], mode: ActivityViewMode): ActivityCard[] {
  if (mode === 'full') return cards
  return cards.filter((item) => {
    if (item.kind === 'message') return item.message.role === 'user'
    if (item.kind === 'trace') return shouldDisplayTraceInCompactMode(item.trace)
    return true
  })
}

function renderActivityDetail(activity: ActivityCard, traceSteps: ServiceTraceStep[] = []): string {
  if (activity.kind === 'message') {
    return activity.message.content
  }

  if (activity.kind === 'trace') {
    return renderTraceActivityDetail(activity.trace, traceSteps)
  }

  if (activity.kind === 'command') {
    const cmd = activity.command
    const output = (cmd.output || '').trim()
    const error = (cmd.error || '').trim()
    return [
      `标题: ${cmd.title}`,
      `状态: ${cmd.status}`,
      `风险: ${riskLabel(cmd.risk_level)}`,
      commandConstraintLabel(cmd) ? `约束: ${commandConstraintLabel(cmd)}` : '',
      commandConstraintReason(cmd) ? `约束说明: ${commandConstraintReason(cmd)}` : '',
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

function shouldDisplayTraceAsActivity(step: ServiceTraceStep): boolean {
  return [
    'ai_context_submit',
    'context_snapshot',
    'llm_request',
    'llm_response',
    'llm_plan',
    'llm_status',
    'plan_decision',
    'plan_parse',
    'loop_control',
    'policy_decision',
    'capability_decision',
    'scope_decision',
    'evidence_parse',
    'session_control',
    'session_adapter',
    'orchestrator_error',
  ].includes(step.step_type)
}

function shouldDisplayTraceInCompactMode(step: ServiceTraceStep): boolean {
  return [
    'llm_request',
    'llm_response',
    'llm_plan',
    'orchestrator_error',
  ].includes(step.step_type)
}

function activityTraceLabel(step: ServiceTraceStep): string {
  if (step.step_type === 'llm_request' || step.step_type === 'llm_response' || step.step_type === 'llm_plan') return 'AI'
  if (step.step_type === 'evidence_parse' || step.step_type === 'command_execution') return '设备'
  return '系统'
}

function activityTraceTitle(step: ServiceTraceStep): string {
  return formatTraceStepTitle(step)
}

function activityTracePreview(step: ServiceTraceStep): string {
  const sections = buildTraceDetailSections(step)
  for (const section of sections) {
    const text = String(section.body || '').trim()
    if (text) return truncateText(text.replace(/\n+/g, ' | '), 220)
  }
  return truncateText(String(step.detail || step.title || '').replace(/\n+/g, ' | '), 220)
}

function renderTraceActivityDetail(step: ServiceTraceStep, allSteps: ServiceTraceStep[] = []): string {
  const sections = buildTraceDetailSections(step, allSteps)
  const blocks = [
    `标题: ${step.title}`,
    `状态: ${step.status}`,
    `类型: ${traceTypeLabel(step.step_type)}`,
    step.duration_ms !== undefined ? `耗时: ${formatDuration(step.duration_ms)}` : '',
  ].filter(Boolean)
  for (const section of sections) {
    const body = String(section.body || '').trim()
    if (!body) continue
    blocks.push(`${section.title}:\n${body}`)
  }
  return blocks.join('\n\n')
}

function extractTraceIteration(title: string): number | undefined {
  const matched = String(title || '').match(/第\s*(\d+)\s*轮/)
  if (!matched) return undefined
  const value = Number(matched[1])
  return Number.isFinite(value) ? value : undefined
}

function extractPlanIterationFromMessage(content: string): number | undefined {
  const matched = String(content || '').match(/^AI\s*规划（第\s*(\d+)\s*轮）/m)
  if (!matched) return undefined
  const value = Number(matched[1])
  return Number.isFinite(value) ? value : undefined
}

function extractLatestMessageByRole(messages: ChatMessage[], role: ChatMessage['role']): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== role) continue
    const text = String(message.content || '').trim()
    if (!text) continue
    return text
  }
  return ''
}

function summarizeSessionConclusion(summary?: DiagnosisSummary): string {
  if (!summary) return '-'
  const result = String(summary.query_result || '').trim()
  if (result) return result
  const cause = String(summary.root_cause || '').trim()
  return cause || '-'
}

function summarizeSessionRecommendation(summary?: DiagnosisSummary): string {
  if (!summary) return '-'
  const followUp = String(summary.follow_up_action || '').trim()
  if (followUp) return followUp
  const recommendation = String(summary.recommendation || '').trim()
  return recommendation || '-'
}

function computeSessionLastUpdatedAt(timeline: Timeline | null): string {
  if (!timeline) return ''
  const candidates: Array<string | undefined> = [timeline.session.created_at]
  for (const message of timeline.messages || []) candidates.push(message.created_at)
  for (const command of timeline.commands || []) {
    candidates.push(command.completed_at)
    candidates.push(command.started_at)
    candidates.push(command.created_at)
  }
  for (const evidence of timeline.evidences || []) candidates.push(evidence.created_at)
  candidates.push(timeline.summary?.created_at)
  let latest = ''
  let latestTs = 0
  for (const item of candidates) {
    if (!item) continue
    const ts = Date.parse(item)
    if (Number.isNaN(ts)) continue
    if (ts >= latestTs) {
      latestTs = ts
      latest = item
    }
  }
  return latest
}

function sleepMs(ms: number): Promise<void> {
  const timeout = Math.max(0, Number(ms) || 0)
  return new Promise((resolve) => {
    window.setTimeout(resolve, timeout)
  })
}

function resolveUnifiedRunId(options: {
  targetId?: string
  sessionHistory: HistorySessionItem[]
  activeSessionId?: string
  sessionRuntimeKind?: 'single' | 'multi'
  multiSessionActiveJobId?: string
}): string | undefined {
  const sid = String(options.targetId || '').trim()
  if (!sid) return undefined
  const historyItem = options.sessionHistory.find((item) => item.id === sid)
  if (historyItem?.run_id) return historyItem.run_id
  const v2JobId = parseV2HistoryJobId(sid)
  if (v2JobId) return toUnifiedMultiRunId(v2JobId)
  if (
    options.activeSessionId === sid
    && options.sessionRuntimeKind === 'multi'
    && options.multiSessionActiveJobId
  ) {
    return toUnifiedMultiRunId(options.multiSessionActiveJobId)
  }
  return toUnifiedSingleRunId(sid)
}

function getMaxTraceSeqNo(traceSteps: ServiceTraceStep[]): number {
  let maxSeq = 0
  for (const item of traceSteps) {
    const seq = Number(item.seq_no || 0)
    if (Number.isFinite(seq) && seq > maxSeq) {
      maxSeq = seq
    }
  }
  return maxSeq
}

function mergeTraceSteps(current: ServiceTraceStep[], incoming: ServiceTraceStep[]): ServiceTraceStep[] {
  if (incoming.length === 0) return current
  const merged = new Map<string, ServiceTraceStep>()
  for (const item of current) {
    merged.set(item.id, item)
  }
  for (const item of incoming) {
    merged.set(item.id, item)
  }
  return [...merged.values()].sort((left, right) => {
    const leftSeq = Number(left.seq_no || 0)
    const rightSeq = Number(right.seq_no || 0)
    if (leftSeq === rightSeq) {
      return Date.parse(left.started_at || '') - Date.parse(right.started_at || '')
    }
    return leftSeq - rightSeq
  })
}

function toV2HistorySessionId(jobId: string): string {
  return `v2job:${String(jobId || '').trim()}`
}

function toUnifiedSingleRunId(sessionId: string): string {
  return `run_s:${String(sessionId || '').trim()}`
}

function toUnifiedMultiRunId(jobId: string): string {
  return `run_m:${String(jobId || '').trim()}`
}

function parseV2HistoryJobId(historyId: string): string | undefined {
  const value = String(historyId || '').trim()
  if (!value.startsWith('v2job:')) return undefined
  return value.slice('v2job:'.length).trim() || undefined
}

function mapRunToV2JobSummary(run: RunSummary): V2JobSummary {
  return {
    id: run.source_id,
    name: run.name,
    problem: run.problem || '',
    mode: run.operation_mode === 'query' ? 'inspection' : run.operation_mode === 'config' ? 'repair' : 'diagnosis',
    status: run.status === 'open' ? 'queued' : run.status,
    phase: (String(run.phase || 'collect') as V2JobSummary['phase']),
    created_at: run.created_at,
    started_at: run.started_at,
    completed_at: run.completed_at,
    updated_at: run.updated_at || run.created_at,
    device_count: run.device_count,
    command_count: 0,
    pending_action_groups: run.pending_actions,
    root_device_id: undefined,
  }
}

function buildHistorySessionItemsFromRuns(runs: RunSummary[]): HistorySessionItem[] {
  const items: HistorySessionItem[] = runs.map((run) => {
    if (run.kind === 'multi') {
      return {
        id: toV2HistorySessionId(run.source_id),
        source_id: run.source_id,
        run_id: run.id,
        kind: 'multi',
        host: `多设备协同(${run.device_count})`,
        device_name: run.name || `任务 ${run.source_id.slice(0, 8)}...`,
        protocol: 'ssh',
        automation_level: run.automation_level,
        operation_mode: run.operation_mode,
        status: run.status,
        created_at: run.created_at,
        updated_at: run.updated_at,
        problem: run.problem,
        device_count: run.device_count,
        command_count: undefined,
        sop_extracted: run.sop_extracted,
        sop_draft_count: run.sop_draft_count,
        sop_published_count: run.sop_published_count,
        primary_sop_id: run.primary_sop_id,
      }
    }
    return {
      id: run.source_id,
      source_id: run.source_id,
      run_id: run.id,
      kind: 'single',
      host: run.device_hosts[0] || '-',
      device_name: run.name,
      protocol: run.protocol || 'ssh',
      automation_level: run.automation_level,
      operation_mode: run.operation_mode,
      status: run.status,
      created_at: run.created_at,
      updated_at: run.updated_at,
      problem: run.problem,
      device_count: run.device_count,
      command_count: undefined,
      sop_extracted: run.sop_extracted,
      sop_draft_count: run.sop_draft_count,
      sop_published_count: run.sop_published_count,
      primary_sop_id: run.primary_sop_id,
    }
  })
  return items.sort((left, right) => {
    const l = Date.parse(String(left.created_at || ''))
    const r = Date.parse(String(right.created_at || ''))
    const lt = Number.isFinite(l) ? l : 0
    const rt = Number.isFinite(r) ? r : 0
    return rt - lt
  })
}

function toSopEditor(entry: SOPArchiveEntry): SOPUpsertRequest {
  return {
    name: entry.name || '',
    summary: entry.summary || '',
    usage_hint: entry.usage_hint || '',
    trigger_keywords: [...(entry.trigger_keywords || [])],
    vendor_tags: [...(entry.vendor_tags || [])],
    version_signatures: [...(entry.version_signatures || [])],
    preconditions: [...(entry.preconditions || [])],
    anti_conditions: [...(entry.anti_conditions || [])],
    evidence_goals: [...(entry.evidence_goals || [])],
    command_templates: (entry.command_templates || []).map((item) => ({
      vendor: item.vendor || 'generic',
      commands: [...(item.commands || [])],
    })),
    fallback_commands: [...(entry.fallback_commands || [])],
    expected_findings: [...(entry.expected_findings || [])],
    source_run_ids: [...(entry.source_run_ids || [])],
    generated_by_model: entry.generated_by_model,
    generated_by_prompt_version: entry.generated_by_prompt_version,
    review_notes: entry.review_notes,
  }
}

function parseTagInput(raw: string): string[] {
  return String(raw || '')
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinTagInput(values?: string[]): string {
  return (values || []).join('\n')
}

function parseCommandTemplatesInput(raw: string) {
  return String(raw || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [vendorPart, commandsPart] = line.includes('|') ? line.split('|', 2) : ['generic', line]
      const vendor = String(vendorPart || 'generic').trim() || 'generic'
      const commands = String(commandsPart || '')
        .split(';')
        .map((item) => item.trim())
        .filter(Boolean)
      return { vendor, commands }
    })
    .filter((item) => item.commands.length > 0)
}

function joinCommandTemplatesInput(values?: Array<{ vendor: string; commands: string[] }>): string {
  return (values || [])
    .map((item) => `${item.vendor || 'generic'} | ${(item.commands || []).join(' ; ')}`)
    .join('\n')
}

function parseV2ActionGroupCommandId(commandId: string): string | undefined {
  const value = String(commandId || '').trim()
  if (!value.startsWith('v2ag:')) return undefined
  return value.slice(5).trim() || undefined
}

function splitHostsFromSnapshot(timeline: Timeline): string[] {
  return String(timeline.session.device?.host || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
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
  const constraintLabels = summarizeCommandConstraintLabels(row.members)
  const constraintReason = summarizeCommandConstraintReasons(row.members)
  const header = [
    `标题: ${row.title}`,
    `状态: ${row.status}`,
    `风险: ${riskLabel(row.risk_level)}`,
    constraintLabels ? `约束: ${constraintLabels}` : '',
    constraintReason ? `约束说明: ${constraintReason}` : '',
    `步骤: ${row.stepLabel}`,
  ].join('\n')
  const sequence = renderCommandSequence(row.members)
  const body = renderCommandRowOutput(row)
  return `${header}\n\n命令序列（按执行顺序）:\n${sequence}\n\n输出:\n${body}`
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

function renderCommandSequence(items: CommandExecution[]): string {
  const ordered = [...items].sort((a, b) => a.step_no - b.step_no)
  if (ordered.length === 0) return '-'
  return ordered
    .map((item, index) => `${index + 1}. #${item.step_no} [${item.status}] ${item.command}`)
    .join('\n')
}

function getMaxStepNo(commands: CommandExecution[]): number {
  let max = 0
  for (const command of commands) {
    if (command.step_no > max) max = command.step_no
  }
  return max
}

function buildContinuePreview(commands: CommandExecution[], summary?: DiagnosisSummary): ContinuePreview {
  void summary
  const pendingCommands = resolveLatestPendingCommands(commands)
  const previewItems = buildContinuePreviewItems(pendingCommands)
  if (pendingCommands.length > 0) {
    return {
      source: 'pending',
      items: previewItems,
      totalCommands: previewItems.reduce((sum, item) => sum + item.commandLines.length, 0),
    }
  }
  return {
    source: 'none',
    items: [],
    totalCommands: 0,
  }
}

function extractLatestAiDecision(traceSteps: ServiceTraceStep[]): 'run_command' | 'final' | undefined {
  const ordered = [...traceSteps].sort((a, b) => {
    const aTime = parseSortTime(a.completed_at || a.started_at || '', a.seq_no)
    const bTime = parseSortTime(b.completed_at || b.started_at || '', b.seq_no)
    if (aTime === bTime) return a.seq_no - b.seq_no
    return aTime - bTime
  })
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    const step = ordered[index]
    if (step.step_type !== 'llm_response' && step.step_type !== 'llm_final' && step.step_type !== 'llm_plan') continue
    const payload = isTraceRecord(step.detail_payload) ? step.detail_payload : undefined
    const detailText = `${step.detail || ''} ${step.title || ''}`.toLowerCase()
    if (detailText.includes('decision=final')) return 'final'
    if (detailText.includes('decision=run_command')) return 'run_command'
    const candidates = [
      payload,
      isTraceRecord(payload?.llm) ? payload?.llm : undefined,
      isTraceRecord(payload?.to_ai) ? payload?.to_ai : undefined,
      isTraceRecord(payload?.plan) ? payload?.plan : undefined,
      isTraceRecord(payload?.ai_response_parsed) ? payload?.ai_response_parsed : undefined,
      isTraceRecord(payload?.final_summary) ? payload?.final_summary : undefined,
    ].filter((item): item is Record<string, unknown> => Boolean(item))
    for (const candidate of candidates) {
      const decision = String(candidate.decision || '').trim().toLowerCase()
      if (decision === 'final' || decision === 'run_command') {
        return decision
      }
    }
  }
  return undefined
}

function buildContinuePreviewItems(commands: CommandExecution[]): ContinuePreviewItem[] {
  const ordered = [...commands].sort((a, b) => a.step_no - b.step_no)
  return ordered.map((item) => ({
    key: item.id,
    step_no: item.step_no,
    title: item.title,
    risk_level: item.risk_level,
    status: item.status,
    commandLines: splitContinueCommandLines(item.command),
  }))
}

function splitContinueCommandLines(value: string): string[] {
  const raw = String(value || '').trim()
  if (!raw) return ['(空命令)']
  const pieces = raw.split(/\s*;\s*/).map((item) => item.trim()).filter(Boolean)
  if (pieces.length > 1) return pieces
  return [raw]
}

function resolveLatestPendingCommands(commands: CommandExecution[]): CommandExecution[] {
  const ordered = [...commands].sort((a, b) => a.step_no - b.step_no)
  if (ordered.length === 0) return []
  let end = ordered.length - 1
  while (end >= 0 && ordered[end].status !== 'pending_confirm') {
    end -= 1
  }
  if (end < 0) return []
  const batchId = ordered[end].batch_id
  if (batchId) {
    return ordered.filter((item) => item.status === 'pending_confirm' && item.batch_id === batchId)
  }
  let start = end
  while (start - 1 >= 0 && ordered[start - 1].status === 'pending_confirm' && !ordered[start - 1].batch_id) {
    start -= 1
  }
  return ordered.slice(start, end + 1)
}

function upsertContinueExecutionState(
  state: ContinueExecutionState | null,
  command: CommandExecution,
  baselineStepNo: number,
): ContinueExecutionState | null {
  if (!state) return state
  const nextCommand: ContinueExecutionCommand = {
    id: command.id,
    step_no: command.step_no,
    title: command.title,
    risk_level: command.risk_level,
    command: command.command,
    status: command.status,
  }
  const existingIndex = state.observedCommands.findIndex((item) => item.id === command.id)
  let observedCommands = state.observedCommands
  if (existingIndex >= 0) {
    observedCommands = [...state.observedCommands]
    observedCommands[existingIndex] = nextCommand
  } else {
    observedCommands = [...state.observedCommands, nextCommand]
  }
  observedCommands.sort((left, right) => left.step_no - right.step_no)
  return {
    ...state,
    baselineStepNo,
    observedCommands,
  }
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

function commandConstraintLabel(command: Pick<CommandExecution, 'constraint_source' | 'status'>): string {
  const source = String(command.constraint_source || '').trim().toLowerCase()
  if (!source) {
    if (command.status === 'pending_confirm') return '待人工确认'
    return ''
  }
  const labels: Record<string, string> = {
    capability_block: '能力规则拦截',
    capability_rewrite: '能力规则改写',
    mode_scope_block: '模式范围拦截',
    policy_block: '阻断规则拦截',
    risk_baseline_block: '风险基线拦截',
    risk_confirm: '高风险需确认',
    policy_confirm: '未命中放行需确认',
    policy_allow: '放行规则允许',
    full_auto_allow: '高风险自动执行',
    default_allow: '默认允许',
  }
  return labels[source] || source
}

function commandConstraintReason(
  command: Pick<CommandExecution, 'constraint_reason' | 'capability_reason' | 'error'>,
): string {
  const reason = String(command.constraint_reason || '').trim()
  if (reason) return reason
  const capabilityReason = String(command.capability_reason || '').trim()
  if (capabilityReason) return capabilityReason
  return ''
}

function summarizeCommandConstraintLabels(commands: CommandExecution[]): string {
  const labels = Array.from(new Set(commands.map((item) => commandConstraintLabel(item)).filter(Boolean)))
  return labels.join('；')
}

function summarizeCommandConstraintReasons(commands: CommandExecution[]): string {
  const reasons = Array.from(new Set(commands.map((item) => commandConstraintReason(item)).filter(Boolean)))
  return reasons.join('；')
}

function constraintTagClass(command: Pick<CommandExecution, 'constraint_source' | 'status'>): string {
  const source = String(command.constraint_source || '').trim().toLowerCase()
  if (source.includes('block')) return 'err'
  if (source.includes('confirm') || command.status === 'pending_confirm') return 'warn'
  if (source.includes('allow') || source.includes('rewrite')) return 'ok'
  return 'idle'
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
      jump_host: (hit.jump_host || '').trim() || undefined,
      jump_port: Number.isFinite(Number(hit.jump_port)) ? Number(hit.jump_port) : undefined,
      jump_username: (hit.jump_username || '').trim() || undefined,
      jump_password: (hit.jump_password || '').trim() || undefined,
      api_token: (hit.api_token || '').trim() || undefined,
      updated_at: hit.updated_at,
    }
  } catch {
    // ignore parse errors
  }
  return undefined
}

function cacheDeviceAuth(host: string, auth: DeviceAuthRecord): void {
  const normalizedHost = String(host || '').trim()
  if (!normalizedHost || typeof window === 'undefined') return
  const username = String(auth.username || '').trim()
  const password = String(auth.password || '').trim()
  const jumpHost = String(auth.jump_host || '').trim()
  const jumpPort = Number(auth.jump_port)
  const jumpUsername = String(auth.jump_username || '').trim()
  const jumpPassword = String(auth.jump_password || '').trim()
  const apiToken = String(auth.api_token || '').trim()
  if (!username && !password && !jumpHost && !jumpUsername && !jumpPassword && !apiToken) return
  try {
    const raw = localStorage.getItem(DEVICE_AUTH_CACHE_KEY)
    const parsed = raw ? (JSON.parse(raw) as Record<string, DeviceAuthRecord>) : {}
    parsed[normalizedHost] = {
      username: username || undefined,
      password: password || undefined,
      jump_host: jumpHost || undefined,
      jump_port: Number.isFinite(jumpPort) && jumpPort > 0 ? jumpPort : undefined,
      jump_username: jumpUsername || undefined,
      jump_password: jumpPassword || undefined,
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
      slowestTitle = formatTraceStepTitle(step)
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

function formatTraceStepTitle(step: ServiceTraceStep): string {
  const raw = String(step.title || '').trim()
  if (!raw) return traceTypeLabel(step.step_type)
  let text = raw

  text = text.replace(/^LLM 规划第\s*(\d+)\s*轮$/, '第$1轮规划')
  text = text.replace(/^提交给 AI（第\s*(\d+)\s*轮）$/, '第$1轮提交')
  text = text.replace(/^AI 原始回复（第\s*(\d+)\s*轮）$/, '第$1轮回复')
  text = text.replace(/^\[([^\]]+)\]\s*LLM 规划第\s*(\d+)\s*轮$/, '[$1] 第$2轮规划')
  text = text.replace(/^\[([^\]]+)\]\s*提交给 AI（第\s*(\d+)\s*轮）$/, '[$1] 第$2轮提交')
  text = text.replace(/^\[([^\]]+)\]\s*AI 原始回复（第\s*(\d+)\s*轮）$/, '[$1] 第$2轮回复')
  text = text.replace(/^系统提交上下文（AI计划第\s*(\d+)\s*轮）$/, '第$1轮上下文写入')
  text = text.replace(/^会话上下文快照（第\s*(\d+)\s*轮）$/, '第$1轮上下文快照')
  text = text.replace(/^\[([^\]]+)\]\s*会话上下文快照（第\s*(\d+)\s*轮）$/, '[$1] 第$2轮上下文快照')
  text = text.replace(/^系统提交上下文（会话头）$/, '会话初始化上下文')
  text = text.replace(/^系统提交上下文（命令结果 #(\d+)）$/, '命令结果写入 #$1')
  text = text.replace(/^系统提交上下文（基线汇总）$/, '基线汇总写入')
  text = text.replace(/^SOP 候选已生成$/, 'SOP候选匹配')
  text = text.replace(/^\[([^\]]+)\]\s*SOP档案候选已装载$/, '[$1] SOP候选匹配')
  text = text.replace(/^SOP 引用结果$/, 'SOP引用结果')

  text = text.replace(/^执行前策略判定$/, '命令安全与风险判定')
  text = text.replace(/^执行前会话范围判定$/, '会话模式范围校验')
  text = text.replace(/^执行前命令能力判定（改写）$/, '命令可用性判定（自动改写）')
  text = text.replace(/^执行前命令能力判定（阻断）$/, '命令可用性判定（命中阻断）')
  text = text.replace(/^执行前命令能力判定（跳过权限阻断）$/, '命令可用性判定（跳过权限阻断）')
  text = text.replace(/^执行前命令能力判定（跳过上下文阻断）$/, '命令可用性判定（跳过上下文阻断）')

  text = text.replace(/^会话控制：设备连接会话创建$/, '建立设备连接')
  text = text.replace(/^会话控制：设备连接会话关闭$/, '关闭设备连接')
  text = text.replace(/^会话控制：执行前终止$/, '执行前已停止')
  text = text.replace(/^会话控制：命令等待确认$/, '等待人工确认')
  text = text.replace(/^会话控制：设备连接失败$/, '设备连接失败')
  text = text.replace(/^会话控制：批量执行前终止$/, '命令组执行前已停止')
  text = text.replace(/^会话控制：命令组全部被拦截$/, '命令组全部被策略拦截')
  text = text.replace(/^会话控制：命令组预检查失败$/, '命令组预检查失败')
  text = text.replace(/^会话控制：命令组等待确认$/, '命令组等待人工确认')
  text = text.replace(/^会话控制：命令组设备连接失败$/, '命令组设备连接失败')

  text = text.replace(/^设备执行命令组预检查 \((\d+) 条\)$/, '批量命令预检查执行（$1条）')
  text = text.replace(/^设备执行命令组 \((\d+) 条\)$/, '批量命令执行（$1条）')
  text = text.replace(/^设备执行命令 #(\d+):\s*/, '命令执行 #$1: ')

  text = text.replace('基线探针/版本识别', '基线采集：版本识别')
  text = text.replace('基线画像/设备时钟', '基线采集：设备时间')
  text = text.replace('基线画像/会话权限', '基线采集：权限级别')

  return text
}

function traceStepOrderTime(step: ServiceTraceStep): number {
  const started = Date.parse(String(step.started_at || ''))
  if (!Number.isNaN(started)) return started
  const completed = Date.parse(String(step.completed_at || ''))
  if (!Number.isNaN(completed)) return completed
  return Number.MAX_SAFE_INTEGER
}

function traceStepOrderSeq(step: ServiceTraceStep): number {
  const seq = Number(step.seq_no)
  if (Number.isFinite(seq) && seq > 0) return seq
  return Number.MAX_SAFE_INTEGER
}

function sortTraceStepsByOrder(steps: ServiceTraceStep[]): ServiceTraceStep[] {
  return [...steps].sort((left, right) => {
    const seqDiff = traceStepOrderSeq(left) - traceStepOrderSeq(right)
    if (seqDiff !== 0) return seqDiff
    const timeDiff = traceStepOrderTime(left) - traceStepOrderTime(right)
    if (timeDiff !== 0) return timeDiff
    const leftId = String(left.id || '')
    const rightId = String(right.id || '')
    return leftId.localeCompare(rightId)
  })
}

function resolveActiveFlowStepId(steps: ServiceTraceStep[]): string | undefined {
  if (steps.length === 0) return undefined
  const ordered = sortTraceStepsByOrder(steps)
  const running = [...ordered].reverse().find((step) => step.status === 'running')
  if (running) return running.id
  const pending = [...ordered].reverse().find((step) => step.status === 'pending_confirm')
  if (pending) return pending.id
  return ordered[ordered.length - 1].id
}

function buildFlowLanes(steps: ServiceTraceStep[], mode: FlowLayoutMode): FlowLane[] {
  const laneOrder = ['user', 'context', 'runtime', 'ai_submit', 'ai_feedback', 'plan', 'policy', 'control', 'execute', 'evidence', 'summary']
  const labels: Record<string, string> = {
    user: '用户输入',
    context: '上下文构建',
    runtime: '运行时约束',
    ai_submit: '提交给AI',
    ai_feedback: 'AI反馈',
    plan: '规划决策',
    policy: '策略判定',
    execute: '设备执行',
    evidence: '证据处理',
    summary: '总结输出',
    control: '会话控制',
  }
  if (steps.length === 0) {
    return laneOrder.map((key) => ({
      key,
      label: labels[key] || key,
      actor: traceLaneActorLabel(key),
      steps: [],
      realCount: 0,
    }))
  }

  const ordered = sortTraceStepsByOrder(steps)
  const lanes = new Map<string, Array<ServiceTraceStep | null>>()
  for (const key of laneOrder) lanes.set(key, [])

  if (mode === 'stair') {
    // Strict stair mode: step N occupies row N.
    for (const step of ordered) {
      const laneKey = traceLaneKey(step)
      for (const key of laneOrder) {
        const column = lanes.get(key)
        if (!column) continue
        column.push(key === laneKey ? step : null)
      }
    }
  } else {
    // Compact mode: stack steps tightly inside each lane (no placeholders).
    const grouped = new Map<string, ServiceTraceStep[]>()
    for (const key of laneOrder) grouped.set(key, [])
    for (const step of ordered) {
      const laneKey = traceLaneKey(step)
      const list = grouped.get(laneKey) || []
      list.push(step)
      grouped.set(laneKey, list)
    }
    for (const key of laneOrder) {
      const column = lanes.get(key)
      if (!column) continue
      const items = grouped.get(key) || []
      for (const item of items) column.push(item)
    }
  }

  return laneOrder.map((key) => ({
    key,
    label: labels[key] || key,
    actor: traceLaneActorLabel(key),
    steps: lanes.get(key) || [],
    realCount: (lanes.get(key) || []).filter((item) => item !== null).length,
  }))
}

function buildTraceLaneCounts(steps: ServiceTraceStep[]): Record<string, number> {
  const counts: Record<string, number> = {
    user: 0,
    context: 0,
    runtime: 0,
    ai_submit: 0,
    ai_feedback: 0,
    plan: 0,
    policy: 0,
    control: 0,
    execute: 0,
    evidence: 0,
    summary: 0,
  }
  for (const step of steps) {
    const laneKey = traceLaneKey(step)
    counts[laneKey] = (counts[laneKey] || 0) + 1
  }
  return counts
}

function traceNodeRelationClass(step: ServiceTraceStep, selected?: ServiceTraceStep): string {
  if (!selected) return ''
  if (step.id === selected.id) return 'selected-focus'
  return ''
}

function traceConstraintLabel(step: ServiceTraceStep): string {
  if (step.step_type === 'scope_decision') return '模式范围约束'
  if (step.step_type === 'capability_decision') {
    const lowered = `${step.title} ${step.detail || ''}`.toLowerCase()
    if (lowered.includes('改写') || lowered.includes('rewrite')) return '能力规则改写'
    if (lowered.includes('阻断') || lowered.includes('block')) return '能力规则拦截'
    if (lowered.includes('跳过') || lowered.includes('skip')) return '能力规则跳过'
    return '能力约束'
  }
  if (step.step_type !== 'policy_decision') return ''

  const source = traceConstraintSourceFromDetail(step.detail)
  const labels: Record<string, string> = {
    policy_block: '阻断规则拦截',
    risk_baseline_block: '风险基线拦截',
    risk_confirm: '高风险需确认',
    policy_confirm: '未命中放行需确认',
    policy_allow: '放行规则允许',
    full_auto_allow: '高风险自动执行',
    default_allow: '默认允许',
  }
  return labels[source] || '策略约束'
}

function traceConstraintSourceFromDetail(detail?: string): string {
  const text = String(detail || '')
  if (!text.trim()) return ''
  const matched = text.match(/(?:^|[;\s])source=([a-z_]+)/i)
  if (!matched) return ''
  return String(matched[1] || '').trim().toLowerCase()
}

function traceLaneKey(stepOrType: ServiceTraceStep | string): string {
  const stepType = typeof stepOrType === 'string' ? stepOrType : stepOrType.step_type
  const detail = typeof stepOrType === 'string' ? '' : String(stepOrType.detail || '')
  if (stepType === 'user_input') return 'user'
  if (stepType === 'ai_context_submit' || stepType === 'context_snapshot') return 'context'
  if (
    stepType === 'sop_candidates_generated'
    || (stepType === 'capability_decision' && detail.includes('planner_context=command_capability'))
    || (stepType === 'capability_decision' && detail.includes('planner_context=filter_capability'))
    || (stepType === 'policy_decision' && detail.includes('planner_context=permission_signal'))
    || (stepType === 'policy_decision' && detail.includes('planner_context=output_compaction'))
  ) return 'runtime'
  if (stepType === 'llm_request') return 'ai_submit'
  if (stepType === 'llm_response' || stepType === 'sop_referenced_by_ai') return 'ai_feedback'
  if (
    stepType === 'llm_plan'
    || stepType === 'llm_status'
    || stepType === 'plan_decision'
    || stepType === 'plan_parse'
    || stepType === 'loop_control'
    || stepType === 'sop_reference_outcome'
  ) return 'plan'
  if (stepType === 'policy_decision' || stepType === 'capability_decision' || stepType === 'scope_decision') return 'policy'
  if (stepType === 'command_execution' || stepType === 'command_confirm_execution') return 'execute'
  if (stepType === 'evidence_parse') return 'evidence'
  if (stepType === 'llm_final') return 'summary'
  if (stepType === 'session_control' || stepType === 'session_adapter') return 'control'
  return 'control'
}

function traceLaneActorLabel(laneKey: string): string {
  if (laneKey === 'user') return '用户'
  if (laneKey === 'ai_feedback' || laneKey === 'summary') return 'AI'
  if (laneKey === 'execute') return '设备'
  return '系统'
}

function traceStepActorLabel(stepType: string): string {
  if (stepType === 'user_input') return '用户'
  if (stepType === 'command_execution' || stepType === 'command_confirm_execution') return '设备'
  if (stepType === 'llm_response' || stepType === 'llm_plan' || stepType === 'llm_final' || stepType === 'llm_status' || stepType === 'sop_referenced_by_ai') return 'AI'
  return '系统'
}

function traceActorClass(actor: string): string {
  if (actor === '用户') return 'actor-user'
  if (actor === 'AI') return 'actor-ai'
  if (actor === '设备') return 'actor-device'
  return 'actor-system'
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '-'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function computeTracePlaybackDelay(current: ServiceTraceStep, next: ServiceTraceStep): number {
  const currentDuration = typeof current.duration_ms === 'number' ? current.duration_ms : undefined
  if (typeof currentDuration === 'number') {
    if (currentDuration <= 0) return 320
    return Math.max(420, Math.min(3600, Math.round(currentDuration)))
  }

  const nextDuration = typeof next.duration_ms === 'number' ? next.duration_ms : undefined
  if (typeof nextDuration === 'number') {
    if (nextDuration <= 0) return 320
    return Math.max(420, Math.min(3600, Math.round(nextDuration)))
  }

  const currentStarted = Date.parse(String(current.started_at || ''))
  const nextStarted = Date.parse(String(next.started_at || ''))
  if (!Number.isNaN(currentStarted) && !Number.isNaN(nextStarted) && nextStarted > currentStarted) {
    const delta = nextStarted - currentStarted
    return Math.max(420, Math.min(3600, delta))
  }

  return 900
}

function isTraceRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function stringifyTraceValue(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function collectTraceCommandResults(payload: Record<string, unknown>): string {
  const lines: string[] = []
  const pushCommand = (record: Record<string, unknown>) => {
    const stepNo = record.step_no !== undefined ? `#${String(record.step_no)} ` : ''
    const cmd = String(record.effective_command || record.command || '').trim()
    const status = String(record.status || '').trim()
    const error = String(record.error || '').trim()
    const output = String(record.output || '').trim()
    if (!cmd) return
    const header = `${stepNo}${cmd}${status ? ` [${status}]` : ''}`
    if (output) {
      lines.push(`${header}\n${output}`)
      return
    }
    if (error) {
      lines.push(`${header}\n${error}`)
      return
    }
    lines.push(header)
  }

  const candidates: Array<unknown> = []
  if (isTraceRecord(payload.command)) candidates.push(payload.command)
  const commandArrays = ['commands', 'precheck_commands', 'pending_commands']
  for (const key of commandArrays) {
    const value = payload[key]
    if (Array.isArray(value)) candidates.push(...value)
  }
  for (const item of candidates) {
    if (!isTraceRecord(item)) continue
    pushCommand(item)
  }
  return lines.join('\n\n').trim()
}

function collectTraceMessageContents(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => (isTraceRecord(item) ? String(item.content || '').trim() : ''))
    .filter(Boolean)
}

function extractPrefixedLines(texts: string[], prefixes: string[]): string[] {
  const rows: string[] = []
  for (const text of texts) {
    for (const line of String(text || '').split('\n')) {
      const trimmed = line.trim()
      if (!trimmed) continue
      if (prefixes.some((prefix) => trimmed.startsWith(prefix))) {
        rows.push(trimmed)
      }
    }
  }
  return rows
}

function summarizePlannerContextText(text: string, maxLines = 5): string {
  const lines = String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length <= maxLines) return lines.join('\n')
  return `${lines.slice(0, maxLines).join('\n')}\n...共 ${lines.length} 行`
}

function extractSopCandidateSummary(texts: string[]): string {
  const source = texts.find((item) => item.includes('SOP档案候选')) || ''
  if (!source) return ''
  const rows = source
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('- '))
    .slice(0, 4)
  if (rows.length === 0) return '已生成 SOP 候选，但当前未附加具体条目。'
  return rows.join('\n')
}

function extractCommandObjects(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (isTraceRecord(item)) return [item]
    if (typeof item === 'string' && item.trim()) return [{ command: item.trim() }]
    return []
  })
}

function detectCommandCompaction(commands: Array<Record<string, unknown>>): string {
  const commandTexts = commands
    .map((item) => String(item.command || item.effective_command || '').trim().toLowerCase())
    .filter(Boolean)
  if (commandTexts.length === 0) return ''
  const compacted = commandTexts.filter((text) =>
    text.includes('| include')
    || text.includes('| exclude')
    || text.includes('| begin')
    || text.includes('| section')
    || text.includes('| match')
    || text.includes('| grep')
    || text.includes('| count')
    || text.includes(' count ')
  )
  if (compacted.length === 0) return '本轮未使用过滤管道，命令已尽量保持为目标对象直查。'
  return `本轮已采用输出压缩命令 ${compacted.length} 条：\n${compacted.join('\n')}`
}

function findPreviousSignalStep(
  steps: ServiceTraceStep[],
  current: ServiceTraceStep,
  predicate: (step: ServiceTraceStep) => boolean,
): ServiceTraceStep | undefined {
  const currentSeq = Number(current.seq_no || 0)
  const ordered = sortTraceStepsByOrder(steps)
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    const item = ordered[index]
    if (item.id === current.id) continue
    if (Number(item.seq_no || 0) >= currentSeq) continue
    if (predicate(item)) return item
  }
  return undefined
}

function buildRuntimeSignalSections(step: ServiceTraceStep, allSteps: ServiceTraceStep[] = []): TraceDetailSection[] {
  const payload = isTraceRecord(step.detail_payload) ? step.detail_payload : undefined
  if (!payload) return []
  const stepType = String(step.step_type || '').trim().toLowerCase()
  const isAiStep = ['llm_request', 'llm_response', 'llm_plan', 'llm_final'].includes(stepType)
  const sections: TraceDetailSection[] = []

  if (isAiStep) {
    const requestPayload = isTraceRecord(payload.request_payload)
      ? payload.request_payload
      : isTraceRecord(payload.to_ai) && isTraceRecord(payload.to_ai.request_payload)
        ? payload.to_ai.request_payload
        : undefined
    const requestMessages = collectTraceMessageContents(payload.request_messages)
    const toAiMessages = isTraceRecord(payload.to_ai) ? collectTraceMessageContents(payload.to_ai.request_messages) : []
    const combinedMessages = [...requestMessages, ...toAiMessages]
    const signalLines: string[] = []

    const device = isTraceRecord(payload.device)
      ? payload.device
      : requestPayload && isTraceRecord(requestPayload.device)
        ? requestPayload.device
        : requestPayload && isTraceRecord(requestPayload.session)
          ? requestPayload.session
          : undefined
    const vendor = String(device?.vendor || '').trim()
    const versionSignature = String(device?.version_signature || '').trim()
    if (vendor || versionSignature) {
      signalLines.push(`设备画像: ${vendor || 'unknown'}${versionSignature ? ` · ${versionSignature}` : ''}`)
    }

    const sessionMode = String(payload.session_mode || requestPayload?.session_mode || '').trim()
    const automationLevel = String(payload.automation_level || requestPayload?.automation_level || '').trim()
    if (sessionMode || automationLevel) {
      signalLines.push(`会话边界: mode=${sessionMode || '-'} ; automation=${automationLevel || '-'}`)
    }

    const familyHints = extractPrefixedLines(combinedMessages, ['命令家族约束:'])
    if (familyHints.length > 0) signalLines.push(...familyHints)

    const permissionHints = extractPrefixedLines(combinedMessages, ['权限探测状态:', '权限动作建议:'])
    if (permissionHints.length > 0) {
      signalLines.push(...permissionHints)
    } else {
      const permissionStep = findPreviousSignalStep(
        allSteps,
        step,
        (item) =>
          item.step_type === 'policy_decision'
          && String(item.detail || '').includes('planner_context=permission_signal')
          && isTraceRecord(item.detail_payload)
          && Boolean(item.detail_payload.planner_context),
      )
      if (permissionStep && isTraceRecord(permissionStep.detail_payload)) {
        signalLines.push(`权限信号:\n${summarizePlannerContextText(String(permissionStep.detail_payload.planner_context || ''))}`)
      }
    }

    const plannerContext = String(requestPayload?.planner_context || '').trim()
    const filterHints = extractPrefixedLines(combinedMessages, ['过滤语法建议:', '已验证可用过滤:', '已验证失败过滤:', '当前目标对象:', '过滤规则:'])
    if (filterHints.length > 0) {
      signalLines.push(`过滤语法能力:\n${filterHints.join('\n')}`)
    } else {
      const filterStep = findPreviousSignalStep(
        allSteps,
        step,
        (item) =>
          item.step_type === 'capability_decision'
          && String(item.detail || '').includes('planner_context=filter_capability')
          && isTraceRecord(item.detail_payload)
          && Boolean(item.detail_payload.planner_context),
      )
      if (filterStep && isTraceRecord(filterStep.detail_payload)) {
        signalLines.push(`过滤语法能力:\n${summarizePlannerContextText(String(filterStep.detail_payload.planner_context || ''))}`)
      }
    }

    const compactionHints = extractPrefixedLines(combinedMessages, ['输出压缩状态:', '过长回显命令:', '输出压缩建议:'])
    if (compactionHints.length > 0) {
      signalLines.push(`输出压缩信号:\n${compactionHints.join('\n')}`)
    } else {
      const compactionStep = findPreviousSignalStep(
        allSteps,
        step,
        (item) =>
          item.step_type === 'policy_decision'
          && String(item.detail || '').includes('planner_context=output_compaction')
          && isTraceRecord(item.detail_payload)
          && Boolean(item.detail_payload.planner_context),
      )
      if (compactionStep && isTraceRecord(compactionStep.detail_payload)) {
        signalLines.push(`输出压缩信号:\n${summarizePlannerContextText(String(compactionStep.detail_payload.planner_context || ''))}`)
      }
    }

    if (plannerContext) {
      signalLines.push(`运行时约束摘要:\n${summarizePlannerContextText(plannerContext)}`)
    } else {
      const capabilityStep = findPreviousSignalStep(
        allSteps,
        step,
        (item) =>
          item.step_type === 'capability_decision'
          && String(item.detail || '').includes('planner_context=command_capability')
          && isTraceRecord(item.detail_payload)
          && Boolean(item.detail_payload.planner_context),
      )
      if (capabilityStep && isTraceRecord(capabilityStep.detail_payload)) {
        signalLines.push(`命令能力规则:\n${summarizePlannerContextText(String(capabilityStep.detail_payload.planner_context || ''))}`)
      }
    }

    const sopSummary = extractSopCandidateSummary(combinedMessages)
    if (sopSummary) {
      signalLines.push(`SOP候选:\n${sopSummary}`)
    } else {
      const sopStep = findPreviousSignalStep(allSteps, step, (item) => item.step_type === 'sop_candidates_generated')
      if (sopStep) {
        signalLines.push('SOP候选: 本轮已匹配到候选 SOP，详情见相邻上下文步骤。')
      }
    }

    if (signalLines.length > 0) {
      sections.push({
        key: 'runtime-signals',
        title: '本轮附加给 AI 的信号',
        body: signalLines.join('\n\n'),
      })
    }

    const parsedResponse = isTraceRecord(payload.ai_response_parsed)
      ? payload.ai_response_parsed
      : isTraceRecord(payload.plan)
        ? payload.plan
        : isTraceRecord(payload.final_summary)
          ? payload.final_summary
          : isTraceRecord(payload.to_ai) && isTraceRecord(payload.to_ai.parsed_response)
            ? payload.to_ai.parsed_response
            : isTraceRecord(payload.llm) && isTraceRecord(payload.llm.parsed_response)
              ? payload.llm.parsed_response
              : undefined
    if (parsedResponse) {
      const commandObjects = extractCommandObjects(parsedResponse.commands)
      const basisLines: string[] = []
      const reason = String(parsedResponse.reason || '').trim()
      const evidenceGoal = String(parsedResponse.evidence_goal || '').trim()
      const whyUseThisSop = String(parsedResponse.why_use_this_sop || '').trim()
      const sopRefs = Array.isArray(parsedResponse.sop_refs) ? parsedResponse.sop_refs.map((item) => String(item).trim()).filter(Boolean) : []
      if (reason) basisLines.push(`规划原因: ${reason}`)
      if (evidenceGoal) basisLines.push(`目标证据: ${evidenceGoal}`)
      if (sopRefs.length > 0) basisLines.push(`引用SOP: ${sopRefs.join(', ')}`)
      if (whyUseThisSop) basisLines.push(`引用原因: ${whyUseThisSop}`)
      const compaction = detectCommandCompaction(commandObjects)
      if (compaction) basisLines.push(`输出压缩策略: ${compaction}`)
      if (basisLines.length > 0) {
        sections.push({
          key: 'command-basis',
          title: '本轮命令生成依据',
          body: basisLines.join('\n\n'),
        })
      }
    }
  }

  if (
    step.step_type === 'sop_candidates_generated'
    || (step.step_type === 'capability_decision' && String(step.detail || '').includes('planner_context=command_capability'))
    || (step.step_type === 'capability_decision' && String(step.detail || '').includes('planner_context=filter_capability'))
    || (step.step_type === 'policy_decision' && String(step.detail || '').includes('planner_context=permission_signal'))
    || (step.step_type === 'policy_decision' && String(step.detail || '').includes('planner_context=output_compaction'))
  ) {
    const payloadText = isTraceRecord(step.detail_payload) && step.detail_payload.planner_context
      ? String(step.detail_payload.planner_context)
      : ''
    if (payloadText.trim()) {
      sections.push({
        key: 'signal-source',
        title: '运行时约束内容',
        body: payloadText.trim(),
      })
    }
  }

  return sections
}

function buildTraceDetailSections(step: ServiceTraceStep, allSteps: ServiceTraceStep[] = []): TraceDetailSection[] {
  const sections: TraceDetailSection[] = []
  const payload = isTraceRecord(step.detail_payload) ? step.detail_payload : undefined
  const extra = payload && isTraceRecord(payload.extra) ? payload.extra : undefined
  const stepType = String(step.step_type || '').trim().toLowerCase()
  const isAiSubmitStep = stepType === 'llm_request' || stepType === 'ai_context_submit' || stepType === 'context_snapshot'
  const isAiFeedbackStep = stepType === 'llm_response' || stepType === 'llm_plan' || stepType === 'llm_final'

  if (step.detail) {
    sections.push({
      key: 'summary',
      title: '摘要',
      body: step.detail,
    })
  }

  if (!payload) return sections

  const promptText = stringifyTraceValue(
    payload.system_prompt
    || payload.final_prompt
    || (isTraceRecord(payload.to_ai) ? (payload.to_ai.system_prompt || payload.to_ai.final_prompt) : ''),
  ).trim()
  if (promptText && isAiSubmitStep) {
    sections.push({
      key: 'prompt',
      title: '引用提示词',
      body: promptText,
    })
  }

  const userInput = String(payload.user_input || payload.user_problem || '').trim()
  if (userInput) {
    sections.push({
      key: 'user-input',
      title: '用户输入',
      body: userInput,
    })
  }

  const commandScript = stringifyTraceValue(payload.command || payload.commands || payload.precheck_commands || '')
  if (commandScript.trim()) {
    sections.push({
      key: 'device-script',
      title: '设备运行脚本/命令',
      body: commandScript,
    })
  }

  const commandResults = collectTraceCommandResults(payload)
  if (commandResults) {
    sections.push({
      key: 'device-result',
      title: '设备返回结果',
      body: commandResults,
    })
  }
  const fullCommandFromExtra = extra && isTraceRecord(extra.command_full) ? extra.command_full : undefined
  if (fullCommandFromExtra) {
    sections.push({
      key: 'device-full',
      title: '设备返回结果(完整)',
      body: stringifyTraceValue(fullCommandFromExtra),
    })
  }

  const toAiPayload: Record<string, unknown> = {}
  if (payload.system_prompt !== undefined) toAiPayload.system_prompt = payload.system_prompt
  if (payload.final_prompt !== undefined) toAiPayload.final_prompt = payload.final_prompt
  if (payload.to_ai_context !== undefined) toAiPayload.to_ai_context = payload.to_ai_context
  if (payload.source !== undefined) toAiPayload.source = payload.source
  if (payload.context_count !== undefined) toAiPayload.context_count = payload.context_count
  if (extra !== undefined) toAiPayload.extra = extra
  if (payload.to_ai !== undefined) toAiPayload.to_ai = payload.to_ai
  if (payload.llm !== undefined) toAiPayload.llm = payload.llm
  if (payload.request_payload !== undefined) toAiPayload.request_payload = payload.request_payload
  if (payload.request_messages !== undefined) toAiPayload.request_messages = payload.request_messages
  if (Object.keys(toAiPayload).length > 0 && isAiSubmitStep) {
    sections.push({
      key: 'to-ai',
      title: '提交给 AI 的内容',
      body: stringifyTraceValue(toAiPayload),
    })
  }

  const aiFeedbackPayload: Record<string, unknown> = {}
  if (payload.ai_response_parsed !== undefined) aiFeedbackPayload.ai_response_parsed = payload.ai_response_parsed
  if (payload.plan !== undefined) aiFeedbackPayload.plan = payload.plan
  if (payload.final_summary !== undefined) aiFeedbackPayload.final_summary = payload.final_summary
  if (isTraceRecord(payload.to_ai)) {
    if (payload.to_ai.raw_response !== undefined) aiFeedbackPayload.raw_response = payload.to_ai.raw_response
    if (payload.to_ai.parsed_response !== undefined) aiFeedbackPayload.parsed_response = payload.to_ai.parsed_response
    if (payload.to_ai.error !== undefined) aiFeedbackPayload.error = payload.to_ai.error
  }
  if (isTraceRecord(payload.llm)) {
    if (payload.llm.raw_response !== undefined) aiFeedbackPayload.raw_response = payload.llm.raw_response
    if (payload.llm.parsed_response !== undefined) aiFeedbackPayload.parsed_response = payload.llm.parsed_response
    if (payload.llm.error !== undefined) aiFeedbackPayload.error = payload.llm.error
  }
  if (Object.keys(aiFeedbackPayload).length > 0 && isAiFeedbackStep) {
    sections.push({
      key: 'ai-feedback',
      title: 'AI 真实反馈',
      body: stringifyTraceValue(aiFeedbackPayload),
    })
  }

  sections.push(...buildRuntimeSignalSections(step, allSteps))

  sections.push({
    key: 'raw-payload',
    title: '节点原始记录(JSON)',
    body: stringifyTraceValue(payload),
  })
  return sections
}

function traceTypeLabel(stepType: string): string {
  if (stepType === 'user_input') return '用户请求'
  if (stepType === 'context_snapshot') return '上下文快照'
  if (stepType === 'ai_context_submit') return '系统提交上下文'
  if (stepType === 'sop_candidates_generated') return 'SOP候选匹配'
  if (stepType === 'llm_request') return '提交给 AI'
  if (stepType === 'llm_response') return 'AI 原始回复'
  if (stepType === 'sop_referenced_by_ai') return 'AI引用SOP'
  if (stepType === 'sop_reference_outcome') return 'SOP引用结果'
  if (stepType === 'llm_plan') return 'LLM 规划'
  if (stepType === 'llm_final') return 'LLM 总结'
  if (stepType === 'llm_status') return 'LLM 可用性'
  if (stepType === 'plan_decision') return '流程判定'
  if (stepType === 'plan_parse') return '计划解析'
  if (stepType === 'loop_control') return '循环控制'
  if (stepType === 'policy_decision') return '策略判定'
  if (stepType === 'capability_decision') return '能力判定'
  if (stepType === 'scope_decision') return '模式范围判定'
  if (stepType === 'evidence_parse') return '回显结果提炼'
  if (stepType === 'command_execution') return '命令执行'
  if (stepType === 'command_confirm_execution') return '确认后执行'
  if (stepType === 'session_control') return '会话控制'
  if (stepType === 'session_adapter') return '连接会话'
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
  if (page === 'third_party_keys') return '⌖'
  if (page === 'control') return '⌘'
  if (page === 'command_policy') return '☑'
  if (page === 'sessions') return '☷'
  if (page === 'service_trace') return '⏱'
  if (page === 'sop_library') return '⌗'
  if (page === 'learning') return '⌬'
  if (page === 'lab') return '△'
  return '◎'
}

export default App
