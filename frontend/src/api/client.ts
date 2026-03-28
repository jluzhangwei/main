import type {
  AutomationLevel,
  CommandCapabilityResetResponse,
  CommandCapabilityRule,
  CommandCapabilityUpsertRequest,
  CommandPolicy,
  CommandPolicyUpdateRequest,
  EventPayload,
  LLMPromptPolicy,
  LLMStatus,
  RiskPolicy,
  RiskPolicyUpdateRequest,
  RunActionDecisionResponse,
  RunListResponse,
  RunStopResponse,
  RunSummary,
  RunTimelineResponse,
  ServiceTrace,
  SessionResponse,
  SOPArchiveResponse,
  V2ApiKey,
  V2ApiKeyCreateResponse,
  V2PermissionTemplates,
} from '../types'

const headers = {
  'Content-Type': 'application/json',
}

function resolveApiBase(): string {
  const envBase = String(
    (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env?.VITE_API_BASE_URL ?? '',
  )
    .trim()
    .replace(/\/+$/, '')
  if (envBase) return envBase

  // Dev server (5173) fallback: use same hostname with backend port 8000.
  if (typeof window !== 'undefined' && window.location.port === '5173') {
    const protocol = window.location.protocol === 'https:' ? 'https' : 'http'
    const host = window.location.hostname
    return `${protocol}://${host}:8000`
  }
  return ''
}

const API_BASE = resolveApiBase()

function apiUrl(path: string): string {
  if (!API_BASE) return path
  return `${API_BASE}${path}`
}

function htmlHint(text: string): string {
  const preview = text.trim().slice(0, 120).replace(/\s+/g, ' ')
  if (preview.toLowerCase().startsWith('<!doctype') || preview.toLowerCase().startsWith('<html')) {
    return '接口返回了 HTML 页面（非 JSON），请确认后端 /v2 服务已启动并且前端代理/网关未把 API 路由回退到 index.html。'
  }
  return `接口返回非 JSON 数据: ${preview || '(empty)'}`
}

async function parseJsonResponse<T>(res: Response, message: string): Promise<T> {
  const body = await res.text()
  let parsed: unknown = null
  if (body.trim()) {
    try {
      parsed = JSON.parse(body)
    } catch {
      parsed = null
    }
  }

  if (!res.ok) {
    const detail =
      parsed && typeof parsed === 'object' && 'detail' in parsed
        ? String((parsed as { detail?: unknown }).detail ?? '')
        : body.trim().slice(0, 200).replace(/\s+/g, ' ')
    throw new Error(detail ? `${message}: ${detail}` : message)
  }

  if (parsed !== null) {
    return parsed as T
  }
  throw new Error(`${message}: ${htmlHint(body)}`)
}

export async function updateRunAutomation(apiKey: string, runId: string, automationLevel: AutomationLevel): Promise<SessionResponse> {
  const res = await fetch(apiUrl(`/api/runs/${runId}`), {
    method: 'PATCH',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ automation_level: automationLevel }),
  })
  const payload = await parseJsonResponse<RunSummary>(res, 'Failed to update run automation level')
  return {
    id: payload.source_id,
    automation_level: payload.automation_level,
    operation_mode: payload.operation_mode,
    status: payload.status === 'cancelled' ? 'closed' : payload.status === 'open' || payload.status === 'running' || payload.status === 'waiting_approval' ? 'open' : 'closed',
    created_at: payload.created_at,
  }
}

