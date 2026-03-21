import { Radio } from 'antd'
import type { AutomationLevel } from '../types'

type Props = {
  value: AutomationLevel
  onChange: (next: AutomationLevel) => void
}

export function AutomationLevelSelector({ value, onChange }: Props) {
  return (
    <div className="panel-card">
      <h3>自动化等级</h3>
      <Radio.Group
        value={value}
        onChange={(e) => onChange(e.target.value as AutomationLevel)}
        optionType="button"
        buttonStyle="solid"
        options={[
          { label: '只读', value: 'read_only' },
          { label: '半自动', value: 'assisted' },
          { label: '全自动', value: 'full_auto' },
        ]}
      />
      <p className="muted">
        高风险由系统风险词表判定（如 configure terminal/interface/shutdown/no shutdown/save/commit 等）。半自动下未命中放行规则的高风险命令需确认；全自动自动执行非硬阻断命令。
      </p>
    </div>
  )
}
