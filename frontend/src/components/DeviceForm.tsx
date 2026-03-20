import { Button, Form, Input, Select } from 'antd'
import type { AutomationLevel, OperationMode } from '../types'

type Props = {
  automationLevel: AutomationLevel
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

export function DeviceForm({ automationLevel, onCreate }: Props) {
  const [form] = Form.useForm()
  const fixedUsername = 'zhangwei'
  const fixedPassword = 'Admin@123'

  return (
    <div className="panel-card">
      <h3>设备连接</h3>
      <Form
        form={form}
        layout="vertical"
        initialValues={{ host: '192.168.0.101', operation_mode: 'diagnosis' }}
        onFinish={async (values) => {
          await onCreate({
            host: values.host,
            protocol: 'ssh',
            operation_mode: values.operation_mode,
            username: fixedUsername,
            password: fixedPassword,
            automation_level: automationLevel,
          })
        }}
      >
        <Form.Item label="测试设备" name="host" rules={[{ required: true }]}>
          <Select
            options={[
              { value: '192.168.0.101', label: '192.168.0.101' },
              { value: '192.168.0.102', label: '192.168.0.102' },
            ]}
          />
        </Form.Item>
        <Form.Item label="Mode" name="operation_mode" rules={[{ required: true }]}>
          <Select
            options={[
              { value: 'diagnosis', label: '诊断模式' },
              { value: 'query', label: '查询模式' },
              { value: 'config', label: '配置模式' },
            ]}
          />
        </Form.Item>
        <Input value={`SSH / ${fixedUsername}`} disabled />
        <Button htmlType="submit" type="primary" block>
          创建会话
        </Button>
      </Form>
    </div>
  )
}
