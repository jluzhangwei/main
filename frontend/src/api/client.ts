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
  ServiceTrace,
  SessionListItem,
  SessionStopResponse,
  SessionResponse,
  Timeline,
  V2ApiKey,
  V2ApiKeyCreateResponse,
  V2JobListResponse,
  V2JobSummary,
  V2JobTimeline,
  V2PermissionTemplates,
} from '../types'

const headers = {
  'Content-Type': 'application/json',
}

const API_BASE = String(
  (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env?.VITE_API_BASE_URL ?? '',
)
  .trim()
  .replace(/\/+$/, '')

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

export async function createSession(input: {
  host: string
  protocol: 'ssh' | 'telnet' | 'api'
  operation_mode: 'diagnosis' | 'query' | 'config'
  username?: string
  password?: string
  jump_host?: string
  jump_port?: number
  jump_username?: string
  jump_password?: string
  api_token?: string
  automation_level: AutomationLevel
}): Promise<SessionResponse> {
  const res = await fetch('/v1/sessions', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      device: {
        host: input.host,
        protocol: input.protocol,
        username: input.username,
        password: input.password,
        jump_host: input.jump_host,
        jump_port: input.jump_port,
        jump_username: input.jump_username,
        jump_password: input.jump_password,
        api_token: input.api_token,
      },
      operation_mode: input.operation_mode,
      automation_level: input.automation_level,
    }),
  })

  if (!res.ok) {
    throw new Error('Failed to create session')
  }

  return res.json()
}

export async function listSessions(): Promise<SessionListItem[]> {
  const res = await fetch('/v1/sessions')
  if (!res.ok) {
    throw new Error('Failed to load sessions')
  }
  return res.json()
}

export async function streamMessage(
  sessionId: string,
  content: string,
  onEvent: (event: string, payload: EventPayload) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/v1/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ content }),
    signal,
  })

  if (!res.ok || !res.body) {
    throw new Error('Failed to stream message')
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

export async function stopSession(sessionId: string): Promise<SessionStopResponse> {
  const res = await fetch(`/v1/sessions/${sessionId}/stop`, {
    method: 'POST',
    headers,
  })
  if (!res.ok) {
    throw new Error('Failed to stop session')
  }
  return res.json()
}

export async function updateSessionAutomation(sessionId: string, automationLevel: AutomationLevel): Promise<SessionResponse> {
  const res = await fetch(`/v1/sessions/${sessionId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify({ automation_level: automationLevel }),
  })

  if (!res.ok) {
    throw new Error('Failed to update automation level')
  }

  return res.json()
}

export async function updateSessionCredentials(
  sessionId: string,
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
  const res = await fetch(`/v1/sessions/${sessionId}/credentials`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    throw new Error('Failed to update session credentials')
  }
  return res.json()
}

export async function confirmCommand(sessionId: string, commandId: string, approved: boolean): Promise<void> {
  const res = await fetch(`/v1/sessions/${sessionId}/commands/${commandId}/confirm`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ approved }),
  })

  if (!res.ok) {
    throw new Error('Failed to confirm command')
  }
}

export async function getTimeline(sessionId: string): Promise<Timeline> {
  const res = await fetch(`/v1/sessions/${sessionId}/timeline`)
  if (!res.ok) {
    throw new Error('Failed to load timeline')
  }
  return res.json()
}

export async function getServiceTrace(sessionId: string): Promise<ServiceTrace> {
  const res = await fetch(`/v1/sessions/${sessionId}/trace`)
  if (!res.ok) {
    throw new Error('Failed to load service trace')
  }
  return res.json()
}

export async function exportMarkdown(sessionId: string): Promise<string> {
  const res = await fetch(`/v1/sessions/${sessionId}/export`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ format: 'markdown' }),
  })

  if (!res.ok) {
    throw new Error('Failed to export report')
  }

  const data = await res.json()
  return data.content as string
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
  model?: string
  baseUrl?: string
  failoverEnabled?: boolean
  batchExecutionEnabled?: boolean
  modelCandidates?: string[]
}): Promise<LLMStatus> {
  const res = await fetch('/v1/llm/config', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      api_key: input.apiKey,
      model: input.model,
      base_url: input.baseUrl,
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

export async function v2CreateJob(apiKey: string, payload: Record<string, unknown>, idempotencyKey?: string): Promise<V2JobSummary> {
  const res = await fetch(apiUrl('/v2/jobs'), {
    method: 'POST',
    headers: v2Headers(apiKey, idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined),
    body: JSON.stringify(payload),
  })
  return parseJsonResponse<V2JobSummary>(res, 'Failed to create v2 job')
}

export async function v2ListJobs(apiKey: string, query?: {
  offset?: number
  limit?: number
  status?: string
  mode?: string
}): Promise<V2JobSummary[]> {
  const params = new URLSearchParams()
  if (query?.offset !== undefined) params.set('offset', String(query.offset))
  if (query?.limit !== undefined) params.set('limit', String(query.limit))
  if (query?.status) params.set('status', query.status)
  if (query?.mode) params.set('mode', query.mode)
  const res = await fetch(apiUrl(`/v2/jobs${params.toString() ? `?${params.toString()}` : ''}`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<V2JobSummary[]>(res, 'Failed to list v2 jobs')
}

export async function v2QueryJobs(apiKey: string, query?: {
  offset?: number
  limit?: number
  status?: string
  mode?: string
}): Promise<V2JobListResponse> {
  const params = new URLSearchParams()
  if (query?.offset !== undefined) params.set('offset', String(query.offset))
  if (query?.limit !== undefined) params.set('limit', String(query.limit))
  if (query?.status) params.set('status', query.status)
  if (query?.mode) params.set('mode', query.mode)
  const res = await fetch(apiUrl(`/v2/jobs/query${params.toString() ? `?${params.toString()}` : ''}`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<V2JobListResponse>(res, 'Failed to query v2 jobs')
}

export async function v2GetJob(apiKey: string, jobId: string): Promise<V2JobSummary> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<V2JobSummary>(res, 'Failed to get v2 job')
}

export async function v2GetJobTimeline(apiKey: string, jobId: string): Promise<V2JobTimeline> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/timeline`), {
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<V2JobTimeline>(res, 'Failed to get v2 job timeline')
}

export async function v2CancelJob(apiKey: string, jobId: string, reason?: string): Promise<V2JobSummary> {
  const query = reason ? `?reason=${encodeURIComponent(reason)}` : ''
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/cancel${query}`), {
    method: 'POST',
    headers: v2Headers(apiKey),
  })
  return parseJsonResponse<V2JobSummary>(res, 'Failed to cancel v2 job')
}

export async function v2ApproveActionGroup(apiKey: string, jobId: string, actionGroupId: string, reason?: string): Promise<void> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/actions/${actionGroupId}/approve`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ reason }),
  })
  await parseJsonResponse<Record<string, unknown>>(res, 'Failed to approve action group')
}

