import type { SessionResponse } from '../types'

export type RunRuntimeKind = 'single' | 'multi'

type HistorySessionLike = {
  id: string
  run_id?: string
}

export function toV2HistorySessionId(jobId: string): string {
  return `v2job:${String(jobId || '').trim()}`
}

export function toUnifiedSingleRunId(sessionId: string): string {
  return `run_s:${String(sessionId || '').trim()}`
}

export function toUnifiedMultiRunId(jobId: string): string {
  return `run_m:${String(jobId || '').trim()}`
}

export function parseV2HistoryJobId(historyId: string): string | undefined {
  const value = String(historyId || '').trim()
  if (!value.startsWith('v2job:')) return undefined
  return value.slice('v2job:'.length).trim() || undefined
}

export function parseV2ActionGroupCommandId(commandId: string): string | undefined {
  const value = String(commandId || '').trim()
  if (!value.startsWith('v2ag:')) return undefined
  return value.slice(5).trim() || undefined
}

export function resolveUnifiedRunId(options: {
  targetId?: string
  sessionHistory: HistorySessionLike[]
  activeSessionId?: string
  sessionRuntimeKind?: RunRuntimeKind
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

export function resolveCurrentRuntimeRunId(options: {
  session: SessionResponse | null
  sessionRuntimeKind: RunRuntimeKind
  multiSessionActiveJobId?: string
}): string | undefined {
  if (!options.session?.id) return undefined
  if (options.sessionRuntimeKind === 'multi' && options.multiSessionActiveJobId) {
    return toUnifiedMultiRunId(options.multiSessionActiveJobId)
  }
  return toUnifiedSingleRunId(options.session.id)
}

export function supportsRunCredentialPatch(runtimeKind: RunRuntimeKind): boolean {
  return runtimeKind === 'single'
}

export function resolveHistorySnapshotRunId(options: {
  historyId: string
  explicitRunId?: string
  sessionHistory: HistorySessionLike[]
  activeSessionId?: string
  sessionRuntimeKind?: RunRuntimeKind
  multiSessionActiveJobId?: string
}): string | undefined {
  return options.explicitRunId || resolveUnifiedRunId({
    targetId: options.historyId,
    sessionHistory: options.sessionHistory,
    activeSessionId: options.activeSessionId,
    sessionRuntimeKind: options.sessionRuntimeKind,
    multiSessionActiveJobId: options.multiSessionActiveJobId,
  })
}

export function resolveStopRunId(options: {
  session: SessionResponse | null
  sessionRuntimeKind: RunRuntimeKind
  multiSessionActiveJobId?: string
}): string | undefined {
  return resolveCurrentRuntimeRunId(options)
}

export function resolveApprovalRunTarget(options: {
  session: SessionResponse | null
  commandId: string
  hasActiveMultiRuntime: boolean
  multiSessionActiveJobId?: string
}): { kind: RunRuntimeKind; runId: string; actionGroupId?: string } | undefined {
  if (!options.session?.id) return undefined
  const actionGroupId = parseV2ActionGroupCommandId(options.commandId)
  if (actionGroupId && options.hasActiveMultiRuntime && options.multiSessionActiveJobId) {
    return {
      kind: 'multi',
      runId: toUnifiedMultiRunId(options.multiSessionActiveJobId),
      actionGroupId,
    }
  }
  return {
    kind: 'single',
    runId: toUnifiedSingleRunId(options.session.id),
  }
}