export async function updateRunCredentials(
  apiKey: string,
  runId: string,
  payload: {
    username?: string
    password?: string
    jump_host?: string
    jump_port?: number
    jump_username?: string
    jump_password?: string
    api_token?: string
  },
): Promise<SessionResponse> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/credentials`), {
    method: 'PATCH',
    headers: v2Headers(apiKey),
    body: JSON.stringify(payload),
  })
  const run = await parseJsonResponse<RunSummary>(res, 'Failed to update run credentials')
  return {
    id: run.source_id,
    automation_level: run.automation_level,
    operation_mode: run.operation_mode,
    status: run.status === 'cancelled' ? 'closed' : run.status === 'open' || run.status === 'running' || run.status === 'waiting_approval' ? 'open' : 'closed',
    created_at: run.created_at,
  }
}

export async function getLlmStatus(): Promise<LLMStatus> {
  const res = await fetch('/v1/llm/status')
  if (!res.ok) {
    throw new Error('Failed to load LLM status')
  }
  return res.json()
}

export async function configureLlm(input: {
  apiKey?: string
  nvidiaApiKey?: string
  model?: string
  baseUrl?: string
  nvidiaBaseUrl?: string
  failoverEnabled?: boolean
  batchExecutionEnabled?: boolean
  modelCandidates?: string[]
}): Promise<LLMStatus> {
  const res = await fetch('/v1/llm/config', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      api_key: input.apiKey,
      nvidia_api_key: input.nvidiaApiKey,
      model: input.model,
      base_url: input.baseUrl,
      nvidia_base_url: input.nvidiaBaseUrl,
      failover_enabled: input.failoverEnabled,
      batch_execution_enabled: input.batchExecutionEnabled,
      model_candidates: input.modelCandidates,
    }),
  })
  if (!res.ok) {
    throw new Error('Failed to configure LLM')
  }
  return res.json()
}

export async function deleteLlmConfig(): Promise<LLMStatus> {
  const res = await fetch('/v1/llm/config', {
    method: 'DELETE',
  })
  if (!res.ok) {
    throw new Error('Failed to delete LLM config')
  }
  return res.json()
}

export async function getLlmPromptPolicy(): Promise<LLMPromptPolicy> {
  const res = await fetch('/v1/llm/prompt-policy')
  if (!res.ok) {
    throw new Error('Failed to load LLM prompt policy')
  }
  return res.json()
}

export async function getCommandPolicy(): Promise<CommandPolicy> {
  const res = await fetch('/v1/command-policy')
  if (!res.ok) {
    throw new Error('Failed to load command policy')
  }
  return res.json()
}

export async function getCommandCapability(input?: {
  host?: string
  version_signature?: string
  scope_key?: string
}): Promise<CommandCapabilityRule[]> {
  const params = new URLSearchParams()
  if (input?.host) params.set('host', input.host)
  if (input?.version_signature) params.set('version_signature', input.version_signature)
  if (input?.scope_key) params.set('scope_key', input.scope_key)
  const query = params.toString()
  const res = await fetch(`/v1/command-capability${query ? `?${query}` : ''}`)
  if (!res.ok) {
    throw new Error('Failed to load command capability rules')
  }
  return res.json()
}

export async function upsertCommandCapability(
  payload: CommandCapabilityUpsertRequest,
): Promise<CommandCapabilityRule> {
  const res = await fetch('/v1/command-capability', {
    method: 'PUT',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    throw new Error('Failed to save command capability rule')
  }
  return res.json()
}

export async function deleteCommandCapability(ruleId: string): Promise<void> {
  const res = await fetch(`/v1/command-capability/${ruleId}`, {
    method: 'DELETE',
    headers,
  })
  if (!res.ok) {
    throw new Error('Failed to delete command capability rule')
  }
}

export async function resetCommandCapability(input?: {
  host?: string
  version_signature?: string
}): Promise<CommandCapabilityResetResponse> {
  const res = await fetch('/v1/command-capability/reset', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      host: input?.host || undefined,
      version_signature: input?.version_signature || undefined,
    }),
  })
  if (!res.ok) {
    throw new Error('Failed to reset command capability rules')
  }
  return res.json()
}

export async function updateCommandPolicy(payload: CommandPolicyUpdateRequest): Promise<CommandPolicy> {
  const res = await fetch('/v1/command-policy', {
    method: 'PUT',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    throw new Error('Failed to update command policy')
  }
  return res.json()
}

export async function resetCommandPolicy(): Promise<CommandPolicy> {
  const res = await fetch('/v1/command-policy/reset', {
    method: 'POST',
    headers,
  })
  if (!res.ok) {
    throw new Error('Failed to reset command policy')
  }
  return res.json()
}

export async function getRiskPolicy(): Promise<RiskPolicy> {
  const res = await fetch('/v1/risk-policy')
  if (!res.ok) {
    throw new Error('Failed to load risk policy')
  }
  return res.json()
}

export async function updateRiskPolicy(payload: RiskPolicyUpdateRequest): Promise<RiskPolicy> {
  const res = await fetch('/v1/risk-policy', {
    method: 'PUT',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    throw new Error('Failed to update risk policy')
  }
  return res.json()
}

export async function resetRiskPolicy(): Promise<RiskPolicy> {
  const res = await fetch('/v1/risk-policy/reset', {
    method: 'POST',
    headers,
  })
  if (!res.ok) {
    throw new Error('Failed to reset risk policy')
  }
  return res.json()
}

function v2Headers(apiKey?: string, extra?: Record<string, string>): HeadersInit {
  const token = String(apiKey || '').trim()
  return {
    ...headers,
    ...(token ? { 'X-API-Key': token } : {}),
    'X-Internal-UI': '1',
    ...(extra || {}),
  }
}

export async function createRun(
  apiKey: string,
  payload: Record<string, unknown>,
  idempotencyKey?: string,
): Promise<RunSummary> {
  const res = await fetch(apiUrl('/api/runs'), {
    method: 'POST',
    headers: v2Headers(apiKey, idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined),
    body: JSON.stringify(payload),
  })
  return parseJsonResponse<RunSummary>(res, 'Failed to create run')
}

export async function streamRunMessage(
  apiKey: string,
  runId: string,
  content: string,
  onEvent: (event: string, payload: EventPayload) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/messages`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ content }),
    signal,
  })

  if (!res.ok || !res.body) {
    throw new Error('Failed to stream run message')
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''

    for (const chunk of chunks) {
      const lines = chunk.split('\n')
      const eventLine = lines.find((l) => l.startsWith('event: '))
      const dataLine = lines.find((l) => l.startsWith('data: '))
      if (!eventLine || !dataLine) continue

      const event = eventLine.replace('event: ', '').trim()
      const raw = dataLine.replace('data: ', '')
      const payload = JSON.parse(raw) as EventPayload
      onEvent(event, payload)
    }
  }
}

