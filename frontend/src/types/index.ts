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
  device_name?: string
  protocol: DeviceProtocol
  automation_level: AutomationLevel
  operation_mode: OperationMode
  status: string
  created_at: string
}

export type SessionStopResponse = {
  session_id: string
  stop_requested: boolean
  adapter_closed: boolean
  running: boolean
  message: string
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
  original_command?: string
  effective_command?: string
  capability_state?: string
  capability_reason?: string
  capability_rule_id?: string
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
      name?: string
      protocol: DeviceProtocol
      version_signature?: string
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
  active_model?: string
  failover_enabled?: boolean
  batch_execution_enabled?: boolean
  model_candidates?: string[]
  last_error?: string
  last_error_code?: string
  unavailable_reason?: string
  last_failover_at?: string
}

export type LLMPromptPolicy = {
  enabled: boolean
  base_url: string
  model: string
  batch_execution_enabled?: boolean
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

export type CommandCapabilityHistoryItem = {
  changed_at: string
  action: 'rewrite' | 'block'
  rewrite_to?: string
  reason_code?: string
  reason_text?: string
}

export type CommandCapabilityRule = {
  id: string
  scope_type: 'version' | 'device' | 'vendor' | 'global'
  scope_key: string
  host?: string
  protocol: DeviceProtocol
  device_type?: string
  vendor?: string
  version_signature?: string
  command_key: string
  action: 'rewrite' | 'block'
  rewrite_to?: string
  reason_code?: string
  reason_text?: string
  source: 'learned' | 'manual'
  enabled: boolean
  hit_count: number
  last_hit_at?: string
  history: CommandCapabilityHistoryItem[]
  created_at: string
  updated_at: string
}

export type CommandCapabilityUpsertRequest = {
  id?: string
  scope_type?: 'version' | 'device' | 'vendor' | 'global'
  host?: string
  protocol?: DeviceProtocol
  device_type?: string
  vendor?: string
  version_signature?: string
  command_key: string
  action: 'rewrite' | 'block'
  rewrite_to?: string
  reason_code?: string
  reason_text?: string
  source?: 'learned' | 'manual'
  enabled?: boolean
}

export type CommandCapabilityResetResponse = {
  removed: number
  remaining: number
}

export type RiskPolicy = {
  high_risk_patterns: string[]
  medium_risk_patterns: string[]
}

export type RiskPolicyUpdateRequest = {
  high_risk_patterns?: string[]
  medium_risk_patterns?: string[]
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
