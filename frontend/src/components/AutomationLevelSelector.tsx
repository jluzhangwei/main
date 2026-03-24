import { Radio } from 'antd'
import type { AutomationLevel } from '../types'

type Props = {
  value: AutomationLevel
  onChange: (next: AutomationLevel) => void
  className?: string
}

export function AutomationLevelSelector({ value, onChange, className }: Props) {
  const levelDetail: Record<
    AutomationLevel,
    {
      title: string
      description: string
      points: string[]
    }
  > = {
    read_only: {
      title: '极低风险',
      description: '仅允许只读采集，不允许配置变更。',
      points: [
        '自动执行：show / display / ping / traceroute 等只读命令。',
        '直接拦截：所有配置变更类命令（如 interface / shutdown / save）。',
        '适合生产网保守排障与证据采集。',
      ],
    },
    assisted: {
      title: '中风险可执行',
      description: '自动执行低/中风险；高风险需要确认。',
      points: [
        '自动执行：低风险 + 中风险命令。',
        '人工确认：高风险命令（高风险由可编辑风险词表判定）。',
        '放行规则可覆盖部分高风险确认。',
        '兼顾效率与安全，推荐日常使用。',
      ],
    },
    full_auto: {
      title: '高风险可执行',
      description: '除阻断规则外，低/中/高风险命令都自动执行。',
      points: [
        '自动执行：低风险 + 中风险 + 高风险命令。',
        '仅阻断规则/硬阻断命中时才会拦截。',
        '适合实验环境或明确授权场景。',
      ],
    },
  }

  const detail = levelDetail[value]

  return (
    <div className={`panel-card ${className || ''}`.trim()}>
      <h3>命令执行控制等级</h3>
      <Radio.Group
        className="mode-selector"
        value={value}
        onChange={(e) => onChange(e.target.value as AutomationLevel)}
        optionType="button"
        buttonStyle="solid"
        options={[
          { label: '极低风险', value: 'read_only' },
          { label: '中风险可执行', value: 'assisted' },
          { label: '高风险可执行', value: 'full_auto' },
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
      <p className="muted mode-hint">
        规则速记：极低风险=仅只读；中风险可执行=低/中自动+高风险确认；高风险可执行=非阻断均自动。
      </p>
    </div>
  )
}
