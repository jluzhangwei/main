import { Button, Input, Select, Switch, message as antMessage } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { UIEvent } from 'react'
import { AutomationLevelSelector } from './components/AutomationLevelSelector'
import { DeviceForm } from './components/DeviceForm'
import { TaskModeSelector } from './components/TaskModeSelector'
import {
  configureLlm,
  confirmCommand,
  createSession,
  deleteCommandCapability,
  deleteLlmConfig,
  exportMarkdown,
  getCommandCapability,
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
  upsertCommandCapability,
  v2ApproveActionGroupsBatch,
  v2CancelJob,
  v2CreateApiKey,
  v2CreateJob,
  v2DeleteApiKey,
  v2GetAuditLogs,
  v2GetCommandProfiles,
  v2GetPermissionTemplates,
  v2GetJobTimeline,
  v2ListApiKeys,
  v2QueryJobs,
  v2RejectActionGroupsBatch,
  v2StreamJobEvents,
  v2UpdateApiKey,
  v2UpdateRcaWeights,
  v2UpdateTopology,
  updateCommandPolicy,
  updateRiskPolicy,
  updateSessionCredentials,
  updateSessionAutomation,
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
  ServiceTraceStep,
  SessionListItem,
  SessionResponse,
  Timeline,
  V2ApiKey,
  V2JobActionGroup,
  V2JobEvent,
  V2JobSummary,
  V2JobTimeline,
} from './types'

type PageId =
  | 'workbench'
  | 'v3_jobs'
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
  traceListExpanded?: boolean
  flowLayoutMode?: FlowLayoutMode
}

const UI_STATE_KEY = 'netops_ui_prefs_v1'
const DEVICE_AUTH_CACHE_KEY = 'netops_device_auth_cache_v1'
const V2_API_KEY_CACHE_KEY = 'netops_v2_api_key_v1'

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
const NAV_ITEMS: Array<{ id: PageId; title: string }> = [
  { id: 'workbench', title: '诊断工作台' },
  { id: 'v3_jobs', title: 'V3 任务编排' },
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
  steps: Array<ServiceTraceStep | null>
  realCount: number
}

type FlowLayoutMode = 'compact' | 'stair'

type ContinuePreview = {
  source: 'pending' | 'none'
  commands: string[]
}

type ContinueExecutionCommand = {
  id: string
  step_no: number
  command: string
  status: string
}

type ContinueExecutionState = {
  active: boolean
  baselineStepNo: number
  plannedCommands: string[]
  observedCommands: ContinueExecutionCommand[]
}

type SendOptions = {
  continueExecution?: {
    baselineStepNo: number
  }
}

type V3JobCreateForm = {
  name: string
  problem: string
  mode: 'diagnosis' | 'inspection' | 'repair'
  topology_mode: 'hybrid' | 'external' | 'auto'
  max_gap_seconds: number
  max_device_concurrency: number
  execution_policy: 'stop_on_failure' | 'continue_on_failure' | 'rollback_template'
  devices_json: string
  topology_edges_json: string
  webhook_url: string
  webhook_events: string
  idempotency_key: string
}

type V3RcaWeightsInput = {
  anomaly: number
  timing: number
  topology: number
  change: number
  consistency: number
}

const V3_DEFAULT_PERMISSIONS = [
  'job.read',
  'job.write',
  'command.execute',
  'command.approve',
  'policy.write',
  'audit.read',
]

const V3_DEFAULT_JOB_FORM: V3JobCreateForm = {
  name: 'v3-multi-device-job',
  problem: '请做跨设备根因分析并输出证据链',
  mode: 'diagnosis',
  topology_mode: 'hybrid',
  max_gap_seconds: 300,
  max_device_concurrency: 20,
  execution_policy: 'stop_on_failure',
  devices_json: JSON.stringify(
    [
      {
        host: '192.168.0.102',
        protocol: 'ssh',
        username: '',
        password: '',
      },
    ],
    null,
    2,
  ),
  topology_edges_json: '[]',
  webhook_url: '',
  webhook_events: 'job_created,phase_changed,job_completed,job_failed',
  idempotency_key: '',
}

