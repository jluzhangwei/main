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

  return (
    <div className="panel-card">
      <h3>设备连接</h3>
      <Form
        form={form}
        layout="vertical"
        initialValues={{ protocol: 'ssh', operation_mode: 'diagnosis' }}
        onFinish={async (values) => {
          await onCreate({ ...values, automation_level: automationLevel })
        }}
      >
        <Form.Item label="Host" name="host" rules={[{ required: true }]}>
          <Input placeholder="10.0.0.1" />
        </Form.Item>
        <Form.Item label="Protocol" name="protocol" rules={[{ required: true }]}>
          <Select
            options={[
              { value: 'ssh', label: 'SSH CLI' },
              { value: 'telnet', label: 'Telnet CLI' },
              { value: 'api', label: 'API' },
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
        <Form.Item label="Username" name="username">
          <Input />
        </Form.Item>
        <Form.Item label="Password" name="password">
          <Input.Password />
        </Form.Item>
        <Form.Item label="API Token" name="api_token">
          <Input.Password />
        </Form.Item>
        <Button htmlType="submit" type="primary" block>
          创建会话
        </Button>
      </Form>
    </div>
  )
}
