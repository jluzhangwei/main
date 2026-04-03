from __future__ import annotations

from app.models.schemas import CommandExecution, CommandStatus, DeviceProtocol, RiskLevel
from app.services.planner_signal_runtime import (
    build_filter_capability_context,
    build_output_compaction_context,
    detect_filter_operators,
)


def _command(
    command: str,
    *,
    output: str = "",
    error: str = "",
    status: CommandStatus = CommandStatus.succeeded,
) -> CommandExecution:
    return CommandExecution(
        session_id="s1",
        step_no=1,
        title="test",
        command=command,
        adapter_type=DeviceProtocol.ssh,
        risk_level=RiskLevel.low,
        status=status,
        output=output,
        error=error,
    )


def test_detect_filter_operators_finds_common_pipe_filters():
    assert detect_filter_operators("display logbuffer | include OSPF") == ["include"]
    assert detect_filter_operators("show interfaces status | section Ethernet2") == ["section"]


def test_build_filter_capability_context_reports_vendor_defaults_and_observed_results():
    commands = [
        _command("show logging | grep OSPF", output="invalid input", error="% Invalid input", status=CommandStatus.failed),
        _command("show interfaces status | include Et2", output="Et2 connected", status=CommandStatus.succeeded),
    ]
    text = build_filter_capability_context(
        vendor="arista",
        version_signature="arista|veos-lab|4.32.4.1m",
        commands=commands,
        problem="检查 Ethernet2 端口 shutdown 状态",
    )
    assert "过滤语法建议" in text
    assert "已验证可用过滤" in text
    assert "include" in text
    assert "已验证失败过滤" in text
    assert "grep" in text
    assert "当前目标对象" in text


def test_build_output_compaction_context_only_appears_for_long_outputs():
    commands = [
        _command("show ip route", output="\n".join(f"line {idx}" for idx in range(80)), status=CommandStatus.succeeded),
    ]
    text = build_output_compaction_context(commands=commands, problem="检查 OSPF 邻居状态")
    assert "输出压缩状态" in text
    assert "过长回显命令" in text
    assert "输出压缩建议" in text
    assert "协议检查规则" in text
    assert "摘要优先规则" in text


def test_build_output_compaction_context_keeps_summary_first_signal_for_short_outputs():
    commands = [
        _command("show clock", output="12:00:01 UTC", status=CommandStatus.succeeded),
    ]
    text = build_output_compaction_context(commands=commands, problem="检查设备时间")
    assert "输出压缩状态" in text
    assert "先摘要后详情" in text
