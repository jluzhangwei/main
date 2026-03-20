import { Button, Input } from 'antd'
import { useState } from 'react'
import type { ChatMessage } from '../types'

type Props = {
  messages: ChatMessage[]
  disabled: boolean
  onSend: (content: string) => Promise<void>
}

export function ChatPanel({ messages, disabled, onSend }: Props) {
  const [text, setText] = useState('')

  return (
    <div className="chat-area">
      <div className="chat-scroll">
        {messages.map((msg) => (
          <div key={msg.id} className={`chat-bubble chat-${msg.role}`}>
            <strong>{msg.role === 'assistant' ? 'AI' : msg.role === 'user' ? '你' : '系统'}</strong>
            <div>{msg.content}</div>
          </div>
        ))}
      </div>
      <div className="chat-input-row">
        <Input.TextArea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="描述故障现象，例如：核心交换机 ping 不通 10.10.10.1"
        />
        <Button
          type="primary"
          disabled={disabled || !text.trim()}
          onClick={async () => {
            const value = text.trim()
            if (!value) return
            setText('')
            await onSend(value)
          }}
        >
          发送并诊断
        </Button>
      </div>
    </div>
  )
}