export async function listRuns(
  apiKey: string,
  query?: {
    offset?: number
    limit?: number
    kind?: 'single' | 'multi'
  },
): Promise<RunListResponse> {
  const params = new URLSearchParams()
  if (query?.offset !== undefined) params.set('offset', String(query.offset))
  if (query?.limit !== undefined) params.set('limit', String(query.limit))
  if (query?.kind) params.set('kind', query.kind)
  const res = await fetch(apiUrl(`/api/runs${params.toString() ? `?${params.toString()}` : ''}`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<RunListResponse>(res, 'Failed to list runs')
}

export async function getRunTimeline(apiKey: string, runId: string): Promise<RunTimelineResponse> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/timeline`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<RunTimelineResponse>(res, 'Failed to get run timeline')
}

export async function getRunTrace(apiKey: string, runId: string): Promise<ServiceTrace> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/trace`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<ServiceTrace>(res, 'Failed to get run trace')
}

export async function streamRunEvents(
  apiKey: string,
  runId: string,
  fromSeq: number,
  onEvent: (event: string, payload: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/events?from_seq=${fromSeq}`), {
    headers: v2Headers(apiKey),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error('Failed to stream run events')
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const lines = chunk.split('\n')
      const eventLine = lines.find((l) => l.startsWith('event: '))
      const dataLine = lines.find((l) => l.startsWith('data: '))
      if (!eventLine || !dataLine) continue
      const event = eventLine.replace('event: ', '').trim()
      const raw = dataLine.replace('data: ', '')
      const payload = JSON.parse(raw) as Record<string, unknown>
      onEvent(event, payload)
    }
  }
}

export async function getSopLibrary(
  apiKey: string,
  query?: {
    problem?: string
    vendor?: string
  },
): Promise<SOPArchiveResponse> {
  const params = new URLSearchParams()
  if (query?.problem) params.set('problem', query.problem)
  if (query?.vendor) params.set('vendor', query.vendor)
  const res = await fetch(apiUrl(`/api/sop-library${params.toString() ? `?${params.toString()}` : ''}`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<SOPArchiveResponse>(res, 'Failed to get SOP library')
}

export async function exportRunMarkdown(apiKey: string, runId: string): Promise<string> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/export`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ format: 'markdown' }),
  })
  const data = await parseJsonResponse<{ content: string }>(res, 'Failed to export run report')
  return String(data.content || '')
}

export async function approveRunActions(
  apiKey: string,
  runId: string,
  itemIds?: string[],
  reason?: string,
): Promise<RunActionDecisionResponse> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/actions/approve`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ item_ids: itemIds || [], reason }),
  })
  return parseJsonResponse<RunActionDecisionResponse>(res, 'Failed to approve run actions')
}

export async function rejectRunActions(
  apiKey: string,
  runId: string,
  itemIds?: string[],
  reason?: string,
): Promise<RunActionDecisionResponse> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/actions/reject`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ item_ids: itemIds || [], reason }),
  })
  return parseJsonResponse<RunActionDecisionResponse>(res, 'Failed to reject run actions')
}

