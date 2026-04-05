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
  constraint_source?: string
  constraint_reason?: string
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
  nvidia_base_url?: string
  model: string
  active_model?: string
  failover_enabled?: boolean
  batch_execution_enabled?: boolean
  model_candidates?: string[]
  deepseek_enabled?: boolean
  nvidia_enabled?: boolean
  last_error?: string
  last_error_code?: string
  unavailable_reason?: string
  last_failover_at?: string
}

export type LLMPromptPolicy = {
  enabled: boolean
  base_url: string
  nvidia_base_url?: string
  model: string
  batch_execution_enabled?: boolean
  nvidia_enabled?: boolean
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

export type SOPArchiveCommandTemplate = {
  vendor: string
  commands: string[]
}

export type SOPArchiveEntry = {
  id: string
  status?: SOPStatus
  name: string
  summary: string
  usage_hint: string
  trigger_keywords: string[]
  vendor_tags: string[]
  version_signatures: string[]
  preconditions: string[]
  anti_conditions: string[]
  evidence_goals: string[]
  command_templates: SOPArchiveCommandTemplate[]
  fallback_commands: string[]
  expected_findings: string[]
  source_run_ids: string[]
  version: number
  matched_count: number
  referenced_count: number
  success_count: number
  review_notes?: string
  generated_by_model?: string
  generated_by_prompt_version?: string
  published_at?: string
  updated_at?: string
}

export type SOPArchiveResponse = {
  total: number
  matched: SOPArchiveEntry[]
  items: SOPArchiveEntry[]
}

export type SOPStatus = 'draft' | 'published' | 'archived'

export type SOPListResponse = {
  total: number
  items: SOPArchiveEntry[]
}

export type SOPExtractFromRunRequest = {
  run_id: string
  force?: boolean
}

export type SOPUpsertRequest = {
  name: string
  summary: string
  usage_hint: string
  trigger_keywords: string[]
  vendor_tags: string[]
  version_signatures: string[]
  preconditions: string[]
  anti_conditions: string[]
  evidence_goals: string[]
  command_templates: SOPArchiveCommandTemplate[]
  fallback_commands: string[]
  expected_findings: string[]
  source_run_ids: string[]
  generated_by_model?: string
  generated_by_prompt_version?: string
  review_notes?: string
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
  detail_payload?: Record<string, unknown>
}

export type ServiceTrace = {
  session_id: string
  steps: ServiceTraceStep[]
}

export type RunKind = 'single' | 'multi'
export type RunStatus = 'open' | 'running' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled'

export type RunSummary = {
  id: string
  source_id: string
  kind: RunKind
  name?: string
  protocol?: DeviceProtocol
  problem?: string
  status: RunStatus
  phase?: string
  automation_level: AutomationLevel
  operation_mode: OperationMode
  created_at: string
  updated_at?: string
  started_at?: string
  completed_at?: string
  device_count: number
  device_hosts: string[]
  pending_actions: number
  sop_extracted: boolean
  sop_draft_count: number
  sop_published_count: number
  primary_sop_id?: string
}

export type RunListResponse = {
  total: number
  items: RunSummary[]
}

export type RunTimelineResponse = {
  run: RunSummary
  payload: Record<string, unknown>
  trace: Array<Record<string, unknown>>
  timeline: Timeline
  service_trace: ServiceTrace
}

export type RunActionDecisionItem = {
  item_id: string
  status: string
  message: string
}

export type RunActionDecisionResponse = {
  run_id: string
  total: number
  updated: number
  skipped: number
  results: RunActionDecisionItem[]
}

export type RunStopResponse = {
  run_id: string
  source_id: string
  kind: RunKind
  status: RunStatus
  stop_requested: boolean
  message: string
}

export type JobMode = 'diagnosis' | 'inspection' | 'repair'
export type JobStatus = 'queued' | 'running' | 'waiting_approval' | 'executing' | 'completed' | 'failed' | 'cancelled'
export type JobPhase = 'collect' | 'correlate' | 'plan' | 'approve' | 'execute' | 'analyze' | 'conclude'

export type V2ApiKey = {
  id: string
  name: string
  key_prefix: string
  permissions: string[]
  enabled: boolean
  disabled_reason?: string
  expires_at?: string
  created_at: string
  last_used_at?: string
}

export type V2ApiKeyCreateResponse = V2ApiKey & {
  api_key: string
  rotated_from_id?: string
}

export type V2JobSummary = {
  id: string
  name?: string
  problem: string
  mode: JobMode
  status: JobStatus
  phase: JobPhase
  created_at: string
  started_at?: string
  completed_at?: string
  updated_at: string
  device_count: number
  command_count: number
  pending_action_groups: number
  root_device_id?: string
}

export type V2PermissionTemplates = {
  templates: Record<string, string[]>
}
