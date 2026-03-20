from __future__ import annotations

from app.models.schemas import ExportResponse, TimelineResponse


def export_timeline_markdown(timeline: TimelineResponse) -> ExportResponse:
    lines = [
        f"# Session Report {timeline.session.id}",
        "",
        "## Device",
        f"- Host: {timeline.session.device.host}",
        f"- Protocol: {timeline.session.device.protocol}",
        f"- Automation Level: {timeline.session.automation_level}",
        "",
        "## Messages",
    ]

    for message in timeline.messages:
        lines.append(f"- **{message.role}**: {message.content}")

    lines.extend(["", "## Commands"])
    for command in timeline.commands:
        lines.append(f"- `{command.command}` | risk={command.risk_level} | status={command.status}")

    lines.extend(["", "## Evidence"])
    for evidence in timeline.evidences:
        lines.append(f"- {evidence.category}: {evidence.conclusion}")

    if timeline.summary:
        lines.extend(
            [
                "",
                "## Incident Summary",
                f"- Root Cause: {timeline.summary.root_cause}",
                f"- Impact: {timeline.summary.impact_scope}",
                f"- Recommendation: {timeline.summary.recommendation}",
            ]
        )
        if timeline.summary.confidence is not None:
            lines.append(f"- Confidence: {timeline.summary.confidence:.2f}")
        if timeline.summary.evidence_refs:
            lines.append("- Evidence Refs:")
            for ref in timeline.summary.evidence_refs:
                lines.append(f"  - {ref}")

    content = "\n".join(lines)
    return ExportResponse(
        filename=f"session-{timeline.session.id}.md",
        mime_type="text/markdown",
        content=content,
    )
