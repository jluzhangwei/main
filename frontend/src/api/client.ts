import type { AutomationLevel, EventPayload, SessionResponse, Timeline } from '../types'

const headers = {
  'Content-Type': 'application/json',
}

export async function createSession(input: {
  host: string
  protocol: 'ssh' | 'telnet' | 'api'
  vendor: string
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
        vendor: input.vendor,
        username: input.username,
        password: input.password,
        api_token: input.api_token,
      },
      automation_level: input.automation_level,
    }),
  })

  if (!res.ok) {
    throw new Error('Failed to create session')
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
