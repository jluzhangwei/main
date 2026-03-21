import { Button, Modal, Tag } from 'antd'
import type { CommandExecution } from '../types'

type Props = {
  command?: CommandExecution
  onApprove: () => Promise<void>
  onReject: () => Promise<void>
}

export function ConfirmModal({ command, onApprove, onReject }: Props) {
  return (
    <Modal open={Boolean(command)} title="命令执行确认" footer={null} closable={false}>
      {command ? (
        <div>
          <p>该命令需要人工确认后才会执行（可能是高风险或未命中可执行规则）。</p>
          <Tag color="red">{command.risk_level}</Tag>
          <pre className="danger-command">{command.command}</pre>
          <div className="modal-actions">
            <Button danger onClick={onReject}>拒绝执行</Button>
            <Button type="primary" onClick={onApprove}>确认执行</Button>
          </div>
        </div>
      ) : null}
    </Modal>
  )
}
