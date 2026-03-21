import { Button, Form, Input, Radio } from 'antd'
import type { AutomationLevel, OperationMode } from '../types'

type Props = {
  automationLevel: AutomationLevel
  className?: string
  onCreate: (payload: {
    host: string
    protocol: 'ssh' | 'telnet' | 'api'
    operation_mode: OperationMode
    username?: string
    password?: string
    api_token?: string
    automation_level: AutomationLevel
  }) => Promise<void>
}

export function DeviceForm({ automationLevel, className, onCreate }: Props) {
  const [form] = Form.useForm()
  const selectedMode = (Form.useWatch('operation_mode', form) as OperationMode | undefined) || 'diagnosis'

  const modeHint: Record<OperationMode, string> = {
    diagnosis: '诊断模式：面向故障定位，AI 会围绕证据收集与根因分析给出结论。',
    query: '查询模式：面向信息获取，优先返回查询结果与必要的后续建议。',
    config: '配置模式：面向变更执行，优先生成并执行配置命令组（按风险策略确认）。',
  }

  return (
    <div className={`panel-card ${className || ''}`.trim()}>
      <h3>设备连接</h3>
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          host: '192.168.0.102',
          username: 'zhangwei',
          password: 'Admin@123',
          operation_mode: 'diagnosis',
        }}
        onFinish={async (values) => {
          await onCreate({
            host: values.host,
            protocol: 'ssh',
            operation_mode: values.operation_mode,
            username: values.username,
            password: values.password,
            automation_level: automationLevel,
          })
        }}
      >
        <Form.Item label="模式" name="operation_mode" rules={[{ required: true }]}>
          <Radio.Group
            className="mode-selector"
            optionType="button"
            buttonStyle="solid"
            options={[
              { value: 'diagnosis', label: '诊断模式' },
              { value: 'query', label: '查询模式' },
              { value: 'config', label: '配置模式' },
            ]}
          />
        </Form.Item>
        <p className="muted mode-hint">{modeHint[selectedMode]}</p>
        <div className="device-inline-fields">
          <Form.Item label="设备地址" name="host" rules={[{ required: true, message: '请输入设备地址' }]}>
            <Input placeholder="192.168.0.102" />
          </Form.Item>
          <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="zhangwei" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input placeholder="Admin@123" />
          </Form.Item>
        </div>
        <Button htmlType="submit" type="primary" block>
          创建会话
        </Button>
      </Form>
    </div>
  )
}