function App() {
  const [automationLevel, setAutomationLevel] = useState<AutomationLevel>('assisted')
  const [operationMode, setOperationMode] = useState<OperationMode>('diagnosis')
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
  const [v3ApiKeyInput, setV3ApiKeyInput] = useState('')
  const [v3BootstrapApiKey, setV3BootstrapApiKey] = useState('')
  const [v3ApiKeyName, setV3ApiKeyName] = useState('ops-admin')
  const [v3ApiKeyPermissions, setV3ApiKeyPermissions] = useState(V3_DEFAULT_PERMISSIONS.join(','))
  const [v3ApiKeyLoading, setV3ApiKeyLoading] = useState(false)
  const [v3ApiKeys, setV3ApiKeys] = useState<V2ApiKey[]>([])
  const [v3LastCreatedSecret, setV3LastCreatedSecret] = useState('')
  const [v3JobsLoading, setV3JobsLoading] = useState(false)
  const [v3Jobs, setV3Jobs] = useState<V2JobSummary[]>([])
  const [v3JobsTotal, setV3JobsTotal] = useState(0)
  const [v3JobOffset, setV3JobOffset] = useState(0)
  const [v3JobLimit, setV3JobLimit] = useState(20)
  const [v3JobStatusFilter, setV3JobStatusFilter] = useState<'all' | 'queued' | 'running' | 'waiting_approval' | 'executing' | 'completed' | 'failed' | 'cancelled'>('all')
  const [v3JobModeFilter, setV3JobModeFilter] = useState<'all' | 'diagnosis' | 'inspection' | 'repair'>('all')
  const [v3SelectedJobId, setV3SelectedJobId] = useState<string | undefined>(undefined)
  const [v3TimelineLoading, setV3TimelineLoading] = useState(false)
  const [v3Timeline, setV3Timeline] = useState<V2JobTimeline | null>(null)
  const [v3Events, setV3Events] = useState<V2JobEvent[]>([])
  const [v3EventSeq, setV3EventSeq] = useState(0)
  const [v3Streaming, setV3Streaming] = useState(false)
  const [v3SelectedActionIds, setV3SelectedActionIds] = useState<string[]>([])
  const [v3AuditLogs, setV3AuditLogs] = useState<Array<Record<string, unknown>>>([])
  const [v3AuditLoading, setV3AuditLoading] = useState(false)
  const [v3CommandProfiles, setV3CommandProfiles] = useState<Array<Record<string, unknown>>>([])
  const [v3ProfilesLoading, setV3ProfilesLoading] = useState(false)
  const [v3PermissionTemplates, setV3PermissionTemplates] = useState<Record<string, string[]>>({})
  const [v3TopologyEditor, setV3TopologyEditor] = useState('[]')
  const [v3RcaWeights, setV3RcaWeights] = useState<V3RcaWeightsInput>({
    anomaly: 0.3,
    timing: 0.25,
    topology: 0.25,
    change: 0.1,
    consistency: 0.1,
  })
  const [v3JobForm, setV3JobForm] = useState<V3JobCreateForm>(V3_DEFAULT_JOB_FORM)
  const [v3CreateJobLoading, setV3CreateJobLoading] = useState(false)
  const [v3ActionLoading, setV3ActionLoading] = useState(false)
  const v3EventAbortRef = useRef<AbortController | null>(null)
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
  const [sessionVersionSignature, setSessionVersionSignature] = useState('')
  const [sessionHistory, setSessionHistory] = useState<SessionListItem[]>([])
  const [selectedHistorySessionId, setSelectedHistorySessionId] = useState<string | undefined>(undefined)
  const [selectedHistorySnapshot, setSelectedHistorySnapshot] = useState<Timeline | null>(null)
  const [historySnapshotLoading, setHistorySnapshotLoading] = useState(false)
  const historySnapshotCacheRef = useRef<Record<string, Timeline>>({})
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
  const terminalGridRef = useRef<HTMLDivElement | null>(null)
  const streamAbortRef = useRef<AbortController | null>(null)

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
  const flowLanes = useMemo(() => buildFlowLanes(traceSteps, flowLayoutMode), [traceSteps, flowLayoutMode])
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
  const activeFlowLaneKey = useMemo(() => {
    if (!selectedTraceStep) return undefined
    return traceLaneKey(selectedTraceStep.step_type)
  }, [selectedTraceStep])
  const sortedTraceSteps = useMemo(
    () => [...traceSteps].sort((a, b) => a.seq_no - b.seq_no),
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
  const v3PendingActionGroups = useMemo<V2JobActionGroup[]>(
    () =>
      (v3Timeline?.job.action_groups || []).filter(
        (item) => item.status === 'pending_approval',
      ),
    [v3Timeline],
  )
  const v3SelectedJobSummary = useMemo(
    () => v3Jobs.find((item) => item.id === v3SelectedJobId),
    [v3Jobs, v3SelectedJobId],
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
    } catch {
      // ignore local storage parse errors
    }
  }, [])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(V2_API_KEY_CACHE_KEY)
      if (!raw) return
      setV3ApiKeyInput(String(raw))
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
      traceListExpanded,
      flowLayoutMode,
    }
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(payload))
  }, [activePage, rightPanelWidth, terminalSplitRatio, statusCollapsed, directionInput, session?.id, traceListExpanded, flowLayoutMode])

  useEffect(() => {
    try {
      if (v3ApiKeyInput.trim()) {
        localStorage.setItem(V2_API_KEY_CACHE_KEY, v3ApiKeyInput.trim())
      } else {
        localStorage.removeItem(V2_API_KEY_CACHE_KEY)
      }
    } catch {
      // ignore local storage errors
    }
  }, [v3ApiKeyInput])

  useEffect(() => {
    if (commands.length === 0) {
      setSelectedCommandId(undefined)
      return
    }
    setSelectedCommandId(commands[commands.length - 1].id)
  }, [commands])

  useEffect(() => {
    if (activePage !== 'v3_jobs') return
    if (!v3ApiKeyInput.trim()) return
    void refreshV3Jobs()
    void refreshV3ApiKeys()
    void refreshV3AuditLogs()
    void refreshV3CommandProfiles()
    void refreshV3PermissionTemplates()
  }, [activePage, v3ApiKeyInput])

  useEffect(() => {
    if (!v3SelectedJobId || !v3ApiKeyInput.trim()) return
    void refreshV3Timeline(v3SelectedJobId)
  }, [v3SelectedJobId, v3ApiKeyInput])

  useEffect(() => {
    const ids = v3PendingActionGroups.map((item) => item.id)
    setV3SelectedActionIds(ids)
  }, [v3PendingActionGroups])

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
    return () => {
      if (tracePlaybackTimerRef.current !== null) {
        window.clearTimeout(tracePlaybackTimerRef.current)
        tracePlaybackTimerRef.current = null
      }
      v3EventAbortRef.current?.abort()
      v3EventAbortRef.current = null
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
      setSelectedTraceStepId(nextStep.id)
    }, delayMs)
    return () => {
      if (tracePlaybackTimerRef.current !== null) {
        window.clearTimeout(tracePlaybackTimerRef.current)
        tracePlaybackTimerRef.current = null
      }
    }
  }, [tracePlaybackActive, activePage, sortedTraceSteps, selectedTraceIndex])

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
      const items = await listSessions()
      setSessionHistory(items)
    } catch {
      setSessionHistory([])
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

  function parseV3PermissionsInput(raw: string): string[] {
    return String(raw || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
  }

  function ensureV3ApiKey(): string {
    const key = v3ApiKeyInput.trim()
    if (!key) {
      throw new Error('请先输入 V3 API Key')
    }
    return key
  }

  async function refreshV3Jobs() {
    let key = ''
    try {
      key = ensureV3ApiKey()
    } catch {
      setV3Jobs([])
      setV3JobsTotal(0)
      return
    }
    setV3JobsLoading(true)
    try {
      const payload = await v2QueryJobs(key, {
        offset: v3JobOffset,
        limit: v3JobLimit,
        status: v3JobStatusFilter === 'all' ? undefined : v3JobStatusFilter,
        mode: v3JobModeFilter === 'all' ? undefined : v3JobModeFilter,
      })
      setV3Jobs(payload.items)
      setV3JobsTotal(payload.total)
      if (!v3SelectedJobId && payload.items[0]) {
        setV3SelectedJobId(payload.items[0].id)
      } else if (v3SelectedJobId && !payload.items.some((item) => item.id === v3SelectedJobId)) {
        setV3SelectedJobId(payload.items[0]?.id)
      }
    } catch (error) {
      setV3Jobs([])
      setV3JobsTotal(0)
      antMessage.error((error as Error).message || '加载 V3 任务失败')
    } finally {
      setV3JobsLoading(false)
    }
  }

  async function refreshV3Timeline(jobId: string, silent = true) {
    const id = String(jobId || '').trim()
    if (!id) return
    let key = ''
    try {
      key = ensureV3ApiKey()
    } catch {
      return
    }
    setV3TimelineLoading(true)
    try {
      const timeline = await v2GetJobTimeline(key, id)
      setV3Timeline(timeline)
      const sortedEvents = [...timeline.events].sort((a, b) => a.seq_no - b.seq_no)
      setV3Events(sortedEvents)
      const maxSeq = sortedEvents.reduce((max, item) => Math.max(max, item.seq_no), 0)
      setV3EventSeq(maxSeq)
      setV3TopologyEditor(JSON.stringify(timeline.job.causal_edges || [], null, 2))
    } catch (error) {
      if (!silent) {
        antMessage.error((error as Error).message || '加载任务时间线失败')
      }
      setV3Timeline(null)
      setV3Events([])
      setV3EventSeq(0)
    } finally {
      setV3TimelineLoading(false)
    }
  }

  async function refreshV3ApiKeys() {
    let key = ''
    try {
      key = ensureV3ApiKey()
    } catch {
      setV3ApiKeys([])
      return
    }
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

  async function refreshV3AuditLogs() {
    let key = ''
    try {
      key = ensureV3ApiKey()
    } catch {
      setV3AuditLogs([])
      return
    }
    setV3AuditLoading(true)
    try {
      const payload = await v2GetAuditLogs(key, { limit: 100, offset: 0 })
      setV3AuditLogs(payload)
    } catch (error) {
      setV3AuditLogs([])
      antMessage.error((error as Error).message || '加载审计日志失败')
    } finally {
      setV3AuditLoading(false)
    }
  }

  async function refreshV3CommandProfiles() {
    let key = ''
    try {
      key = ensureV3ApiKey()
    } catch {
      setV3CommandProfiles([])
      return
    }
    setV3ProfilesLoading(true)
    try {
      const payload = await v2GetCommandProfiles(key)
      setV3CommandProfiles(payload)
    } catch (error) {
      setV3CommandProfiles([])
      antMessage.error((error as Error).message || '加载命令能力画像失败')
    } finally {
      setV3ProfilesLoading(false)
    }
  }

  async function refreshV3PermissionTemplates() {
    let key = ''
    try {
      key = ensureV3ApiKey()
    } catch {
      setV3PermissionTemplates({})
      return
    }
    try {
      const payload = await v2GetPermissionTemplates(key)
      setV3PermissionTemplates(payload.templates || {})
    } catch (error) {
      setV3PermissionTemplates({})
      antMessage.error((error as Error).message || '加载权限模板失败')
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
        bootstrapApiKey: v3BootstrapApiKey.trim() || undefined,
      })
      setV3LastCreatedSecret(created.api_key)
      setV3ApiKeyInput((prev) => prev.trim() || created.api_key)
      setV3BootstrapApiKey('')
      antMessage.success('V3 API Key 创建成功')
      await refreshV3ApiKeys()
      await refreshV3AuditLogs()
    } catch (error) {
      antMessage.error((error as Error).message || '创建 API Key 失败')
    } finally {
      setV3ApiKeyLoading(false)
    }
  }

  async function handleV3DeleteApiKey(keyId: string) {
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入管理员 API Key')
      return
    }
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
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入管理员 API Key')
      return
    }
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

  async function handleV3CreateJob() {
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入 V3 API Key')
      return
    }
    let devices: Array<Record<string, unknown>> = []
    let topologyEdges: Array<Record<string, unknown>> = []
    try {
      const parsedDevices = JSON.parse(v3JobForm.devices_json)
      if (!Array.isArray(parsedDevices)) {
        throw new Error('devices_json 必须是数组')
      }
      devices = parsedDevices as Array<Record<string, unknown>>
    } catch (error) {
      antMessage.error(`设备 JSON 解析失败: ${(error as Error).message}`)
      return
    }
    try {
      const parsedEdges = JSON.parse(v3JobForm.topology_edges_json || '[]')
      if (!Array.isArray(parsedEdges)) {
        throw new Error('topology_edges_json 必须是数组')
      }
      topologyEdges = parsedEdges as Array<Record<string, unknown>>
    } catch (error) {
      antMessage.error(`拓扑边 JSON 解析失败: ${(error as Error).message}`)
      return
    }
    setV3CreateJobLoading(true)
    try {
      const created = await v2CreateJob(
        key,
        {
          name: v3JobForm.name.trim() || undefined,
          problem: v3JobForm.problem.trim(),
          mode: v3JobForm.mode,
          topology_mode: v3JobForm.topology_mode,
          max_gap_seconds: Number(v3JobForm.max_gap_seconds || 300),
          max_device_concurrency: Number(v3JobForm.max_device_concurrency || 20),
          execution_policy: v3JobForm.execution_policy,
          devices,
          topology_edges: topologyEdges,
          webhook_url: v3JobForm.webhook_url.trim() || undefined,
          webhook_events: v3JobForm.webhook_events
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
        },
        v3JobForm.idempotency_key.trim() || undefined,
      )
      antMessage.success(`任务创建成功: ${created.id.slice(0, 8)}...`)
      await refreshV3Jobs()
      setV3SelectedJobId(created.id)
      await refreshV3Timeline(created.id, false)
    } catch (error) {
      antMessage.error((error as Error).message || '创建任务失败')
    } finally {
      setV3CreateJobLoading(false)
    }
  }

  async function handleV3CancelSelectedJob() {
    if (!v3SelectedJobId) return
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入 V3 API Key')
      return
    }
    setV3ActionLoading(true)
    try {
      await v2CancelJob(key, v3SelectedJobId, 'manual_stop')
      await refreshV3Jobs()
      await refreshV3Timeline(v3SelectedJobId)
      antMessage.success('任务已取消')
    } catch (error) {
      antMessage.error((error as Error).message || '取消任务失败')
    } finally {
      setV3ActionLoading(false)
    }
  }

  async function handleV3ApproveSelected() {
    if (!v3SelectedJobId || v3SelectedActionIds.length === 0) {
      antMessage.info('请选择待审批命令组')
      return
    }
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入 V3 API Key')
      return
    }
    setV3ActionLoading(true)
    try {
      await v2ApproveActionGroupsBatch(key, v3SelectedJobId, v3SelectedActionIds, 'batch-approve-from-ui')
      await refreshV3Timeline(v3SelectedJobId, false)
      await refreshV3Jobs()
      antMessage.success('已批量通过命令组')
    } catch (error) {
      antMessage.error((error as Error).message || '批量审批失败')
    } finally {
      setV3ActionLoading(false)
    }
  }

  async function handleV3RejectSelected() {
    if (!v3SelectedJobId || v3SelectedActionIds.length === 0) {
      antMessage.info('请选择待审批命令组')
      return
    }
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入 V3 API Key')
      return
    }
    setV3ActionLoading(true)
    try {
      await v2RejectActionGroupsBatch(key, v3SelectedJobId, v3SelectedActionIds, 'batch-reject-from-ui')
      await refreshV3Timeline(v3SelectedJobId, false)
      await refreshV3Jobs()
      antMessage.success('已批量拒绝命令组')
    } catch (error) {
      antMessage.error((error as Error).message || '批量拒绝失败')
    } finally {
      setV3ActionLoading(false)
    }
  }

  async function handleV3StartStream() {
    if (!v3SelectedJobId) {
      antMessage.warning('请先选择任务')
      return
    }
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入 V3 API Key')
      return
    }
    v3EventAbortRef.current?.abort()
    const controller = new AbortController()
    v3EventAbortRef.current = controller
    setV3Streaming(true)
    try {
      await v2StreamJobEvents(
        key,
        v3SelectedJobId,
        v3EventSeq,
        (event, payload) => {
          const seqNo = Number(payload.seq_no || 0)
          if (seqNo > 0) {
            setV3EventSeq((prev) => Math.max(prev, seqNo))
            const normalized: V2JobEvent = {
              id: String(payload.id || `${v3SelectedJobId}-${seqNo}`),
              job_id: String(payload.job_id || v3SelectedJobId),
              seq_no: seqNo,
              event_type: String(payload.event_type || event),
              payload: payload.payload && typeof payload.payload === 'object'
                ? (payload.payload as Record<string, unknown>)
                : payload,
              created_at: String(payload.created_at || new Date().toISOString()),
            }
            setV3Events((prev) => {
              const merged = [...prev.filter((item) => item.seq_no !== normalized.seq_no), normalized]
              merged.sort((a, b) => a.seq_no - b.seq_no)
              return merged
            })
          }
          if (event === 'completed') {
            void refreshV3Jobs()
            void refreshV3Timeline(v3SelectedJobId, true)
          }
        },
        controller.signal,
      )
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        antMessage.error((error as Error).message || '事件流连接失败')
      }
    } finally {
      if (v3EventAbortRef.current === controller) {
        v3EventAbortRef.current = null
      }
      setV3Streaming(false)
    }
  }

  function handleV3StopStream() {
    v3EventAbortRef.current?.abort()
    v3EventAbortRef.current = null
    setV3Streaming(false)
  }

  async function handleV3UpdateTopology() {
    if (!v3SelectedJobId) {
      antMessage.warning('请先选择任务')
      return
    }
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入 V3 API Key')
      return
    }
    let edges: Array<{ source: string; target: string; kind?: string; confidence?: number; reason?: string }> = []
    try {
      const parsed = JSON.parse(v3TopologyEditor || '[]')
      if (!Array.isArray(parsed)) {
        throw new Error('拓扑边必须是数组')
      }
      edges = parsed as Array<{ source: string; target: string; kind?: string; confidence?: number; reason?: string }>
    } catch (error) {
      antMessage.error(`拓扑边 JSON 解析失败: ${(error as Error).message}`)
      return
    }
    setV3ActionLoading(true)
    try {
      await v2UpdateTopology(key, v3SelectedJobId, { edges, replace: true })
      await refreshV3Jobs()
      await refreshV3Timeline(v3SelectedJobId, false)
      antMessage.success('拓扑输入已更新')
    } catch (error) {
      antMessage.error((error as Error).message || '更新拓扑失败')
    } finally {
      setV3ActionLoading(false)
    }
  }

  async function handleV3UpdateRcaWeights() {
    if (!v3SelectedJobId) {
      antMessage.warning('请先选择任务')
      return
    }
    const key = v3ApiKeyInput.trim()
    if (!key) {
      antMessage.warning('请先输入 V3 API Key')
      return
    }
    setV3ActionLoading(true)
    try {
      await v2UpdateRcaWeights(key, v3SelectedJobId, {
        rca_weights: {
          anomaly: Number(v3RcaWeights.anomaly || 0),
          timing: Number(v3RcaWeights.timing || 0),
          topology: Number(v3RcaWeights.topology || 0),
          change: Number(v3RcaWeights.change || 0),
          consistency: Number(v3RcaWeights.consistency || 0),
        },
      })
      await refreshV3Jobs()
      await refreshV3Timeline(v3SelectedJobId, false)
      antMessage.success('RCA 权重已更新')
    } catch (error) {
      antMessage.error((error as Error).message || '更新 RCA 权重失败')
    } finally {
      setV3ActionLoading(false)
    }
  }

  async function hydrateSessionById(sessionId: string, silent = false) {
    try {
      const data = await getTimeline(sessionId)
      historySnapshotCacheRef.current[sessionId] = data
      const restoredSession: SessionResponse = {
        id: data.session.id,
        automation_level: data.session.automation_level,
        operation_mode: data.session.operation_mode,
        status: data.session.status,
        created_at: data.session.created_at,
      }
      setSession(restoredSession)
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
    jump_host?: string
    jump_port?: number
    jump_username?: string
    jump_password?: string
    api_token?: string
    automation_level: AutomationLevel
  }) {
    try {
      cacheDeviceAuth(payload.host, {
        username: payload.username,
        password: payload.password,
        jump_host: payload.jump_host,
        jump_port: payload.jump_port,
        jump_username: payload.jump_username,
        jump_password: payload.jump_password,
        api_token: payload.api_token,
      })
      const resp = await createSession(payload)
      setSession(resp)
      setAutomationLevel(resp.automation_level)
      setOperationMode(resp.operation_mode)
      setMessages([])
      setCommands([])
      setEvidences([])
      setSummary(undefined)
      setContinueExecutionState(null)
      setTraceSteps([])
      setSessionDeviceAddress(payload.host)
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

  async function handleSend(content: string, options?: SendOptions) {
    if (!session?.id) {
      antMessage.warning('请先在连接控制创建会话')
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
    setStoppingSession(true)
    try {
      streamAbortRef.current?.abort()
      await stopSession(session.id)
      setResumedSessionId(undefined)
      setContinueExecutionState(null)
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
          await updateSessionCredentials(sessionId, {
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
    const cached = historySnapshotCacheRef.current[target]
    if (cached && !forceRefresh) {
      setSelectedHistorySnapshot(cached)
      setHistorySnapshotLoading(false)
      return
    }
    const requestId = historySnapshotRequestRef.current + 1
    historySnapshotRequestRef.current = requestId
    setHistorySnapshotLoading(true)
    if (!cached || forceRefresh) {
      setSelectedHistorySnapshot((prev) => (prev && prev.session.id === target ? prev : null))
    }
    try {
      const data = await getTimeline(target)
      if (historySnapshotRequestRef.current !== requestId) return
      historySnapshotCacheRef.current[target] = data
      setSelectedHistorySnapshot(data)
    } catch {
      if (historySnapshotRequestRef.current !== requestId) return
      setSelectedHistorySnapshot(null)
    } finally {
      if (historySnapshotRequestRef.current !== requestId) return
      setHistorySnapshotLoading(false)
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
    const baselineStepNo = getMaxStepNo(commands)
    const plannedCommands = continuePreview.commands
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
      setSelectedTraceStepId(sortedTraceSteps[0].id)
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
                            {commandConstraintLabel(item.command) && (
                              <span className={`constraint-tag ${constraintTagClass(item.command)}`}>
                                {commandConstraintLabel(item.command)}
                              </span>
                            )}
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
                            <div className="summary-plan-strip">
                              <div className="summary-plan-head">
                                <span>继续执行前命令序列</span>
                                <span className="summary-plan-source">
                                  {continuePreview.source === 'pending'
                                    ? '来源: 待确认命令'
                                    : '来源: 待继续后生成'}
                                </span>
                              </div>
                              {continuePreview.commands.length > 0 ? (
                                <div className="summary-plan-list">
                                  {continuePreview.commands.map((commandText, index) => (
                                    <code key={`${commandText}-${index}`}>{commandText}</code>
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
                                loading={Boolean(continueExecutionState?.active)}
                                disabled={!sessionReady || busy}
                                onClick={() => void handleContinueFromCard()}
                              >
                                {continueExecutionState?.active ? '继续执行中...' : '继续执行'}
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
                                        <code>{`#${entry.step_no} ${entry.command}`}</code>
                                      </div>
                                    ))}
                                  </div>
                                ) : continueExecutionState.plannedCommands.length > 0 ? (
                                  <div className="summary-progress-planned">
                                    {continueExecutionState.plannedCommands.map((entry, index) => (
                                      <code key={`${entry}-${index}`}>{entry}</code>
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

          {activePage === 'v3_jobs' && (
            <div className="page-grid v3-layout">
              <div className="panel-card v3-card">
                <div className="policy-overview-head">
                  <div>
                    <h3>V3 API Key 与权限</h3>
                    <p className="muted">支持多 Key + 权限标签（轻量 RBAC），用于外部 API 调用与审批控制。</p>
                  </div>
                  <div className="policy-actions">
                    <Button size="small" loading={v3ApiKeyLoading} onClick={() => void refreshV3ApiKeys()}>
                      刷新 Key
                    </Button>
                    <Button size="small" onClick={() => void refreshV3PermissionTemplates()}>
                      刷新权限模板
                    </Button>
                  </div>
                </div>
                <div className="v3-grid-2">
                  <Input
                    size="small"
                    value={v3ApiKeyInput}
                    onChange={(event) => setV3ApiKeyInput(event.target.value)}
                    placeholder="当前使用 API Key（X-API-Key）"
                  />
                  <Input
                    size="small"
                    value={v3BootstrapApiKey}
                    onChange={(event) => setV3BootstrapApiKey(event.target.value)}
                    placeholder="Bootstrap Admin Key（首次可留空）"
                  />
                </div>
                <div className="v3-grid-3">
                  <Input
                    size="small"
                    value={v3ApiKeyName}
                    onChange={(event) => setV3ApiKeyName(event.target.value)}
                    placeholder="新 Key 名称"
                  />
                  <Input
                    size="small"
                    value={v3ApiKeyPermissions}
                    onChange={(event) => setV3ApiKeyPermissions(event.target.value)}
                    placeholder="权限标签，逗号分隔"
                  />
                  <Button
                    size="small"
                    type="primary"
                    loading={v3ApiKeyLoading}
                    onClick={() => void handleV3CreateApiKey()}
                  >
                    创建 Key
                  </Button>
                </div>
                <div className="v3-template-box">
                  <span className="muted">最小权限模板</span>
                  {Object.keys(v3PermissionTemplates).length === 0 ? (
                    <div className="muted">-</div>
                  ) : (
                    <div className="v3-template-list">
                      {Object.entries(v3PermissionTemplates).map(([name, perms]) => (
                        <div key={name} className="v3-template-item">
                          <strong>{name}</strong>
                          <code>{perms.join(', ')}</code>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {v3LastCreatedSecret && (
                  <div className="v3-secret-box">
                    <span className="muted">最近创建密钥（仅展示一次，请立即保存）</span>
                    <code>{v3LastCreatedSecret}</code>
                  </div>
                )}
                <div className="policy-rule-table v3-table">
                  <div className="policy-rule-row policy-rule-head v3-key-head">
                    <span>ID</span>
                    <span>名称 / 权限</span>
                    <span>状态 / 操作</span>
                  </div>
                  {v3ApiKeys.length === 0 && <div className="policy-empty muted">暂无 API Key</div>}
                  {v3ApiKeys.map((item) => (
                    <div key={item.id} className="policy-rule-row v3-key-row">
                      <span>
                        <code>{item.id.slice(0, 8)}...</code>
                      </span>
                      <span className="v3-col-stack">
                        <strong>{item.name}</strong>
                        <code>{item.permissions.join(', ') || '*'}</code>
                        <span className="muted">{item.key_prefix}</span>
                      </span>
                      <span className="v3-row-actions">
                        <Switch
                          size="small"
                          checked={item.enabled}
                          onChange={(checked) => void handleV3ToggleApiKey(item.id, checked)}
                        />
                        <Button size="small" danger onClick={() => void handleV3DeleteApiKey(item.id)}>删除</Button>
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="panel-card v3-card">
                <div className="policy-overview-head">
                  <div>
                    <h3>V3 任务创建与调度</h3>
                    <p className="muted">支持多设备并发采集、跨设备关联分析、修复命令组审批与执行策略。</p>
                  </div>
                  <div className="policy-actions">
                    <Button size="small" loading={v3JobsLoading} onClick={() => void refreshV3Jobs()}>
                      刷新任务
                    </Button>
                    <Button
                      size="small"
                      danger
                      loading={v3ActionLoading}
                      onClick={() => void handleV3CancelSelectedJob()}
                      disabled={!v3SelectedJobId}
                    >
                      停止任务
                    </Button>
                  </div>
                </div>
                <div className="v3-grid-3">
                  <Input
                    size="small"
                    value={v3JobForm.name}
                    onChange={(event) => setV3JobForm((prev) => ({ ...prev, name: event.target.value }))}
                    placeholder="任务名称"
                  />
                  <Select
                    size="small"
                    value={v3JobForm.mode}
                    options={[
                      { value: 'diagnosis', label: '诊断' },
                      { value: 'inspection', label: '巡检' },
                      { value: 'repair', label: '修复' },
                    ]}
                    onChange={(value) => setV3JobForm((prev) => ({ ...prev, mode: value }))}
                  />
                  <Select
                    size="small"
                    value={v3JobForm.execution_policy}
                    options={[
                      { value: 'stop_on_failure', label: '失败即停' },
                      { value: 'continue_on_failure', label: '失败继续' },
                      { value: 'rollback_template', label: '失败回滚模板' },
                    ]}
                    onChange={(value) => setV3JobForm((prev) => ({ ...prev, execution_policy: value }))}
                  />
                </div>
                <Input.TextArea
                  value={v3JobForm.problem}
                  onChange={(event) => setV3JobForm((prev) => ({ ...prev, problem: event.target.value }))}
                  rows={2}
                  placeholder="问题描述"
                />
                <div className="v3-grid-4">
                  <Select
                    size="small"
                    value={v3JobForm.topology_mode}
                    options={[
                      { value: 'hybrid', label: 'hybrid' },
                      { value: 'external', label: 'external' },
                      { value: 'auto', label: 'auto' },
                    ]}
                    onChange={(value) => setV3JobForm((prev) => ({ ...prev, topology_mode: value }))}
                  />
                  <Input
                    size="small"
                    value={String(v3JobForm.max_gap_seconds)}
                    onChange={(event) =>
                      setV3JobForm((prev) => ({
                        ...prev,
                        max_gap_seconds: Number(event.target.value || 300),
                      }))
                    }
                    placeholder="max_gap_seconds"
                  />
                  <Input
                    size="small"
                    value={String(v3JobForm.max_device_concurrency)}
                    onChange={(event) =>
                      setV3JobForm((prev) => ({
                        ...prev,
                        max_device_concurrency: Number(event.target.value || 20),
                      }))
                    }
                    placeholder="max_device_concurrency"
                  />
                  <Input
                    size="small"
                    value={v3JobForm.idempotency_key}
                    onChange={(event) => setV3JobForm((prev) => ({ ...prev, idempotency_key: event.target.value }))}
                    placeholder="Idempotency-Key（可选）"
                  />
                </div>
                <Input
                  size="small"
                  value={v3JobForm.webhook_url}
                  onChange={(event) => setV3JobForm((prev) => ({ ...prev, webhook_url: event.target.value }))}
                  placeholder="Webhook URL（可选）"
                />
                <Input
                  size="small"
                  value={v3JobForm.webhook_events}
                  onChange={(event) => setV3JobForm((prev) => ({ ...prev, webhook_events: event.target.value }))}
                  placeholder="Webhook 事件（逗号分隔）"
                />
                <div className="v3-grid-2">
                  <Input.TextArea
                    value={v3JobForm.devices_json}
                    onChange={(event) => setV3JobForm((prev) => ({ ...prev, devices_json: event.target.value }))}
                    rows={6}
                    placeholder="devices JSON"
                  />
                  <Input.TextArea
                    value={v3JobForm.topology_edges_json}
                    onChange={(event) => setV3JobForm((prev) => ({ ...prev, topology_edges_json: event.target.value }))}
                    rows={6}
                    placeholder="topology_edges JSON"
                  />
                </div>
                <div className="policy-actions">
                  <Button
                    type="primary"
                    loading={v3CreateJobLoading}
                    onClick={() => void handleV3CreateJob()}
                  >
                    创建任务
                  </Button>
                  <Button
                    onClick={() => {
                      setV3JobForm(V3_DEFAULT_JOB_FORM)
                    }}
                  >
                    重置表单
                  </Button>
                </div>
                <div className="v3-list-head">
                  <strong>任务列表</strong>
                  <span className="muted">总数 {v3JobsTotal}</span>
                </div>
                <div className="v3-grid-4">
                  <Select
                    size="small"
                    value={v3JobStatusFilter}
                    options={[
                      { value: 'all', label: '状态: 全部' },
                      { value: 'queued', label: 'queued' },
                      { value: 'running', label: 'running' },
                      { value: 'waiting_approval', label: 'waiting_approval' },
                      { value: 'executing', label: 'executing' },
                      { value: 'completed', label: 'completed' },
                      { value: 'failed', label: 'failed' },
                      { value: 'cancelled', label: 'cancelled' },
                    ]}
                    onChange={(value) => setV3JobStatusFilter(value)}
                  />
                  <Select
                    size="small"
                    value={v3JobModeFilter}
                    options={[
                      { value: 'all', label: '模式: 全部' },
                      { value: 'diagnosis', label: 'diagnosis' },
                      { value: 'inspection', label: 'inspection' },
                      { value: 'repair', label: 'repair' },
                    ]}
                    onChange={(value) => setV3JobModeFilter(value)}
                  />
                  <Input
                    size="small"
                    value={String(v3JobOffset)}
                    onChange={(event) => setV3JobOffset(Number(event.target.value || 0))}
                    placeholder="offset"
                  />
                  <Input
                    size="small"
                    value={String(v3JobLimit)}
                    onChange={(event) => setV3JobLimit(Number(event.target.value || 20))}
                    placeholder="limit"
                  />
                </div>
                <div className="policy-actions">
                  <Button size="small" onClick={() => void refreshV3Jobs()} loading={v3JobsLoading}>
                    应用筛选
                  </Button>
                </div>
                <div className="policy-rule-table v3-table">
                  {v3Jobs.length === 0 && <div className="policy-empty muted">暂无任务</div>}
                  {v3Jobs.map((job) => (
                    <button
                      type="button"
                      key={job.id}
                      className={`v3-job-item ${v3SelectedJobId === job.id ? 'active' : ''}`}
                      onClick={() => setV3SelectedJobId(job.id)}
                    >
                      <div className="v3-job-head">
                        <strong>{job.name || job.id.slice(0, 8)}</strong>
                        <span className={`cmd-status ${statusClass(job.status)}`}>{job.status}</span>
                      </div>
                      <div className="v3-job-meta">
                        <span>{job.mode}</span>
                        <span>{job.phase}</span>
                        <span>设备 {job.device_count}</span>
                        <span>命令 {job.command_count}</span>
                      </div>
                      <div className="muted">{truncateText(job.problem, 120)}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="panel-card v3-card">
                <div className="policy-overview-head">
                  <div>
                    <h3>任务详情 / 审批 / 事件流</h3>
                    <p className="muted">命令组按审批粒度执行，支持批量确认；可实时订阅任务事件。</p>
                  </div>
                  <div className="policy-actions">
                    <Button size="small" loading={v3TimelineLoading} onClick={() => v3SelectedJobId && void refreshV3Timeline(v3SelectedJobId, false)}>
                      刷新时间线
                    </Button>
                    {v3Streaming ? (
                      <Button size="small" danger onClick={handleV3StopStream}>停止流</Button>
                    ) : (
                      <Button size="small" type="primary" onClick={() => void handleV3StartStream()} disabled={!v3SelectedJobId}>
                        订阅事件流
                      </Button>
                    )}
                  </div>
                </div>
                {!v3Timeline && <div className="muted">请先选择任务</div>}
                {v3Timeline && (
                  <div className="v3-details-grid">
                    <div className="v3-section-card">
                      <div className="kv"><span>任务 ID</span><strong>{v3Timeline.job.id}</strong></div>
                      <div className="kv"><span>状态</span><strong>{v3Timeline.job.status}</strong></div>
                      <div className="kv"><span>阶段</span><strong>{v3Timeline.job.phase}</strong></div>
                      <div className="kv"><span>策略</span><strong>{v3Timeline.job.execution_policy}</strong></div>
                      <div className="kv"><span>根因设备</span><strong>{v3SelectedJobSummary?.root_device_id || '-'}</strong></div>
                    </div>

                    <div className="v3-section-card">
                      <div className="v3-list-head">
                        <strong>待审批命令组</strong>
                        <span className="muted">{v3PendingActionGroups.length} 组</span>
                      </div>
                      <div className="v3-action-list">
                        {v3PendingActionGroups.length === 0 && <div className="muted">无待审批命令组</div>}
                        {v3PendingActionGroups.map((group) => {
                          const selected = v3SelectedActionIds.includes(group.id)
                          return (
                            <label key={group.id} className="v3-action-item">
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={(event) => {
                                  setV3SelectedActionIds((prev) => {
                                    if (event.target.checked) {
                                      return Array.from(new Set([...prev, group.id]))
                                    }
                                    return prev.filter((id) => id !== group.id)
                                  })
                                }}
                              />
                              <div className="v3-col-stack">
                                <strong>{group.title}</strong>
                                <span className="muted">
                                  {group.device_id} | risk={group.risk_level} | {group.commands.length} 条
                                </span>
                                <code>{group.commands.join(' ; ')}</code>
                              </div>
                            </label>
                          )
                        })}
                      </div>
                      <div className="policy-actions">
                        <Button
                          size="small"
                          type="primary"
                          loading={v3ActionLoading}
                          disabled={v3SelectedActionIds.length === 0}
                          onClick={() => void handleV3ApproveSelected()}
                        >
                          批量通过
                        </Button>
                        <Button
                          size="small"
                          danger
                          loading={v3ActionLoading}
                          disabled={v3SelectedActionIds.length === 0}
                          onClick={() => void handleV3RejectSelected()}
                        >
                          批量拒绝
                        </Button>
                      </div>
                    </div>

                    <div className="v3-section-card">
                      <div className="v3-list-head">
                        <strong>命令结果（最近 20 条）</strong>
                        <span className="muted">{v3Timeline.job.command_results.length} 条</span>
                      </div>
                      <div className="v3-command-list">
                        {v3Timeline.job.command_results.slice(-20).map((row) => (
                          <div key={row.id} className="v3-command-item">
                            <span className="muted">#{row.step_no}</span>
                            <span className={`cmd-status ${statusClass(row.status)}`}>{row.status}</span>
                            <code>{row.command}</code>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="v3-section-card">
                      <div className="v3-list-head">
                        <strong>拓扑输入 / RCA 权重</strong>
                        <span className="muted">可在线调整</span>
                      </div>
                      <Input.TextArea
                        value={v3TopologyEditor}
                        onChange={(event) => setV3TopologyEditor(event.target.value)}
                        rows={5}
                      />
                      <div className="v3-grid-5">
                        {(['anomaly', 'timing', 'topology', 'change', 'consistency'] as const).map((key) => (
                          <Input
                            key={key}
                            size="small"
                            value={String(v3RcaWeights[key])}
                            onChange={(event) =>
                              setV3RcaWeights((prev) => ({
                                ...prev,
                                [key]: Number(event.target.value || 0),
                              }))
                            }
                            placeholder={key}
                          />
                        ))}
                      </div>
                      <div className="policy-actions">
                        <Button size="small" loading={v3ActionLoading} onClick={() => void handleV3UpdateTopology()}>
                          更新拓扑
                        </Button>
                        <Button size="small" loading={v3ActionLoading} onClick={() => void handleV3UpdateRcaWeights()}>
                          更新权重
                        </Button>
                      </div>
                    </div>

                    <div className="v3-section-card v3-events-card">
                      <div className="v3-list-head">
                        <strong>事件流</strong>
                        <span className="muted">seq: {v3EventSeq}</span>
                      </div>
                      <div className="v3-event-list">
                        {v3Events.length === 0 && <div className="muted">暂无事件</div>}
                        {v3Events.slice(-80).map((event) => (
                          <details key={`${event.job_id}-${event.seq_no}`} className="v3-event-item">
                            <summary>
                              <span>[{event.seq_no}]</span>
                              <strong>{event.event_type}</strong>
                              <span className="muted">{formatTime(event.created_at)}</span>
                            </summary>
                            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                          </details>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <div className="panel-card v3-card v3-bottom-card">
                <div className="policy-overview-head">
                  <div>
                    <h3>审计日志 / 命令成功率画像</h3>
                    <p className="muted">用于排查审批轨迹与命令命中效果，帮助持续优化自动化成功率。</p>
                  </div>
                  <div className="policy-actions">
                    <Button size="small" loading={v3AuditLoading} onClick={() => void refreshV3AuditLogs()}>
                      刷新审计
                    </Button>
                    <Button size="small" loading={v3ProfilesLoading} onClick={() => void refreshV3CommandProfiles()}>
                      刷新画像
                    </Button>
                  </div>
                </div>
                <div className="v3-grid-2">
                  <div className="v3-section-card">
                    <div className="v3-list-head">
                      <strong>审计日志（最近 100 条）</strong>
                    </div>
                    <div className="v3-event-list">
                      {v3AuditLogs.length === 0 && <div className="muted">暂无审计日志</div>}
                      {v3AuditLogs.slice(-100).map((row, idx) => (
                        <div key={`audit-${idx}`} className="v3-audit-row">
                          <span className="muted">{String(row.ts || '-')}</span>
                          <strong>{String(row.action || '-')}</strong>
                          <span>{String(row.resource || '-')}</span>
                          <span className={`cmd-status ${statusClass(String(row.status || 'idle'))}`}>
                            {String(row.status || '-')}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="v3-section-card">
                    <div className="v3-list-head">
                      <strong>命令画像（最近 100 条）</strong>
                    </div>
                    <div className="v3-event-list">
                      {v3CommandProfiles.length === 0 && <div className="muted">暂无命令画像</div>}
                      {v3CommandProfiles.slice(0, 100).map((row, idx) => (
                        <div key={`profile-${idx}`} className="v3-audit-row">
                          <span>{String(row.version_signature || '-')}</span>
                          <code>{String(row.command_key || '-')}</code>
                          <span className="muted">
                            rate={Number(row.success_rate || 0).toFixed(2)} hit={String(row.hits || 0)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activePage === 'control' && (
            <div className="page-grid control-layout">
              <div className="panel-card">
                <h3>连接控制</h3>
                <p className="muted">会话创建、任务模式、设备连接、命令执行控制等级设置放在此页面。</p>
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
              <div className="panel-card">
                <h3>连接状态</h3>
                <div className="kv"><span>会话 ID</span><strong>{session?.id || '-'}</strong></div>
                <div className="kv"><span>会话模式</span><strong>{session?.operation_mode ? operationModeLabel(session.operation_mode) : '-'}</strong></div>
                <div className="kv"><span>命令执行控制等级</span><strong>{automationLabel(automationLevel)}</strong></div>
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
                      className={`session-history-item ${session?.id === item.id ? 'active' : ''} ${selectedHistorySessionId === item.id ? 'selected' : ''}`}
                      onClick={() => handleSelectHistorySession(item.id)}
                      onDoubleClick={() => void handleRestoreSession(item.id, item.host)}
                    >
                      <div className="session-history-open">
                        <div className="session-history-main">
                          <strong>{item.host}</strong>
                          <span>设备名称: {formatDeviceName(item.device_name)}</span>
                          <span>{operationModeLabel(item.operation_mode)} / {automationLabel(item.automation_level)}</span>
                        </div>
                        <div className="session-history-meta">
                          <span>{item.id.slice(0, 8)}...</span>
                          <span>{formatTime(item.created_at)}</span>
                        </div>
                      </div>
                      <div className="session-history-actions">
                        <Button size="small" type="primary" onClick={() => void handleRestoreSession(item.id, item.host)}>
                          恢复
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="panel-card session-snapshot-panel">
                <h3>当前会话快照</h3>
                {historySnapshotLoading && <div className="muted">快照加载中...</div>}
                <div className="kv"><span>已选会话</span><strong>{selectedHistorySessionId || session?.id || '-'}</strong></div>
                <div className="kv"><span>设备</span><strong>{selectedHistorySnapshotView?.session.device.host || selectedHistoryItem?.host || sessionDeviceAddress || '-'}</strong></div>
                <div className="kv"><span>设备名称</span><strong>{formatDeviceName(selectedHistorySnapshotView?.session.device.name || selectedHistoryItem?.device_name || sessionDeviceName)}</strong></div>
                <div className="kv"><span>模式</span><strong>{selectedHistorySnapshotView?.session.operation_mode ? operationModeLabel(selectedHistorySnapshotView.session.operation_mode) : (selectedHistoryItem?.operation_mode ? operationModeLabel(selectedHistoryItem.operation_mode) : '-')}</strong></div>
                <div className="kv"><span>自动化等级</span><strong>{selectedHistorySnapshotView?.session.automation_level ? automationLabel(selectedHistorySnapshotView.session.automation_level) : (selectedHistoryItem?.automation_level ? automationLabel(selectedHistoryItem.automation_level) : '-')}</strong></div>
                <div className="kv"><span>状态</span><strong>{selectedHistorySnapshotView?.session.status || selectedHistoryItem?.status || '-'}</strong></div>
                <div className="kv"><span>创建时间</span><strong>{formatTime(selectedHistorySnapshotView?.session.created_at || selectedHistoryItem?.created_at || '')}</strong></div>
                <div className="kv"><span>最近活动</span><strong>{formatTime(snapshotUpdatedAt || '')}</strong></div>
                <div className="kv"><span>消息数</span><strong>{selectedHistorySnapshotView?.messages.length ?? 0}</strong></div>
                <div className="kv"><span>命令数</span><strong>{selectedHistorySnapshotView?.commands.length ?? 0}</strong></div>
                <div className="kv"><span>证据数</span><strong>{selectedHistorySnapshotView?.evidences.length ?? 0}</strong></div>
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
                          <strong>{lane.label}</strong>
                          <span>{lane.realCount} 步</span>
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
                                `#${step.seq_no} ${step.title}`,
                                `${traceTypeLabel(step.step_type)} · ${step.status}`,
                                traceConstraintLabel(step) ? `约束判定 · ${traceConstraintLabel(step)}` : '',
                              ].filter(Boolean).join('\n')}
                            >
                              <div className="flow-node-line">
                                <div className="flow-node-title">#{step.seq_no} {step.title}</div>
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
                          <strong>{lane.label}</strong>
                          <span>{lane.realCount} 步</span>
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
                                  `#${step.seq_no} ${step.title}`,
                                  `${traceTypeLabel(step.step_type)} · ${step.status}`,
                                  traceConstraintLabel(step) ? `约束判定 · ${traceConstraintLabel(step)}` : '',
                                ].filter(Boolean).join('\n')}
                              >
                                <div className="flow-node-line">
                                  <div className="flow-node-title">#{step.seq_no} {step.title}</div>
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
                        <div><span>开始</span><strong>{formatTime(selectedTraceStep.started_at)}</strong></div>
                        <div><span>结束</span><strong>{selectedTraceStep.completed_at ? formatTime(selectedTraceStep.completed_at) : '-'}</strong></div>
                        <div><span>耗时</span><strong>{selectedTraceStep.duration_ms !== undefined ? formatDuration(selectedTraceStep.duration_ms) : '-'}</strong></div>
                      </div>
                      <pre className="flow-detail-text">{selectedTraceStep.detail || '(无详细信息)'}</pre>
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
            </div>
          )}

          {activePage === 'learning' && (
            <div className="learning-layout">
              <div className="panel-card learning-toolbar-card">
                <div className="learning-toolbar-main">
                  <div>
                    <h3>学习规则（版本级：失败阻断 / 替代改写）</h3>
                    <p className="muted learning-subtitle">按版本指纹维护命令替代与阻断规则，避免重复试错。</p>
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
  if (pendingCommands.length > 0) {
    return {
      source: 'pending',
      commands: pendingCommands.map((item) => `#${item.step_no} ${item.command}`),
    }
  }
  return {
    source: 'none',
    commands: [],
  }
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

function buildFlowLanes(steps: ServiceTraceStep[], mode: FlowLayoutMode): FlowLane[] {
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
  if (steps.length === 0) {
    return laneOrder.map((key) => ({
      key,
      label: labels[key] || key,
      steps: [],
      realCount: 0,
    }))
  }

  const ordered = [...steps].sort((a, b) => a.seq_no - b.seq_no)
  const lanes = new Map<string, Array<ServiceTraceStep | null>>()
  for (const key of laneOrder) lanes.set(key, [])

  if (mode === 'stair') {
    // Strict stair mode: step N occupies row N.
    for (const step of ordered) {
      const laneKey = traceLaneKey(step.step_type)
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
      const laneKey = traceLaneKey(step.step_type)
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
    steps: lanes.get(key) || [],
    realCount: (lanes.get(key) || []).filter((item) => item !== null).length,
  }))
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

function traceLaneKey(stepType: string): string {
  if (stepType === 'user_input') return 'user'
  if (stepType === 'llm_plan' || stepType === 'llm_status' || stepType === 'plan_decision') return 'plan'
  if (stepType === 'policy_decision' || stepType === 'capability_decision' || stepType === 'scope_decision') return 'policy'
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

function traceTypeLabel(stepType: string): string {
  if (stepType === 'user_input') return '用户请求'
  if (stepType === 'llm_plan') return 'LLM 规划'
  if (stepType === 'llm_final') return 'LLM 总结'
  if (stepType === 'llm_status') return 'LLM 可用性'
  if (stepType === 'plan_decision') return '流程判定'
  if (stepType === 'policy_decision') return '策略判定'
  if (stepType === 'capability_decision') return '能力判定'
  if (stepType === 'scope_decision') return '模式范围判定'
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
  if (page === 'v3_jobs') return '⬢'
  if (page === 'control') return '⌘'
  if (page === 'command_policy') return '☑'
  if (page === 'sessions') return '☷'
  if (page === 'service_trace') return '⏱'
  if (page === 'learning') return '⌬'
  if (page === 'lab') return '△'
  return '◎'
}

export default App