export async function stopRun(apiKey: string, runId: string): Promise<RunStopResponse> {
  const res = await fetch(apiUrl(`/api/runs/${runId}/stop`), {
    method: 'POST',
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<RunStopResponse>(res, 'Failed to stop run')
}

export async function v2CreateApiKey(input: {
  name: string
  permissions: string[]
  bootstrapApiKey?: string
  expiresAt?: string
}): Promise<V2ApiKeyCreateResponse> {
  const h: HeadersInit = v2Headers(input.bootstrapApiKey)
  const res = await fetch(apiUrl('/v2/keys'), {
    method: 'POST',
    headers: h,
    body: JSON.stringify({
      name: input.name,
      permissions: input.permissions,
      expires_at: input.expiresAt,
    }),
  })
  return parseJsonResponse<V2ApiKeyCreateResponse>(res, 'Failed to create API key')
}

export async function v2ListApiKeys(apiKey: string): Promise<V2ApiKey[]> {
  const res = await fetch(apiUrl('/v2/keys'), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<V2ApiKey[]>(res, 'Failed to list API keys')
}

export async function v2UpdateApiKey(apiKey: string, keyId: string, payload: {
  enabled?: boolean
  disabled_reason?: string
  expires_at?: string
}): Promise<V2ApiKey> {
  const res = await fetch(apiUrl(`/v2/keys/${keyId}`), {
    method: 'PATCH',
    headers: v2Headers(apiKey),
    body: JSON.stringify(payload),
  })
  return parseJsonResponse<V2ApiKey>(res, 'Failed to update API key')
}

export async function v2RotateApiKey(apiKey: string, keyId: string, payload?: {
  name?: string
  permissions?: string[]
  expires_at?: string
}): Promise<V2ApiKeyCreateResponse> {
  const res = await fetch(apiUrl(`/v2/keys/${keyId}/rotate`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify(payload || {}),
  })
  return parseJsonResponse<V2ApiKeyCreateResponse>(res, 'Failed to rotate API key')
}

export async function v2DeleteApiKey(apiKey: string, keyId: string): Promise<void> {
  const res = await fetch(apiUrl(`/v2/keys/${keyId}`), {
    method: 'DELETE',
    headers: v2Headers(apiKey),
  })
  await parseJsonResponse<Record<string, unknown>>(res, 'Failed to delete API key')
}

export async function v2GetAuditLogs(apiKey: string, query?: {
  action?: string
  status?: string
  actor_key_id?: string
  limit?: number
  offset?: number
}): Promise<Array<Record<string, unknown>>> {
  const params = new URLSearchParams()
  if (query?.action) params.set('action', query.action)
  if (query?.status) params.set('status', query.status)
  if (query?.actor_key_id) params.set('actor_key_id', query.actor_key_id)
  if (query?.limit !== undefined) params.set('limit', String(query.limit))
  if (query?.offset !== undefined) params.set('offset', String(query.offset))
  const res = await fetch(apiUrl(`/v2/audit/logs${params.toString() ? `?${params.toString()}` : ''}`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<Array<Record<string, unknown>>>(res, 'Failed to get audit logs')
}

export async function v2GetAuditReport(apiKey: string, query?: {
  format?: 'json' | 'csv' | 'pdf'
  action?: string
  status?: string
  actor_key_id?: string
}): Promise<Record<string, unknown>> {
  const params = new URLSearchParams()
  if (query?.format) params.set('format', query.format)
  if (query?.action) params.set('action', query.action)
  if (query?.status) params.set('status', query.status)
  if (query?.actor_key_id) params.set('actor_key_id', query.actor_key_id)
  const res = await fetch(apiUrl(`/v2/audit/reports${params.toString() ? `?${params.toString()}` : ''}`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<Record<string, unknown>>(res, 'Failed to get audit report')
}

export async function v2GetCommandProfiles(apiKey: string): Promise<Array<Record<string, unknown>>> {
  const res = await fetch(apiUrl('/v2/command-profiles'), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<Array<Record<string, unknown>>>(res, 'Failed to get command profiles')
}

export async function v2GetPermissionTemplates(apiKey: string): Promise<V2PermissionTemplates> {
  const res = await fetch(apiUrl('/v2/security/permission-templates'), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<V2PermissionTemplates>(res, 'Failed to get permission templates')
}
