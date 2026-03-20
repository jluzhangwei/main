import { Button, Tag } from 'antd'
import type { CommandExecution, DiagnosisSummary, Evidence } from '../types'

type Props = {
  commands: CommandExecution[]
  evidences: Evidence[]
  summary?: DiagnosisSummary
  onRefresh: () => Promise<void>
  onExport: () => Promise<void>
}

const riskColor: Record<string, string> = {
  low: 'green',
  medium: 'orange',
  high: 'red',
}

export function TimelinePanel({ commands, evidences, summary, onRefresh, onExport }: Props) {
  const isQuerySummary = summary?.mode === 'query'
  const isConfigSummary = summary?.mode === 'config'
  const isActionSummary = isQuerySummary || isConfigSummary
  return (
    <div className="timeline-area">
      <div className="timeline-actions">
        <Button onClick={onRefresh}>刷新时间线</Button>
        <Button onClick={onExport}>导出 Markdown</Button>
      </div>

      <h3>最终结果</h3>
      <div className="summary-card">
        {summary ? (
          <>
            {isActionSummary ? (
              <>
                <div className="summary-row">
                  <span className="summary-label">{isConfigSummary ? '配置结果' : '查询结果'}</span>
                  <strong>{summary.query_result || summary.root_cause}</strong>
                </div>
                <div className="summary-row">
                  <span className="summary-label">后续动作</span>
                  <span>{summary.follow_up_action || summary.recommendation}</span>
                </div>
              </>
            ) : (
              <>
                <div className="summary-row">
                  <span className="summary-label">根因</span>
                  <strong>{summary.root_cause}</strong>
                </div>
                <div className="summary-row">
                  <span className="summary-label">影响范围</span>
                  <span>{summary.impact_scope}</span>
                </div>
                <div className="summary-row">
                  <span className="summary-label">建议动作</span>
                  <span>{summary.recommendation}</span>
                </div>
              </>
            )}
            {typeof summary.confidence === 'number' && (
              <div className="summary-row">
                <span className="summary-label">置信度</span>
                <strong>{Math.round(summary.confidence * 100)}%</strong>
              </div>
            )}
            {summary.evidence_refs && summary.evidence_refs.length > 0 && (
              <div className="summary-evidence">
                <span className="summary-label">证据引用</span>
                <div className="summary-evidence-list">
                  {summary.evidence_refs.map((ref, idx) => (
                    <code key={idx}>{JSON.stringify(ref, null, 0)}</code>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="muted">暂无最终结果，请先执行对话。</div>
        )}
      </div>

      <h3>命令回放</h3>
      <div className="list-wrap">
        {commands.length === 0 && <div className="muted">暂无命令执行记录</div>}
        {commands.map((cmd) => (
          <div key={cmd.id} className="list-item">
            <div>
              <strong>{cmd.step_no}. {cmd.title}</strong>
              <code>{cmd.command}</code>
            </div>
            <div>
              <Tag color={riskColor[cmd.risk_level]}>{cmd.risk_level}</Tag>
              <Tag>{cmd.status}</Tag>
            </div>
          </div>
        ))}
      </div>

      <h3>执行证据</h3>
      <div className="list-wrap">
        {evidences.length === 0 && <div className="muted">暂无证据</div>}
        {evidences.map((ev) => (
          <div key={ev.id} className="list-item">
            <div>
              <strong>{ev.category}</strong>
              <div>{ev.conclusion}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
