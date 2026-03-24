import { Button, Form, Input } from 'antd'
import type { AutomationLevel, OperationMode } from '../types'

type Props = {
  automationLevel: AutomationLevel
  operationMode: OperationMode
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

export function DeviceForm({ automationLevel, operationMode, className, onCreate }: Props) {
  const [form] = Form.useForm()
  const ipv4Pattern = /^(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)){3}$/
  const hostnamePattern = /^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$/

  return (
    <div className={`panel-card ${className || ''}`.trim()}>
      <h3>设备连接</h3>
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          host: '192.168.0.88',
          username: 'zhangwei',
          password: 'Huawei@123',
        }}
        onFinish={async (values) => {
          const host = String(values.host || '').trim()
          const username = String(values.username || '').trim()
          const password = String(values.password || '').trim()
          await onCreate({
            host,
            protocol: 'ssh',
            operation_mode: operationMode,
            username,
            password,
            automation_level: automationLevel,
          })
        }}
      >
        <div className="device-inline-fields">
          <Form.Item
            label="设备地址"
            name="host"
            rules={[
              { required: true, message: '请输入设备地址' },
              {
                validator: async (_, value) => {
                  const normalized = String(value || '').trim()
                  if (!normalized) return
                  if (ipv4Pattern.test(normalized) || hostnamePattern.test(normalized)) return
                  throw new Error('设备地址格式无效，请输入完整 IPv4 或主机名')
                },
              },
            ]}
          >
            <Input placeholder="192.168.0.88" />
          </Form.Item>
          <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="zhangwei" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input placeholder="Huawei@123" />
          </Form.Item>
        </div>
        <Button htmlType="submit" type="primary" block>
          创建会话
        </Button>
        <p className="muted mode-hint">当前任务模式：{operationMode === 'diagnosis' ? '诊断排障' : operationMode === 'query' ? '状态查询' : '⚠️ 配置变更'}</p>
      </Form>
    </div>
  )
}
