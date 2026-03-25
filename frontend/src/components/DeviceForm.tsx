import { Button, Form, Input, Switch } from 'antd'
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
    jump_host?: string
    jump_port?: number
    jump_username?: string
    jump_password?: string
    api_token?: string
    automation_level: AutomationLevel
  }) => Promise<void>
}

export function DeviceForm({ automationLevel, operationMode, className, onCreate }: Props) {
  const [form] = Form.useForm()
  const jumpEnabled = !!Form.useWatch('jump_enabled', form)
  const ipv4Pattern = /^(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)){3}$/
  const hostnamePattern = /^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$/

  return (
    <div className={`panel-card ${className || ''}`.trim()}>
      <h3>设备连接</h3>
      <Form
        form={form}
        layout="vertical"
        initialValues={{ jump_port: 22, jump_enabled: false }}
        onFinish={async (values) => {
          const host = String(values.host || '').trim()
          const username = String(values.username || '').trim()
          const password = String(values.password || '').trim()
          const jumpHost = String(values.jump_host || '').trim()
          const jumpUsername = String(values.jump_username || '').trim()
          const jumpPassword = String(values.jump_password || '').trim()
          const rawJumpPort = Number(values.jump_port)
          const jumpPort = Number.isFinite(rawJumpPort) && rawJumpPort > 0 ? rawJumpPort : 22
          const useJumpHost = Boolean(values.jump_enabled) && !!jumpHost
          await onCreate({
            host,
            protocol: 'ssh',
            operation_mode: operationMode,
            username,
            password,
            jump_host: useJumpHost ? jumpHost : undefined,
            jump_port: useJumpHost ? jumpPort : undefined,
            jump_username: useJumpHost ? jumpUsername || username : undefined,
            jump_password: useJumpHost ? jumpPassword || password : undefined,
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
                  const hosts = normalized
                    .split(/[\n,; ]+/)
                    .map((item) => item.trim())
                    .filter(Boolean)
                  if (hosts.length === 0) return
                  const invalid = hosts.find((item) => !(ipv4Pattern.test(item) || hostnamePattern.test(item)))
                  if (!invalid) return
                  throw new Error(`设备地址格式无效: ${invalid}`)
                },
              },
            ]}
          >
            <Input placeholder="例如 10.0.0.1 或 10.0.0.1,10.0.0.2" />
          </Form.Item>
          <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="输入设备 SSH 用户名" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password placeholder="输入设备 SSH 密码" autoComplete="current-password" />
          </Form.Item>
        </div>
        <div className="jump-switch-row">
          <span>通过跳板机 SSH 连接</span>
          <Form.Item name="jump_enabled" valuePropName="checked" noStyle>
            <Switch />
          </Form.Item>
        </div>
        {jumpEnabled && (
          <div className="device-inline-fields device-inline-fields-4">
            <Form.Item
              label="跳板机地址"
              name="jump_host"
              rules={[
                { required: true, message: '请输入跳板机地址' },
                {
                  validator: async (_, value) => {
                    const normalized = String(value || '').trim()
                    if (!normalized) return
                    if (ipv4Pattern.test(normalized) || hostnamePattern.test(normalized)) return
                    throw new Error('跳板机地址格式无效')
                  },
                },
              ]}
            >
              <Input placeholder="例如 10.0.0.10" />
            </Form.Item>
            <Form.Item
              label="跳板机端口"
              name="jump_port"
              rules={[
                { required: true, message: '请输入端口' },
                {
                  validator: async (_, value) => {
                    const port = Number(value)
                    if (Number.isInteger(port) && port > 0 && port <= 65535) return
                    throw new Error('端口范围应为 1-65535')
                  },
                },
              ]}
            >
              <Input placeholder="22" />
            </Form.Item>
            <Form.Item label="跳板机用户名" name="jump_username">
              <Input placeholder="留空则复用设备用户名" />
            </Form.Item>
            <Form.Item label="跳板机密码" name="jump_password">
              <Input.Password placeholder="留空则复用设备密码" autoComplete="current-password" />
            </Form.Item>
          </div>
        )}
        <Button htmlType="submit" type="primary" block>
          创建会话
        </Button>
        <p className="muted mode-hint">
          当前任务模式：{operationMode === 'diagnosis' ? '诊断排障' : operationMode === 'query' ? '状态查询' : '⚠️ 配置变更'}。
          设备地址支持多台，系统将自动并行执行并汇总。
        </p>
      </Form>
    </div>
  )
}
