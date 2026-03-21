import type {
  AutomationLevel,
  CommandPolicy,
  CommandPolicyUpdateRequest,
  EventPayload,
  LLMPromptPolicy,
  LLMStatus,
  RiskPolicy,
  RiskPolicyUpdateRequest,
  ServiceTrace,
  SessionListItem,
  SessionResponse,
  Timeline,
} from '../types'

const headers = {
  'Content-Type': 'application/json',
}

export async function createSession(input: {
  host: string
  protocol: 'ssh' | 'telnet' | 'api'
  operation_mode: 'diagnosis' | 'query' | 'config'
  username?: string
  password?: string
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
): Promise<void> {
  const res = await fetch(`/v1/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ content }),
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
