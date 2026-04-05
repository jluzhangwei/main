from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import pwd
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import httpx

from app.models.schemas import CommandExecution, Evidence, IncidentSummary, Session

OUTPUT_COMPACTION_RULES = (
    "为减少命令回显长度并提升执行效率，生成命令时必须优先使用“最小化输出”策略。"
    "优先使用设备支持的过滤语法，例如 include/exclude/begin/section/count/match/grep/regex。"
    "你必须优先从命令设计上压缩输出，而不是依赖系统对回显做二次摘要。"
    "若当前输出过长或信号分散，下一轮应优先改写为带过滤条件的查询命令。"
    "若不确定过滤语法是否支持，先用简短探测命令确认，再选择等效过滤写法。"
    "未验证命令语法前，禁止在同一条命令里叠加多种过滤/截断方式（如 include/grep 与 last/tail/section 同时使用）。"
    "先执行摘要型命令，再按命中结果执行细节命令，避免直接抓取全量输出。"
    "对于状态/存在性检查类问题，必须先使用 brief/summary/count 级命令，再根据摘要结果展开详情。"
    "当用户已给出明确对象（如接口、IP、邻居、协议实例）时，优先生成直接命中该对象的命令；若无法直接命中，优先使用 include/match/grep/count 缩窄范围。"
    "对于协议状态检查（如 OSPF/BGP/ISIS），先查邻居/接口/路由摘要或 count，只有摘要显示异常后才展开配置或明细。"
    "每轮命令应控制在2-5条，且每条命令都要有明确目标，禁止无目标全量采集。"
    "commands数组建议使用对象结构{title, command}，title需简述该命令要验证的信号。"
    "当decision=final时，仅引用关键证据行，禁止粘贴大段原始回显。"
)

RAW_OUTPUT_GROUNDING_RULES = (
    "最终判断必须优先依据命令原始回显（commands.output / error），不要把系统中间提炼摘要当成最终事实。"
    "若某条命令回显仍不足以支撑判断，应继续设计更精准的过滤命令，而不是放大模糊摘要。"
    "当原始回显与任何中间摘要不一致时，必须以原始回显为准。"
)

ROUTE_DELIVERY_CLOSURE_RULES = (
    "当用户问题明确描述为‘设备A收不到设备B的某个目标前缀/网段路由’这类路由传递问题时，"
    "在final前必须确保原始命令回显至少覆盖以下关键验证之一：源端是否存在该目标前缀；源端是否通过目标协议发布/重分发该前缀。"
    "如果尚未覆盖上述关键验证，禁止把任务表述成‘已找到根因’；应继续返回run_command，或在final中明确写出‘尚未执行源端前缀存在性/发布性验证’，不得用笼统结论替代。"
    "已确认邻接正常、且接收端未学习到目标前缀，只能说明传播链存在缺口；这不足以单独构成根因闭环。"
    "如果原始回显已经确认‘源端存在该前缀’，且‘源端目标协议数据库/目标协议路由表中未见该前缀’，必须把这两条作为已确认事实明确写出。"
    "在这种情况下，允许将‘当前证据未显示该前缀被目标协议发布’作为事实化表述，但不得扩写成未被原始回显直接支持的具体配置错误。"
    "该规则只约束结论闭环完整性，不代表预设根因方向；最终判断仍必须完全依据原始命令回显。"
)

FINAL_EXPRESSION_RULES = (
    "当输出final且mode=diagnosis时，只允许优化表达，不允许改变证据支持的方向。"
    "root_cause必须先写已确认事实，再写无法确认的环节；若无法确认唯一根因，必须明确指出仍缺失哪条关键证据。"
    "禁止只写笼统的“证据不足”或大而泛的可能原因列表；若写可能性，必须直接锚定到当前缺失证据。"
    "涉及多设备时，逐设备事实必须逐设备锚定；禁止把单侧证据扩写成‘双方/两端/所有设备’共同状态。"
    "impact_scope只描述已观察到的影响，禁止扩展成未被证据支持的泛化影响。"
    "recommendation或follow_up_action必须直接对应缺失证据的下一步核查动作，避免泛泛地写“检查配置/检查网络”。"
)

PERMISSION_PRECHECK_RULES = (
    "执行查询前必须先评估当前会话是否具备所需执行权限。"
    "在基线识别命令（如show version/display version）完成后，下一步优先检查当前权限级别与会话模式。"
    "若第一次发现权限不足，应优先返回最小必要提权命令并立即复核权限，不要继续无关查询。"
    "对可能受权限限制的命令，先返回权限探测命令（如角色/级别/模式检查），再决定是否继续执行目标命令。"
    "若探测结果显示权限不足，不要继续盲目下发后续命令，应先输出明确的提权或放权需求。"
    "若最近证据已显示在特权/配置模式，禁止重复输出enable或system-view这类提权命令。"
    "当用户可确认放行时，应将待执行命令组一次性给出，避免逐条失败后再补权限。"
    "对Huawei/华为设备，若基线已提供display users或等效会话信息，应优先利用该证据判断会话权限/授权状态；"
    "不要凭空假设display privilege可用，除非已有证据明确显示该命令被设备支持。"
)

ACTION_MARKER_RULES = (
    "当decision=final时，必须在行动建议文本中显式给出状态词。"
    "若仍需执行动作（如继续配置/修复），follow_up_action或recommendation必须包含以下词之一：建议执行、修复、打开、变更。"
    "若任务已闭环且无需继续动作，follow_up_action或recommendation必须包含以下词之一：已完成、无需。"
)

SOP_EXTRACTION_PROMPT_VERSION = "sop-extract-v1"