export async function v2RejectActionGroup(apiKey: string, jobId: string, actionGroupId: string, reason?: string): Promise<void> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/actions/${actionGroupId}/reject`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ reason }),
  })
  await parseJsonResponse<Record<string, unknown>>(res, 'Failed to reject action group')
}

export async function v2ApproveActionGroupsBatch(apiKey: string, jobId: string, actionGroupIds: string[], reason?: string): Promise<void> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/actions/approve-batch`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ action_group_ids: actionGroupIds, reason }),
  })
  await parseJsonResponse<Record<string, unknown>>(res, 'Failed to batch approve action groups')
}

export async function v2RejectActionGroupsBatch(apiKey: string, jobId: string, actionGroupIds: string[], reason?: string): Promise<void> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/actions/reject-batch`), {
    method: 'POST',
    headers: v2Headers(apiKey),
    body: JSON.stringify({ action_group_ids: actionGroupIds, reason }),
  })
  await parseJsonResponse<Record<string, unknown>>(res, 'Failed to batch reject action groups')
}

export async function v2UpdateTopology(apiKey: string, jobId: string, payload: {
  edges: Array<{ source: string; target: string; kind?: string; confidence?: number; reason?: string }>
  replace?: boolean
}): Promise<V2JobSummary> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/topology`), {
    method: 'PUT',
    headers: v2Headers(apiKey),
    body: JSON.stringify(payload),
  })
  return parseJsonResponse<V2JobSummary>(res, 'Failed to update job topology')
}

export async function v2UpdateRcaWeights(apiKey: string, jobId: string, payload: {
  rca_weights: {
    anomaly: number
    timing: number
    topology: number
    change: number
    consistency: number
  }
}): Promise<V2JobSummary> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/rca-weights`), {
    method: 'PUT',
    headers: v2Headers(apiKey),
    body: JSON.stringify(payload),
  })
  return parseJsonResponse<V2JobSummary>(res, 'Failed to update rca weights')
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

export async function v2StreamJobEvents(
  apiKey: string,
  jobId: string,
  fromSeq: number,
  onEvent: (event: string, payload: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(apiUrl(`/v2/jobs/${jobId}/events?from_seq=${fromSeq}`), {
    headers: v2Headers(apiKey),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error('Failed to stream v2 events')
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
