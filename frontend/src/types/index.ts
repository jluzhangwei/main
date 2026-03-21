export type DeviceProtocol = 'ssh' | 'telnet' | 'api'

export type AutomationLevel = 'read_only' | 'assisted' | 'full_auto'
export type OperationMode = 'diagnosis' | 'query' | 'config'

export type SessionResponse = {
  id: string
  automation_level: AutomationLevel
  operation_mode: OperationMode
  status: string
  created_at: string
}

export type SessionListItem = {
  id: string
  host: string
  protocol: DeviceProtocol
  automation_level: AutomationLevel
  operation_mode: OperationMode
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
  batch_id?: string
  batch_index?: number
  batch_total?: number
  created_at?: string
  started_at?: string
  completed_at?: string
  duration_ms?: number
}

export type Evidence = {
  id: string
  session_id?: string
  command_id?: string
  category: string
  conclusion: string
  raw_output: string
  parsed_data: Record<string, unknown>
  created_at?: string
}

export type DiagnosisSummary = {
  mode?: 'diagnosis' | 'query' | 'config' | 'unavailable' | 'error'
  root_cause: string
  impact_scope: string
  recommendation: string
  query_result?: string
  follow_up_action?: string
  confidence?: number
  evidence_refs?: Array<Record<string, unknown>>
  created_at?: string
}

export type Timeline = {
  session: {
    id: string
    automation_level: AutomationLevel
    operation_mode: OperationMode
    status: string
    created_at: string
    device: {
      host: string
      protocol: DeviceProtocol
    }
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

export type LLMStatus = {
  enabled: boolean
  base_url: string
  model: string
}

export type LLMPromptPolicy = {
  enabled: boolean
  base_url: string
  model: string
  prompts: Record<string, string>
}

export type CommandPolicy = {
  blocked_patterns: string[]
  executable_patterns: string[]
  legality_check_enabled: boolean
}

export type CommandPolicyUpdateRequest = {
  blocked_patterns?: string[]
  executable_patterns?: string[]
  legality_check_enabled?: boolean
}

export type ServiceTraceStep = {
  id: string
  session_id: string
  seq_no: number
  step_type: string
  title: string
  status: string
  started_at: string
  completed_at?: string
  duration_ms?: number
  command_id?: string
  detail?: string
}

export type ServiceTrace = {
  session_id: string
  steps: ServiceTraceStep[]
}