MINIMAL_CHANGE_RULES = (
    "变更执行遵循通用“最小必要变更”原则："
    "优先闭环当前会话目标与已确认根因，不做机会性扩展修复。"
    "若发现其他潜在问题，可写入follow_up_action/recommendation，但不要在同一轮直接下发无关变更命令。"
    "配置命令必须具备证据锚点：命令中的目标对象需在用户目标或当前会话证据中出现。"
    "若已定位到单一对象（如某接口/邻居/下一跳），后续变更仅允许作用于该对象，禁止扩展到未验证对象。"
    "若要引入新对象或新子域，先返回只读验证命令收集证据，待证据成立后再变更。"
)

BASELINE_CONTEXT_RULES = (
    "系统已在每轮开始前自动执行基线采集：版本识别、设备时钟、会话权限。"
    "你应优先利用这些基线证据做判断，减少重复探测命令。"
    "仅在证据不足或状态可能变化时，再追加必要复核命令。"
)

CAPABILITY_CONTEXT_RULES = (
    "若历史命令结果中已包含 capability_state/capability_reason/constraint_source/constraint_reason，"
    "必须将其视为已验证的命令能力证据。"
    "对于已被标记为block_hit、blocked、command_error、syntax failure、wrong parameter、unrecognized command 的命令，"
    "下一轮禁止重复输出相同命令；如需继续取证，必须改用同厂商等效命令，并在reason中说明替代依据。"
)

VENDOR_COMMAND_FAMILY_RULES = (
    "当会话中已识别厂商/平台/版本（如厂商不为unknown，或存在版本指纹）时，必须遵循命令家族一致性。"
    "Huawei/华为设备优先使用display家族命令；Arista/Cisco-like设备优先使用show家族命令。"
    "除非为会话控制命令（如enable/terminal length/screen-length）或已明确说明兼容探测原因，禁止跨家族盲试。"
    "若出现一次“命令不识别/参数错误”且可判断为家族不匹配，下一轮必须切换到同厂商等效命令，不要重复原家族。"
    "禁止在相邻两轮中反复 show/display 来回切换；仅在证据表明当前家族不可用时才切换，并在reason说明原因。"
)

HISTORY_FORENSICS_RULES = (
    "当用户问题包含“上次/历史/曾经/闪断/flap/间歇”这类历史性故障诉求时："
    "下一轮命令必须优先包含历史取证命令（设备日志、告警、协议邻接变化记录），不能只看当前瞬时状态。"
    "若协议相关（如OSPF/BGP），应优先给出协议事件日志与邻接变化证据采集命令。"
    "历史取证应优先选择稳定、基础、广兼容的命令；在未验证语法前，不要猜测带子参数/后缀的协议历史命令（如 peer history-record、event detail 等）。"
    "对Huawei/华为设备采集历史日志时，优先使用 display logbuffer 或 display logbuffer | include 关键词；"
    "不要在 display logbuffer 后附加 last/tail 之类的 Unix 风格截断片段。"
    "若日志命令不可用或无记录，必须在reason中明确说明“日志证据不足/不可得”，再给出替代取证路径。"
)

SOP_ARCHIVE_RULES = (
    "系统可能提供 SOP 档案候选，它们只是可选参考，不会被系统自动执行。"
    "你必须先判断是否需要调用某个 SOP；若调用，应在reason中说明调用了哪个SOP，并自行决定真正执行的命令。"
    "若你认为 SOP 不适用，可完全忽略并继续自主规划。"
)

MODE_SCOPE_RULES = (
    "你必须严格遵守会话模式边界（字段“会话模式”）："
    "当会话模式为query或diagnosis时，只能输出采集/查询类命令，禁止输出配置变更类命令。"
    "配置变更类命令包括但不限于configure terminal/system-view/interface/shutdown/no shutdown/undo/save/write memory/commit。"
    "当会话模式为config时，允许输出配置变更命令，但必须先给出必要的只读验证，并在变更后给出复核命令。"
    "若当前模式无法完成目标，请在reason中明确说明“需要切换到配置模式”，不要直接越界下发命令。"
)

NEXT_STEP_SYSTEM_PROMPT_WITH_HISTORY = (
    "你是网络故障诊断代理。"
    "你正在同一会话内连续对话，必须结合已有上下文。"
    "你无法访问其他会话，禁止引用其他会话的信息。"
    "你的任务是决定下一步动作。"
    "只输出JSON对象。"
    "字段: decision, title, command, commands, reason, sop_refs, why_use_this_sop, evidence_goal, mode, query_result, follow_up_action, root_cause, impact_scope, recommendation, confidence, evidence_refs。"
    "decision只能是run_command或final。"
    "run_command时优先使用commands（数组，最多5条）；仅在确实只有单条且无需分组时才使用command。"
    "commands每项可为字符串，或对象{title, command}。"
    "final时如果是查询任务，mode=query且必须给出query_result，可选follow_up_action；"
    "final时如果是配置任务，mode=config且必须给出query_result，可选follow_up_action；"
    "final时如果是诊断任务，mode=diagnosis且必须给出root_cause, impact_scope, recommendation。"
    "confidence是0到1。evidence_refs是数组，quote应来自会话中的证据输出。"
    "若要执行配置命令，必须先有只读取证证明目标对象存在且状态明确。"
    "当用户未明确提供对象标识（如具体接口名）时，禁止直接输出配置命令，必须先输出只读发现命令。"
    "禁止凭空假设接口名（如Ethernet1/Gi1/0/1）并直接下发配置。"
    f"{OUTPUT_COMPACTION_RULES}"
    f"{PERMISSION_PRECHECK_RULES}"
    f"{MODE_SCOPE_RULES}"
    f"{ACTION_MARKER_RULES}"
    f"{MINIMAL_CHANGE_RULES}"
    f"{BASELINE_CONTEXT_RULES}"
    f"{CAPABILITY_CONTEXT_RULES}"
    f"{VENDOR_COMMAND_FAMILY_RULES}"
    f"{HISTORY_FORENSICS_RULES}"
    f"{SOP_ARCHIVE_RULES}"
    f"{RAW_OUTPUT_GROUNDING_RULES}"
    f"{FINAL_EXPRESSION_RULES}"
    f"{ROUTE_DELIVERY_CLOSURE_RULES}"
)

