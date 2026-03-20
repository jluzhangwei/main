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
        只读: 只允许 show/ping；半自动: 高风险命令需确认；全自动: 自动执行全部非硬阻断命令。
      </p>
    </div>
  )
}
