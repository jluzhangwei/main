export type DeviceProtocol = 'ssh' | 'telnet' | 'api'

export type AutomationLevel = 'read_only' | 'assisted' | 'full_auto'

export type SessionResponse = {
  id: string
  automation_level: AutomationLevel
  status: string
  created_at: string
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
}

export type CommandExecution = {
  id: string
  session_id: string
  step_no: number
  title: string
  command: string
  risk_level: 'low' | 'medium' | 'high'
  status: string
  requires_confirmation: boolean
  output?: string
  error?: string
}

export type Evidence = {
  id: string
  category: string
  conclusion: string
  raw_output: string
  parsed_data: Record<string, unknown>
}

export type DiagnosisSummary = {
  root_cause: string
  impact_scope: string
  recommendation: string
  confidence?: number
  evidence_refs?: Array<Record<string, unknown>>
}

export type Timeline = {
  session: {
    id: string
  }
  messages: ChatMessage[]
  commands: CommandExecution[]
  evidences: Evidence[]
  summary?: DiagnosisSummary
}

export type EventPayload = {
  message?: ChatMessage
  command?: CommandExecution
  summary?: DiagnosisSummary
  reason?: string
}