NEXT_STEP_SYSTEM_PROMPT = (
    "你是网络故障诊断代理。"
    "任务是基于用户问题和已有证据，决定下一步动作。"
    "你可以自由决定诊断路径，不使用固定剧本。"
    "只输出JSON对象。"
    "字段: decision, title, command, commands, reason, sop_refs, why_use_this_sop, evidence_goal, mode, query_result, follow_up_action, root_cause, impact_scope, recommendation, confidence, evidence_refs。"
    "decision只能是run_command或final。"
    "当decision为run_command时，优先使用commands（数组，最多5条）；仅在确实只有单条且无需分组时才使用command。优先只读排查命令。"
    "commands每项可为字符串，或对象{title, command}。"
    "当decision为final时，如果是查询任务，mode=query且必须给出query_result；"
    "当decision为final时，如果是配置任务，mode=config且必须给出query_result；"
    "当decision为final时，如果是诊断任务，mode=diagnosis且必须给出root_cause, impact_scope, recommendation。"
    "confidence是0到1。evidence_refs是数组，且quote必须来自已有证据原文。"
    "若要执行配置命令，必须先有只读取证证明目标对象存在且状态明确。"
    "当用户未明确提供对象标识（如具体接口名）时，禁止直接输出配置命令，必须先输出只读发现命令。"
    "禁止凭空假设接口名（如Ethernet1/Gi1/0/1）并直接下发配置。"
    f"{OUTPUT_COMPACTION_RULES}"
    f"{PERMISSION_PRECHECK_RULES}"
    f"{MODE_SCOPE_RULES}"
    f"{ACTION_MARKER_RULES}"
    f"{MINIMAL_CHANGE_RULES}"
    f"{BASELINE_CONTEXT_RULES}"
    f"{CAPABILITY_CONTEXT_RULES}"
    f"{VENDOR_COMMAND_FAMILY_RULES}"
    f"{HISTORY_FORENSICS_RULES}"
    f"{SOP_ARCHIVE_RULES}"
    f"{RAW_OUTPUT_GROUNDING_RULES}"
    f"{FINAL_EXPRESSION_RULES}"
    f"{ROUTE_DELIVERY_CLOSURE_RULES}"
)

PRIMARY_SUMMARY_SYSTEM_PROMPT = (
    "你是网络故障诊断引擎。"
    "严格依据输入证据判断，不得猜测。"
    "若证据不足以确认根因，必须明确说明不确定。"
    f"{RAW_OUTPUT_GROUNDING_RULES}"
    f"{FINAL_EXPRESSION_RULES}"
    f"{ROUTE_DELIVERY_CLOSURE_RULES}"
    "只输出JSON对象。"
    "字段必须是: root_cause, impact_scope, recommendation, confidence, evidence_refs。"
    "confidence是0到1的小数。"
    "evidence_refs是数组，每项包含 command_step, quote, why。quote必须是输入证据中的原文片段。"
)

REVIEW_SYSTEM_PROMPT = (
    "你是网络诊断结果审稿器。"
    "只能依据证据审查，不得引入新事实。"
    "如果candidate缺证据支撑，给出fail并可附corrected_summary。"
    "只输出JSON对象。"
)

REWRITE_SYSTEM_PROMPT = (
    "你是网络诊断改写器。"
    "只依据证据，修正不被支持的结论。"
    "只输出JSON对象。"
)

SOP_EXTRACTION_SYSTEM_PROMPT = (
    "你是网络故障知识库提炼器。"
    "任务是从一次真实诊断会话中提炼可复用的SOP草稿。"
    "目标不是复述会话，而是抽取未来可复用的方法。"
    "严格依据输入证据，不得编造未出现的对象、命令或结论。"
    "只输出JSON对象。"
    "字段必须是: name, summary, usage_hint, trigger_keywords, vendor_tags, version_signatures, preconditions, anti_conditions, evidence_goals, command_templates, fallback_commands, expected_findings, review_notes。"
    "command_templates必须是数组，每项结构为{vendor, commands}。"
    "trigger_keywords应面向未来检索，不要只是抄用户原句。"
    "preconditions描述适用前提；anti_conditions描述不应调用该SOP的条件。"
    "evidence_goals描述该SOP期望验证到的关键信号。"
    "command_templates只保留真正有复用价值的最小必要命令组，禁止收录明显试错命令。"
    "fallback_commands是替代性简化命令；expected_findings是调用后期望观察到的现象。"
    "review_notes要明确指出人工审核时需要重点留意什么。"
)


