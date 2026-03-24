import { Radio } from 'antd'
import type { OperationMode } from '../types'

type Props = {
  value: OperationMode
  onChange: (next: OperationMode) => void
  className?: string
}

export function TaskModeSelector({ value, onChange, className }: Props) {
  const modeDetail: Record<
    OperationMode,
    {
      title: string
      description: string
      points: string[]
    }
  > = {
    diagnosis: {
      title: '诊断排障（默认）',
      description: '用于故障定位，AI 按证据链逐步采集并给出根因结论。',
      points: [
        '先查现状，再逐步缩小范围（接口/路由/邻接等）。',
        '以“定位原因”为目标，不会主动扩大为无关变更。',
        '适合端口异常、路由不通、邻居抖动等排障任务。',
      ],
    },
    query: {
      title: '状态查询',
      description: '用于信息查看与状态核对，优先返回结果与必要说明。',
      points: [
        '聚焦“查什么答什么”，例如版本、OSPF 邻居、接口状态。',
        '默认不进入修复流程，避免把查询任务误当诊断。',
        '适合巡检、核验、审计取证等只需结果的场景。',
      ],
    },
    config: {
      title: '⚠️ 配置变更',
      description: '用于执行修复/调整命令，变更行为受命令执行控制等级约束。',
      points: [
        'AI 会先校验证据，再生成可执行命令组推进变更。',
        '高风险命令是否需确认取决于“命令执行控制等级”。',
        '适合明确要修复或调整配置的任务（如 no shutdown）。',
      ],
    },
  }
  const detail = modeDetail[value]

  return (
    <div className={`panel-card ${className || ''}`.trim()}>
      <h3>任务模式</h3>
      <Radio.Group
        className="mode-selector"
        value={value}
        onChange={(e) => onChange(e.target.value as OperationMode)}
        optionType="button"
        buttonStyle="solid"
        options={[
          { value: 'query', label: '状态查询' },
          { value: 'diagnosis', label: '诊断排障（默认）' },
          { value: 'config', label: '⚠️ 配置变更' },
        ]}
      />
      <div className="mode-detail-card">
        <div className="mode-detail-title">{detail.title}</div>
        <p className="muted mode-detail-desc">{detail.description}</p>
        <ul className="mode-detail-list">
          {detail.points.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
      <p className="muted mode-hint mode-session-note">该设置在“创建会话”时生效。</p>
    </div>
  )
}