class DeepSeekDiagnoser:
    def __init__(self) -> None:
        self.default_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
        self.default_nvidia_base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip().rstrip("/")
        self.default_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.nvidia_api_key = os.getenv("NVIDIA_API_KEY", "").strip()
        self.base_url = self.default_base_url
        self.nvidia_base_url = self.default_nvidia_base_url
        self.model = self.default_model
        self.failover_enabled = os.getenv("NETOPS_MODEL_FAILOVER_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
        self.batch_execution_enabled = (
            os.getenv("NETOPS_BATCH_EXECUTION_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
        )
        self.model_candidates = self._normalize_model_candidates(
            (os.getenv("DEEPSEEK_MODEL_CANDIDATES", "deepseek-chat,deepseek-reasoner")).split(",")
        )
        if self.model:
            self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
        self.active_model: Optional[str] = self.model
        self.last_error: Optional[str] = None
        self.last_error_code: Optional[str] = None
        self.last_failover_at: Optional[datetime] = None
        self.timeout = float(os.getenv("DEEPSEEK_TIMEOUT", "30"))
        env_config_path = (os.getenv("NETOPS_LLM_CONFIG_PATH") or "").strip()
        self.config_path = Path(env_config_path).expanduser() if env_config_path else self._default_config_path()
        self.legacy_config_path = Path(tempfile.gettempdir()) / "netops_ai_v1_llm_config.json"
        if self._should_load_saved_config():
            self._load_saved_config()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.nvidia_api_key)

    def configure(
        self,
        *,
        api_key: Optional[str] = None,
        nvidia_api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        nvidia_base_url: Optional[str] = None,
        model: Optional[str] = None,
        failover_enabled: Optional[bool] = None,
        model_candidates: Optional[list[str]] = None,
        batch_execution_enabled: Optional[bool] = None,
    ) -> None:
        if api_key is not None:
            self.api_key = api_key.strip()
        if nvidia_api_key is not None:
            self.nvidia_api_key = nvidia_api_key.strip()
        if base_url:
            self.base_url = base_url.strip().rstrip("/")
        if nvidia_base_url:
            self.nvidia_base_url = nvidia_base_url.strip().rstrip("/")
        if model:
            self.model = model.strip()
        if failover_enabled is not None:
            self.failover_enabled = bool(failover_enabled)
        if batch_execution_enabled is not None:
            self.batch_execution_enabled = bool(batch_execution_enabled)
        if model_candidates is not None:
            self.model_candidates = self._normalize_model_candidates(model_candidates)
        self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
        self.active_model = self.model
        self.last_error = None
        self.last_error_code = None
        self._save_config()

    def delete_saved_config(self) -> None:
        self.api_key = ""
        self.nvidia_api_key = ""
        self.base_url = self.default_base_url
        self.nvidia_base_url = self.default_nvidia_base_url
        self.model = self.default_model
        self.active_model = self.default_model
        self.last_error = None
        self.last_error_code = None
        self.last_failover_at = None
        try:
            if self.config_path.exists():
                self.config_path.unlink()
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "nvidia_base_url": self.nvidia_base_url,
            "model": self.model,
            "active_model": self.active_model or self.model,
            "failover_enabled": self.failover_enabled,
            "batch_execution_enabled": self.batch_execution_enabled,
            "model_candidates": list(self.model_candidates),
            "deepseek_enabled": bool(self.api_key),
            "nvidia_enabled": bool(self.nvidia_api_key),
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
            "unavailable_reason": self._unavailable_reason(),
            "last_failover_at": self.last_failover_at,
        }

    def prompt_strategy(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "nvidia_base_url": self.nvidia_base_url,
            "model": self.model,
            "active_model": self.active_model or self.model,
            "failover_enabled": self.failover_enabled,
            "batch_execution_enabled": self.batch_execution_enabled,
            "model_candidates": list(self.model_candidates),
            "nvidia_enabled": bool(self.nvidia_api_key),
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
            "prompts": {
                "next_step_history": self._next_step_prompt(with_history=True),
                "next_step_default": self._next_step_prompt(with_history=False),
                "summary_primary": PRIMARY_SUMMARY_SYSTEM_PROMPT,
                "summary_review": REVIEW_SYSTEM_PROMPT,
                "summary_rewrite": REWRITE_SYSTEM_PROMPT,
            },
        }

    def _load_saved_config(self) -> None:
        data = self._read_config(self.config_path)
        loaded_from = self.config_path
        if data is None and self.legacy_config_path != self.config_path:
            data = self._read_config(self.legacy_config_path)
            loaded_from = self.legacy_config_path
        if data is None:
            return
        self._apply_loaded_config(data)

        # Migrate legacy temp-file config to persistent default location.
        if loaded_from == self.legacy_config_path and self.config_path != self.legacy_config_path:
            self._save_config()
            try:
                self.legacy_config_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _save_config(self) -> None:
        payload = {
            "api_key": self.api_key,
            "nvidia_api_key": self.nvidia_api_key,
            "base_url": self.base_url,
            "nvidia_base_url": self.nvidia_base_url,
            "model": self.model,
            "failover_enabled": self.failover_enabled,
            "batch_execution_enabled": self.batch_execution_enabled,
            "model_candidates": list(self.model_candidates),
        }
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(self.config_path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def _default_config_path(self) -> Path:
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "llm_config.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_llm_config.json"

    def _read_config(self, path: Path) -> Optional[dict[str, Any]]:
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def _apply_loaded_config(self, data: dict[str, Any]) -> None:
        api_key = str(data.get("api_key", "")).strip()
        nvidia_api_key = str(data.get("nvidia_api_key", "")).strip()
        base_url = str(data.get("base_url", "")).strip().rstrip("/")
        nvidia_base_url = str(data.get("nvidia_base_url", "")).strip().rstrip("/")
        model = str(data.get("model", "")).strip()
        failover_enabled = data.get("failover_enabled")
        batch_execution_enabled = data.get("batch_execution_enabled")
        candidates = data.get("model_candidates")
        if api_key:
            self.api_key = api_key
        if nvidia_api_key:
            self.nvidia_api_key = nvidia_api_key
        if base_url:
            self.base_url = base_url
        if nvidia_base_url:
            self.nvidia_base_url = nvidia_base_url
        if model:
            self.model = model
        if isinstance(failover_enabled, bool):
            self.failover_enabled = failover_enabled
        if isinstance(batch_execution_enabled, bool):
            self.batch_execution_enabled = batch_execution_enabled
        if isinstance(candidates, list):
            self.model_candidates = self._normalize_model_candidates([str(item) for item in candidates])
        self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
        self.active_model = self.model

    def _should_load_saved_config(self) -> bool:
        if os.getenv("NETOPS_DISABLE_LLM_CONFIG_LOAD", "").strip().lower() in {"1", "true", "yes"}:
            return False
        if os.getenv("PYTEST_CURRENT_TEST"):
            explicit_config_path = (os.getenv("NETOPS_LLM_CONFIG_PATH") or "").strip()
            if explicit_config_path:
                return True
            return self.config_path != self._real_user_default_config_path()
        return True

    def _real_user_default_config_path(self) -> Path:
        try:
            home_dir = Path(pwd.getpwuid(os.getuid()).pw_dir).expanduser()
        except Exception:
            home_dir = Path.home().expanduser()
        return home_dir / ".netops-ai-v1" / "llm_config.json"

    async def diagnose(
        self,
        session: Session,
        commands: list[CommandExecution],
        evidences: list[Evidence],
    ) -> Optional[IncidentSummary]:
        if not self.enabled:
            return None

        payload = self._build_payload(session, commands, evidences)

        primary = await self._run_primary(payload)
        if primary is None:
            return None

        review = await self._run_review(payload, primary)
        if review is None:
            return self._to_incident_summary(session.id, primary)

        if review.get("verdict") == "pass":
            return self._to_incident_summary(session.id, primary)

        corrected_summary = review.get("corrected_summary")
        candidate = corrected_summary if isinstance(corrected_summary, dict) else None

        if candidate is None:
            candidate = await self._run_rewrite(payload, primary, review.get("issues", []))
            if candidate is None:
                return self._to_incident_summary(session.id, primary)

        second_review = await self._run_review(payload, candidate)
        if second_review is None:
            return self._to_incident_summary(session.id, candidate)
        if second_review.get("verdict") != "pass":
            return self._to_incident_summary(session.id, candidate)

        return self._to_incident_summary(session.id, candidate)

    async def propose_next_step(
        self,
        *,
        session: Session,
        user_problem: str,
        commands: list[CommandExecution],
        evidences: list[Evidence],
        iteration: int,
        max_iterations: int,
        conversation_history: Optional[list[dict[str, str]]] = None,
        planner_context: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        plan, _ = await self.propose_next_step_with_debug(
            session=session,
            user_problem=user_problem,
            commands=commands,
            evidences=evidences,
            iteration=iteration,
            max_iterations=max_iterations,
            conversation_history=conversation_history,
            planner_context=planner_context,
        )
        return plan

    async def propose_next_step_with_debug(
        self,
        *,
        session: Session,
        user_problem: str,
        commands: list[CommandExecution],
        evidences: list[Evidence],
        iteration: int,
        max_iterations: int,
        conversation_history: Optional[list[dict[str, str]]] = None,
        planner_context: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
        debug: dict[str, Any] = {
            "iteration": iteration,
            "max_iterations": max_iterations,
            "with_history": bool(conversation_history),
        }
        if not self.enabled:
            debug["error"] = "llm_disabled"
            return None, debug

        if conversation_history:
            system_prompt = self._next_step_prompt(with_history=True)
            history = self._normalize_history_messages(conversation_history)
            debug["system_prompt"] = self._clip_trace_text(system_prompt, 200000)
            debug["request_messages"] = [
                {
                    "role": item.get("role", ""),
                    "content": self._clip_trace_text(item.get("content", ""), 200000),
                }
                for item in [{"role": "system", "content": system_prompt}, *history]
            ]
            content = await self._chat_completion_messages(
                system_prompt=system_prompt,
                messages=history,
            )
            debug["raw_response"] = self._clip_trace_text(content, 200000)
            if not content:
                debug["error"] = "empty_response"
                return None, debug
            parsed = self._parse_json_object(content)
            if not parsed:
                debug["error"] = "unparseable_json"
                return None, debug
            decision = str(parsed.get("decision", "")).strip().lower()
            if decision not in {"run_command", "final"}:
                debug["error"] = f"invalid_decision:{decision}"
                debug["parsed_response"] = parsed
                return None, debug
            parsed["decision"] = decision
            promoted = self._promote_final_to_run_command_if_actionable(
                parsed,
                iteration=iteration,
                max_iterations=max_iterations,
            )
            if promoted is not None:
                debug["promotion"] = promoted
                parsed = promoted["plan"]
            debug["parsed_response"] = parsed
            return parsed, debug

        payload = self._build_next_step_payload(
            session=session,
            user_problem=user_problem,
            commands=commands,
            evidences=evidences,
            iteration=iteration,
            max_iterations=max_iterations,
            planner_context=planner_context,
        )
        system_prompt = self._next_step_prompt(with_history=False)
        debug["system_prompt"] = self._clip_trace_text(system_prompt, 200000)
        debug["request_payload"] = payload

        content = await self._chat_completion(
            system_prompt=system_prompt,
            user_payload=payload,
        )
        debug["raw_response"] = self._clip_trace_text(content, 200000)
        if not content:
            debug["error"] = "empty_response"
            return None, debug
        parsed = self._parse_json_object(content)
        if not parsed:
            debug["error"] = "unparseable_json"
            return None, debug

        decision = str(parsed.get("decision", "")).strip().lower()
        if decision not in {"run_command", "final"}:
            debug["error"] = f"invalid_decision:{decision}"
            debug["parsed_response"] = parsed
            return None, debug
        parsed["decision"] = decision
        promoted = self._promote_final_to_run_command_if_actionable(
            parsed,
            iteration=iteration,
            max_iterations=max_iterations,
        )
        if promoted is not None:
            debug["promotion"] = promoted
            parsed = promoted["plan"]
        debug["parsed_response"] = parsed
        return parsed, debug

    def _promote_final_to_run_command_if_actionable(
        self,
        plan: dict[str, Any],
        *,
        iteration: int,
        max_iterations: int,
    ) -> dict[str, Any] | None:
        if iteration >= max_iterations:
            return None
        if str(plan.get("decision", "")).strip().lower() != "final":
            return None

        mode = str(plan.get("mode", "")).strip().lower()
        if mode and mode != "diagnosis":
            return None

        root_cause = str(plan.get("root_cause") or "")
        recommendation = str(plan.get("recommendation") or "")
        follow_up_action = str(plan.get("follow_up_action") or "")
        reason = str(plan.get("reason") or "")
        combined = "\n".join(part for part in (root_cause, recommendation, follow_up_action, reason) if part).lower()

        missing_markers = (
            "证据不足",
            "不确定",
            "未验证",
            "尚未执行",
            "缺失证据",
            "无法确定",
            "insufficient evidence",
            "uncertain",
            "not verified",
            "not executed",
            "missing evidence",
            "cannot determine",
        )
        if not any(marker in combined for marker in missing_markers):
            return None

        extracted = self._extract_actionable_commands_from_text("\n".join([recommendation, follow_up_action]))
        if not extracted:
            return None

        promoted_plan = dict(plan)
        promoted_plan["decision"] = "run_command"
        promoted_plan.pop("query_result", None)
        promoted_plan["title"] = str(plan.get("title") or "补充关键验证").strip() or "补充关键验证"
        promoted_plan["reason"] = (
            str(plan.get("reason") or "").strip()
            + ("；" if str(plan.get("reason") or "").strip() else "")
            + "当前final明确承认仍缺关键验证，已按AI给出的建议命令继续执行补证。"
        ).strip("；")
        promoted_plan["commands"] = [
            {"title": title, "command": command}
            for title, command in extracted[:5]
        ]
        return {
            "trigger": "final_with_actionable_missing_evidence",
            "commands": [command for _title, command in extracted[:5]],
            "plan": promoted_plan,
        }

    def _extract_actionable_commands_from_text(self, text: str) -> list[tuple[str, str]]:
        content = str(text or "").strip()
        if not content:
            return []
        matches = re.findall(r"[`'\"“”‘’]([^`'\"“”‘’]{3,160})[`'\"“”‘’]", content)
        commands: list[str] = []
        seen: set[str] = set()
        for raw in matches:
            candidate = str(raw).strip()
            lowered = candidate.lower()
            if not candidate or " " not in candidate:
                continue
            if not re.match(r"^(show|display|ping|traceroute|enable|terminal|screen-length|show run|display current-configuration)\b", lowered):
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            commands.append(candidate)
        return [(f"补充关键验证 {idx+1}", command) for idx, command in enumerate(commands[:5])]

    async def extract_sop_draft(
        self,
        *,
        run_payload: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        if not self.enabled:
            return None
        content = await self._chat_completion(
            system_prompt=SOP_EXTRACTION_SYSTEM_PROMPT,
            user_payload=run_payload,
        )
        if not content:
            return None
        return self._parse_json_object(content)

    def _build_payload(
        self,
        session: Session,
        commands: list[CommandExecution],
        evidences: list[Evidence],
    ) -> dict[str, Any]:
        selected_commands = self._select_relevant_commands(commands)
        return {
            "session": {
                "id": session.id,
                "vendor": session.device.vendor,
                "protocol": session.device.protocol.value,
                "issue_scope": session.issue_scope,
            },
            "commands": [
                {
                    "step_no": cmd.step_no,
                    "title": cmd.title,
                    "command": cmd.command,
                    "status": cmd.status.value,
                    "risk_level": cmd.risk_level.value,
                    "output": self._compress_output_for_llm(cmd.output, limit=1400),
                    "error": cmd.error,
                }
                for cmd in selected_commands
            ],
            "evidences": [],
            "evidence_handling": "ignore_intermediate_summaries_use_raw_command_outputs",
            "task": (
                "请给出根因、影响范围、建议。"
                "只能依据证据，不得增加未出现的假设。"
            ),
        }

    def _build_next_step_payload(
        self,
        *,
        session: Session,
        user_problem: str,
        commands: list[CommandExecution],
        evidences: list[Evidence],
        iteration: int,
        max_iterations: int,
        planner_context: Optional[str] = None,
    ) -> dict[str, Any]:
        selected_commands = self._select_relevant_commands(commands)
        payload = {
            "session": {
                "id": session.id,
                "vendor": session.device.vendor,
                "protocol": session.device.protocol.value,
            },
            "user_problem": user_problem,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "commands": [
                {
                    "step_no": cmd.step_no,
                    "title": cmd.title,
                    "command": cmd.command,
                    "original_command": getattr(cmd, "original_command", None),
                    "effective_command": getattr(cmd, "effective_command", None),
                    "status": cmd.status.value,
                    "output": self._compress_output_for_llm(cmd.output, limit=1400),
                    "error": cmd.error,
                    "capability_state": getattr(cmd, "capability_state", None),
                    "capability_reason": getattr(cmd, "capability_reason", None),
                    "constraint_source": getattr(cmd, "constraint_source", None),
                    "constraint_reason": getattr(cmd, "constraint_reason", None),
                }
                for cmd in selected_commands
            ],
            "evidences": [],
            "evidence_handling": "ignore_intermediate_summaries_use_raw_command_outputs",
        }
        if str(planner_context or "").strip():
            payload["planner_context"] = str(planner_context).strip()
        return payload

    async def _run_primary(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        content = await self._chat_completion(
            system_prompt=PRIMARY_SUMMARY_SYSTEM_PROMPT,
            user_payload=payload,
        )
        if not content:
            return None
        parsed = self._parse_json_object(content)
        if not parsed:
            return None
        return parsed

    async def _run_review(self, payload: dict[str, Any], candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
        review_payload = {
            "evidence_package": payload,
            "candidate_summary": candidate,
            "review_task": (
                "审查candidate_summary是否完全由证据支持。"
                "如果evidence_refs中的quote在证据里找不到，必须判定fail。"
                "仅输出JSON对象，字段: verdict, issues, corrected_summary。"
                "verdict只能是pass或fail。"
                "issues是字符串数组。"
                "corrected_summary要么是null，要么是与candidate同结构的JSON。"
            ),
        }
        content = await self._chat_completion(
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_payload=review_payload,
        )
        if not content:
            return None
        parsed = self._parse_json_object(content)
        if not parsed:
            return None
        verdict = str(parsed.get("verdict", "")).strip().lower()
        if verdict not in {"pass", "fail"}:
            return None
        parsed["verdict"] = verdict
        if "issues" not in parsed or not isinstance(parsed["issues"], list):
            parsed["issues"] = []
        return parsed

    async def _run_rewrite(
        self,
        payload: dict[str, Any],
        previous_summary: dict[str, Any],
        issues: list[Any],
    ) -> Optional[dict[str, Any]]:
        rewrite_payload = {
            "evidence_package": payload,
            "previous_summary": previous_summary,
            "issues": issues,
            "task": (
                "根据issues重写诊断结果。"
                "只输出JSON对象。"
                "字段必须是: root_cause, impact_scope, recommendation, confidence, evidence_refs。"
            ),
        }
        content = await self._chat_completion(
            system_prompt=REWRITE_SYSTEM_PROMPT,
            user_payload=rewrite_payload,
        )
        if not content:
            return None
        return self._parse_json_object(content)

    async def _chat_completion(self, *, system_prompt: str, user_payload: dict[str, Any]) -> str:
        request_body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        data = await self._post_json(request_body)
        if data:
            return self._extract_content(data)

        # Compatibility retry for providers that do not support response_format.
        request_body.pop("response_format", None)
        data = await self._post_json(request_body)
        if not data:
            return ""
        return self._extract_content(data)

    async def _chat_completion_messages(self, *, system_prompt: str, messages: list[dict[str, str]]) -> str:
        history = self._normalize_history_messages(messages)

        request_body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system_prompt}, *history],
        }
        data = await self._post_json(request_body)
        if data:
            return self._extract_content(data)

        request_body.pop("response_format", None)
        data = await self._post_json(request_body)
        if not data:
            return ""
        return self._extract_content(data)

    def _normalize_history_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        history = []
        for item in messages[-12:]:
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            history.append({"role": role, "content": self._clip_trace_text(content, 1600)})
        return history

    def _compress_output_for_llm(self, text: Any, *, limit: int = 1400) -> str:
        value = str(text or "")
        if len(value) <= limit:
            return value
        lines = value.splitlines()
        if len(lines) <= 12:
            return self._clip_trace_text(value, limit)
        head = lines[:5]
        tail = lines[-5:]
        kept = "\n".join([*head, "...(omitted middle lines)...", *tail])
        return self._clip_trace_text(kept, limit)

    def _select_relevant_commands(self, commands: list[CommandExecution]) -> list[CommandExecution]:
        if not commands:
            return []
        recent = commands[-6:]
        failed: list[CommandExecution] = []
        constrained: list[CommandExecution] = []
        filtered_success: list[CommandExecution] = []
        for cmd in reversed(commands):
            status = str(getattr(cmd.status, "value", cmd.status) or "").strip().lower()
            command_text = str(getattr(cmd, "effective_command", None) or cmd.command or "")
            if status in {"failed", "blocked", "rejected"} and len(failed) < 2:
                failed.append(cmd)
            if (getattr(cmd, "capability_state", None) or getattr(cmd, "constraint_source", None)) and len(constrained) < 2:
                constrained.append(cmd)
            if any(token in command_text.lower() for token in ("| include", "| exclude", "| begin", "| section", "| match", "| grep", "| count")) and len(filtered_success) < 2:
                filtered_success.append(cmd)
            if len(failed) >= 2 and len(constrained) >= 2 and len(filtered_success) >= 2:
                break
        picked = [*recent, *reversed(failed), *reversed(constrained), *reversed(filtered_success)]
        out: list[CommandExecution] = []
        seen: set[str] = set()
        for cmd in picked:
            key = str(getattr(cmd, "id", "") or f"{cmd.step_no}:{cmd.command}")
            if key in seen:
                continue
            seen.add(key)
            out.append(cmd)
        return out[-10:]

    def _clip_trace_text(self, value: Any, limit: int) -> str:
        text = str(value or "")
        if limit <= 0:
            return text
        if len(text) <= limit:
            return text
        return f"{text[:limit]}...(truncated,{len(text)} chars)"

    async def _post_json(self, request_body: dict[str, Any]) -> Optional[dict[str, Any]]:
        requested = str(request_body.get("model") or self.model).strip() or self.default_model
        models = self._candidate_model_order(requested)
        if not self.failover_enabled and models:
            models = [models[0]]

        first_model = models[0] if models else requested
        first_error: Optional[str] = None
        first_error_code: Optional[str] = None
        for idx, model in enumerate(models):
            body = dict(request_body)
            body["model"] = model
            data, error, error_code = await self._post_json_once(body)
            if data is not None:
                self.active_model = model
                if idx > 0:
                    self.last_failover_at = datetime.now(timezone.utc)
                    self.last_error = (
                        f"Model failover: {first_model} -> {model}. "
                        f"Root error: {(first_error or error or 'unknown')[:260]}"
                    )
                    self.last_error_code = first_error_code or error_code or "model_failover"
                    # Keep system stable after successful failover.
                    self.model = model
                    self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
                    self._save_config()
                else:
                    self.last_error = None
                    self.last_error_code = None
                return data
            if idx == 0:
                first_error = error
                first_error_code = error_code

        self.active_model = first_model
        self.last_error = (first_error or "LLM request failed")[:300]
        self.last_error_code = first_error_code or "llm_request_failed"
        return None

    async def _post_json_once(
        self, request_body: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[str]]:
        model = str(request_body.get("model") or "").strip()
        request_base_url = self._resolve_request_base_url(model=model)
        request_api_key = self._resolve_request_api_key(model=model, base_url=request_base_url)
        if not request_api_key:
            return None, f"[{model}] missing api key", "api_key_missing"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{request_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {request_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
                if resp.status_code >= 400:
                    snippet = (resp.text or "").strip().replace("\n", " ")
                    code = "provider_http_error"
                    if resp.status_code in {401, 403}:
                        code = "auth_error"
                    elif resp.status_code == 429:
                        code = "rate_limit"
                    elif resp.status_code >= 500:
                        code = "provider_unavailable"
                    return None, f"[{model}] HTTP {resp.status_code}: {snippet[:220]}", code
                data = resp.json()
                if isinstance(data, dict):
                    return data, None, None
                return None, f"[{model}] invalid response payload", "invalid_payload"
        except Exception as exc:
            return None, f"[{model}] {str(exc)[:220]}", "connectivity_error"

    def _normalize_model_candidates(self, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(text)
        return out

    def _candidate_model_order(self, preferred: str) -> list[str]:
        fallback_defaults = ["deepseek-chat", "deepseek-reasoner"]
        return self._normalize_model_candidates([preferred, *self.model_candidates, *fallback_defaults])

    def _resolve_request_base_url(self, *, model: str) -> str:
        model_text = str(model or "").strip().lower()
        configured = str(self.base_url or "").strip().rstrip("/")
        configured_nvidia = str(self.nvidia_base_url or "").strip().rstrip("/")

        # NVIDIA catalog models are usually provider-prefixed, e.g. deepseek-ai/deepseek-r1.
        if self._is_nvidia_model(model_text):
            return configured_nvidia or self.default_nvidia_base_url

        # DeepSeek models should always hit DeepSeek-compatible endpoint.
        if model_text.startswith("deepseek"):
            if configured and "nvidia.com" not in configured.lower():
                return configured
            return self.default_base_url

        # Keep user-configured endpoint for unknown model families.
        if configured:
            return configured
        return self.default_base_url

    def _resolve_request_api_key(self, *, model: str, base_url: str) -> str:
        model_text = str(model or "").strip().lower()
        current_base_url = str(base_url or "").strip().lower()
        if "nvidia.com" in current_base_url:
            return self.nvidia_api_key
        if model_text.startswith("deepseek") and self.api_key:
            return self.api_key
        if self.api_key:
            return self.api_key
        if self.nvidia_api_key:
            return self.nvidia_api_key
        return ""

    def _is_nvidia_model(self, model_text: str) -> bool:
        if not model_text:
            return False
        if "/" in model_text:
            return True
        return model_text.startswith(
            (
                "meta/",
                "nvidia/",
                "mistralai/",
                "qwen/",
                "microsoft/",
                "google/",
            )
        )

    def _unavailable_reason(self) -> Optional[str]:
        if not self.enabled:
            return "api_key_missing"
        if self.last_error_code:
            return self.last_error_code
        return None

    def _next_step_prompt(self, *, with_history: bool) -> str:
        base = NEXT_STEP_SYSTEM_PROMPT_WITH_HISTORY if with_history else NEXT_STEP_SYSTEM_PROMPT
        if not self.batch_execution_enabled:
            return base
        return (
            f"{base}"
            "优先使用批量命令计划。"
            "若任务涉及多步命令（尤其配置任务），请优先返回commands数组，一次给出完整命令组，而不是多轮单条命令。"
            "若会话模式是config且需要变更配置，请优先返回commands数组。"
            "若包含配置变更，请先用只读命令采集当前状态，再给出配置命令组。"
            "不要在同一个commands数组里混合“状态采集命令”和“配置变更命令”。"
            "若用户未明确对象标识（如接口名），先返回发现对象的只读命令，不要猜测对象名。"
            "同一轮变更应聚焦一个已证实目标；其他潜在问题写入follow_up_action，不要并行下发。"
        )

    def _extract_content(self, data: dict[str, Any]) -> str:
        try:
            return str(data["choices"][0]["message"]["content"])
        except Exception:
            return ""

    def _parse_json_object(self, text: str) -> Optional[dict[str, Any]]:
        text = text.strip()
        if not text:
            return None

        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None

        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except Exception:
            return None

        return None

    def _to_incident_summary(self, session_id: str, result: dict[str, Any]) -> Optional[IncidentSummary]:
        root_cause = str(result.get("root_cause", "")).strip()
        impact_scope = str(result.get("impact_scope", "")).strip()
        recommendation = str(result.get("recommendation", "")).strip()

        if not root_cause or not impact_scope or not recommendation:
            return None

        confidence_raw = result.get("confidence")
        confidence: Optional[float]
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else None
        except Exception:
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        evidence_refs_raw = result.get("evidence_refs", [])
        evidence_refs = evidence_refs_raw if isinstance(evidence_refs_raw, list) else []

        return IncidentSummary(
            session_id=session_id,
            root_cause=root_cause,
            impact_scope=impact_scope,
            recommendation=recommendation,
            confidence=confidence,
            evidence_refs=evidence_refs,
        )
