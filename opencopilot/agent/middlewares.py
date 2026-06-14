import json
import time
import asyncio
import inspect
import os
import traceback
from typing import Dict, Any, Optional, Callable, AsyncGenerator

from .pipeline import BaseMiddleware, PipelineContext
from .observability import PipelineObservability
from .types import AgentContextMeta, TaskComplexity, AgentParadigm, ExecutionPlan, PlanStep

# 延迟导入避免循环依赖
def _get_config_manager():
    from config_manager import ConfigManager
    return ConfigManager.get_instance()


# ============================================================
# 中间件定义
# ============================================================

class SessionSetupMiddleware(BaseMiddleware):
    _ppt_editor_direct_edit_prompt = (
        "You are handling a PPT co-creation edit request.\n"
        "The prompt already includes the current slide data and enough context to edit directly.\n"
        "Do not ask to read the slide again.\n"
        "Do not output placeholder tool calls such as read_slide.\n"
        "Return the final editable result directly, preferably as render_commands JSON.\n"
        "Unless the user explicitly asks to add or modify other pages, default to the current slide only."
    )


    def __init__(
        self,
        memory,
        window_manager,
        normalize_context_envelope: Callable,
        load_persona: Callable,
        build_context_prefix: Callable,
        sanitize_persona_for_context: Callable,
    ):
        self._memory = memory
        self._window_manager = window_manager
        self._normalize_context_envelope = normalize_context_envelope
        self._load_persona = load_persona
        self._build_context_prefix = build_context_prefix
        self._sanitize_persona_for_context = sanitize_persona_for_context
        # 延迟加载 SkillLoader，注入 SKILL.md 工具描述
        self._skill_loader = None
        self._tools_prompt_cache = None

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()
        try:
            req = ctx.request
            text = ctx.text
            session_id = ctx.session_id
            is_new_task = ctx.is_new_task
            runtime_flags = self._extract_runtime_flags(req)

            # ── 诊断埋点: 入口参数 ──
            PipelineObservability.get_instance().log(
                "SessionSetup", "ENTRY",
                session_id=session_id, level="DEBUG",
                event="SESSION_ENTRY",
                chunk_count=1 if is_new_task else 0,
                elapsed_ms=None,
                extra_data={"is_new_task": is_new_task, "action_type": ctx.action_type,
                            "text_len": len(text), "text_preview": text[:80]},
            )

            _t1 = time.time()
            context_source = req.get("context_source", "drag")
            context_meta = req.get("context_meta", {})
            envelope = self._normalize_context_envelope(req, text, context_source, context_meta)
            _t_norm = time.time() - _t1

            _t2 = time.time()
            if is_new_task:
                self._memory.clear(session_id)
                self._memory.set_persona(session_id, ctx.action_type)
                # ── 诊断埋点: 新建任务触发 clear ──
                PipelineObservability.get_instance().log(
                    "SessionSetup", "CLEAR triggered by is_new_task=True",
                    session_id=session_id, level="DEBUG",
                    event="SESSION_CLEAR",
                    extra_data={"trigger": "is_new_task", "new_persona": ctx.action_type},
                )

            # 当 action_type 非 default 时，更新 persona 以确保正确的角色切换
            chat_ctx = self._memory.get_context(session_id)
            if ctx.action_type != "default" and chat_ctx.get("persona") != ctx.action_type:
                old_persona = chat_ctx.get("persona", "")
                self._memory.set_persona(session_id, ctx.action_type)
                chat_ctx = self._memory.get_context(session_id)
                # ── 诊断埋点: persona 切换 ──
                PipelineObservability.get_instance().log(
                    "SessionSetup", f"Persona switch: {old_persona} → {ctx.action_type}",
                    session_id=session_id, level="DEBUG",
                    event="PERSONA_SWITCH",
                    extra_data={"old_persona": old_persona, "new_persona": ctx.action_type},
                )
            _t_mem = time.time() - _t2

            # ── 诊断埋点: 历史消息摘要 ──
            history_msgs = chat_ctx.get("messages", [])
            history_roles = [m.get("role", "?") for m in history_msgs] if history_msgs else []
            # 截取最后 2 条消息的内容预览（每条最多 60 字符）
            recent_previews = []
            for m in history_msgs[-2:]:
                content = m.get("content", "")
                if isinstance(content, str):
                    recent_previews.append(f"[{m.get('role','?')}]: {content[:60]}")
                else:
                    recent_previews.append(f"[{m.get('role','?')}]: <non-str content>")
            PipelineObservability.get_instance().log(
                "SessionSetup",
                f"History summary: count={len(history_msgs)} roles={'→'.join(history_roles) if history_roles else 'EMPTY'}",
                session_id=session_id, level="DEBUG",
                event="HISTORY_DUMP",
                extra_data={
                    "history_count": len(history_msgs),
                    "history_roles": history_roles,
                    "recent_previews": recent_previews,
                    "persona": chat_ctx.get("persona", ""),
                },
            )

            _t3 = time.time()
            ctx.persona = chat_ctx["persona"]
            persona_prompt = self._load_persona(ctx.persona)
            _t_persona = time.time() - _t3

            # 翻译模式：根据 context_meta 动态注入翻译方向
            if ctx.action_type == "translate":
                persona_prompt = self._inject_translation_direction(persona_prompt, context_meta)

            _t4 = time.time()
            source = envelope.get("source", "drag")
            context_prefix = ""
            if not runtime_flags.get("disable_context_prefix", False):
                context_prefix = self._build_context_prefix(source, envelope.get("meta", {}))
            persona_prompt = self._sanitize_persona_for_context(persona_prompt, source)
            if runtime_flags.get("disable_persona_prompt", False):
                persona_prompt = ""

            if context_prefix:
                ctx.enriched_system = f"{context_prefix}\n\n{persona_prompt}"
            else:
                ctx.enriched_system = persona_prompt

            if self._is_ppt_editor_request(ctx):
                ctx.enable_web_search = False
                ctx.metadata["answer_first"] = True
                ctx.enriched_system = f"{ctx.enriched_system}\n\n{self._ppt_editor_direct_edit_prompt}".strip()

            # 注入 SKILL.md 工具描述到 system prompt（延迟加载，缓存复用）
            tools_prompt = ""
            if not runtime_flags.get("disable_tools_prompt", False) and not self._is_ppt_editor_request(ctx):
                tools_prompt = self._get_tools_prompt()
            if tools_prompt:
                ctx.enriched_system = f"{ctx.enriched_system}\n\n{tools_prompt}"
                PipelineObservability.get_instance().log(
                    "SessionSetup", f"Injected tools prompt ({len(tools_prompt)} chars)",
                    session_id=session_id, level="DEBUG",
                    event="SKILL_TOOLS_INJECTED",
                    extra_data={"tools_prompt_len": len(tools_prompt)},
                )
            _t_prefix = time.time() - _t4

            _t5 = time.time()
            history_messages = chat_ctx["messages"]
            if runtime_flags.get("disable_history", False):
                history_messages = []
            ctx.messages = self._window_manager.build_messages(
                system_prompt=ctx.enriched_system,
                envelope=envelope,
                history_messages=history_messages,
            )
            _t_build = time.time() - _t5

            # ── 诊断埋点: 构建后的 messages 摘要 ──
            built_roles = [m.get("role", "?") for m in ctx.messages] if ctx.messages else []
            total_chars = sum(len(str(m.get("content", ""))) for m in ctx.messages)
            # system prompt 长度
            system_msg = next((m for m in ctx.messages if m.get("role") == "system"), None)
            sys_len = len(str(system_msg.get("content", ""))) if system_msg else 0
            PipelineObservability.get_instance().log(
                "SessionSetup",
                f"Built messages: count={len(ctx.messages)} total_chars={total_chars} sys_len={sys_len} roles={'→'.join(built_roles)}",
                session_id=session_id, level="DEBUG",
                event="MESSAGES_BUILT",
                extra_data={
                    "msg_count": len(ctx.messages),
                    "total_chars": total_chars,
                    "system_prompt_len": sys_len,
                    "roles": built_roles,
                },
            )

            image_base64 = req.get("image_base64")
            if image_base64:
                ctx.image_base64 = image_base64
                last_msg = ctx.messages[-1]
                last_msg["content"] = [
                    {"type": "text", "text": last_msg["content"]},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ]

            user_content = envelope.get("content", text)
            user_message_content = []
            if user_content:
                user_message_content.append({"type": "text", "text": user_content})
            if image_base64:
                user_message_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                })
            if not user_message_content:
                user_message_content.append({"type": "text", "text": "你好"})

            ctx.user_message_content = (
                json.dumps(user_message_content, ensure_ascii=False)
                if len(user_message_content) > 1
                else user_message_content[0]["text"]
            )

            if not runtime_flags.get("disable_session_memory", False):
                self._memory.add_message(session_id, "user", ctx.user_message_content)

            PipelineObservability.get_instance().timer(f"[Timer] SessionSetup: total={time.time()-_t0:.3f}s | norm={_t_norm:.3f} mem={_t_mem:.3f} persona={_t_persona:.3f} prefix={_t_prefix:.3f} build={_t_build:.3f}",
                                                        action_type=ctx.action_type)
        except Exception as e:
            print(f"[Pipeline] SessionSetup error: {e}", flush=True)
            traceback.print_exc()

        await next_fn()

    @staticmethod
    def _extract_runtime_flags(req: Dict[str, Any]) -> Dict[str, Any]:
        context_meta = req.get("context_meta", {})
        if not isinstance(context_meta, dict):
            return {}
        runtime_flags = context_meta.get("runtime_flags", {})
        return runtime_flags if isinstance(runtime_flags, dict) else {}

    @staticmethod
    def _inject_translation_direction(persona_prompt: str, context_meta: dict) -> str:
        """根据 context_meta 动态注入翻译方向到 system prompt

        将翻译类型 persona 的模糊指令（"翻译为指定目标语言"）替换为明确的方向，
        确保 LLM 不会在 system/user prompt 之间产生歧义。
        """
        source_lang = context_meta.get("source_lang", "zh")
        target_lang = context_meta.get("target_lang", "en")
        lang_map = {
            "zh": "中文", "en": "英文", "ja": "日文",
            "ko": "韩文", "fr": "法文", "de": "德文",
            "es": "西班牙文", "ru": "俄文",
        }
        source_name = lang_map.get(source_lang, source_lang)
        target_name = lang_map.get(target_lang, target_lang)

        direction_line = f"请将用户提供的文本从{source_name}翻译为{target_name}。"

        # 替换或插入翻译方向指令
        if "翻译为指定目标语言" in persona_prompt:
            persona_prompt = persona_prompt.replace(
                "翻译为指定目标语言", f"从{source_name}翻译为{target_name}"
            )
        else:
            # 在 persona 末尾追加方向指令（兜底）
            persona_prompt = f"{persona_prompt}\n\n{direction_line}"

        return persona_prompt

    def _get_tools_prompt(self) -> str:
        """延迟加载 SkillLoader 并缓存工具描述（注入 System Prompt）"""
        if self._tools_prompt_cache is not None:
            return self._tools_prompt_cache
        try:
            from .skill_loader import SkillLoader
            self._skill_loader = SkillLoader(skills_dir="skills/")
            eligible = self._skill_loader.load_eligible()
            if eligible:
                self._tools_prompt_cache = self._skill_loader.build_tools_prompt(eligible)
            else:
                self._tools_prompt_cache = ""
        except Exception as e:
            print(f"[Pipeline] SkillLoader failed: {e}", flush=True)
            self._tools_prompt_cache = ""
        return self._tools_prompt_cache

    @staticmethod
    def _is_ppt_editor_request(ctx: PipelineContext) -> bool:
        if os.getenv("OPEN_COPILOT_DISABLE_PPT_DIRECT_EDIT_GUARD", "0").strip().lower() in {"1", "true", "on"}:
            return False
        context_source = str(ctx.request.get("context_source", "")).lower()
        text = ctx.text or ""
        return (
            context_source in ("ppt_editor", "v5plus_stage3")
            or ("PPT 总共" in text and "当前幻灯片数据" in text)
            or ("修改当前 PPT 大纲" in text and "当前幻灯片数据" in text)
        )



class SecurityGuardMiddleware(BaseMiddleware):

    def __init__(self, security_module):
        self._security = security_module
        self._initialized_users = set()

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()
        try:
            # 先静默初始化用户，避免 security module 内部输出 "Permission denied" 噪声
            if ctx.session_id not in self._initialized_users:
                self._init_user(ctx.session_id)

            _t1 = time.time()
            allowed = await self._security.check_permission(
                user_id=ctx.session_id,
                resource="agent.chat",
                action="execute",
            )
            _t_perm = time.time() - _t1

            if not allowed:
                ctx.short_circuit("❌ 权限不足，请求被拦截。")
                PipelineObservability.get_instance().timer(f"[Timer] SecurityGuard: total={time.time()-_t0:.3f}s | perm={_t_perm:.3f} (BLOCKED)",
                                                            action_type=ctx.action_type)
                return

            _t3 = time.time()
            has_quota, reason = await self._security.check_rate_limit(
                user_id=ctx.session_id,
                resource="agent.chat",
                action="execute",
            )
            _t_quota = time.time() - _t3

            if not has_quota:
                ctx.short_circuit(f"⚠️ 请求过于频繁: {reason}")
                PipelineObservability.get_instance().timer(f"[Timer] SecurityGuard: total={time.time()-_t0:.3f}s | perm={_t_perm:.3f} quota={_t_quota:.3f} (RATE LIMITED)",
                                                            action_type=ctx.action_type)
                return

            PipelineObservability.get_instance().timer(f"[Timer] SecurityGuard: total={time.time()-_t0:.3f}s | perm={_t_perm:.3f} quota={_t_quota:.3f}",
                                                        action_type=ctx.action_type)
        except Exception as e:
            print(f"[Pipeline] Security check error, allowing pass: {e}", flush=True)
            PipelineObservability.get_instance().timer(f"[Timer] SecurityGuard: total={time.time()-_t0:.3f}s (ERROR, pass-through)",
                                                        action_type=ctx.action_type)

        await next_fn()

    def _init_user(self, session_id):
        """静默自动注册用户权限（容忍已存在）"""
        try:
            from opencopilot.safety.security.models import Permission
            try:
                self._security.permission_manager.add_permission(
                    Permission(
                        permission_id="agent.chat",
                        resource="agent.chat",
                        action="execute",
                        description="Agent chat permission (auto-created)",
                    )
                )
            except Exception:
                pass  # permission 已存在
            try:
                self._security.permission_manager.create_role("user", "Default User (auto-created)")
            except Exception:
                pass  # role 已存在
            try:
                self._security.permission_manager.assign_permission_to_role("agent.chat", "user")
            except Exception:
                pass  # 已分配
            try:
                self._security.permission_manager.assign_role_to_user(session_id, "user")
            except Exception:
                pass  # 已分配
            self._initialized_users.add(session_id)
        except Exception:
            pass  # 整体容错


class ImmuneSystemMiddleware(BaseMiddleware):

    def __init__(self, immune_system):
        self._immune = immune_system

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()
        try:
            from opencopilot.safety.immune import RuleContext

            # 内容生成任务完全跳过安全检查
            # PPT/翻译/聊天/润色等只是生成文本，不会执行代码，无需安全检查
            content_generation_actions = {"ppt", "translate", "chat", "polish", "explain", "evaluation"}
            if ctx.action_type in content_generation_actions:
                PipelineObservability.get_instance().log(
                    "ImmuneSystem", f"SKIPPED for content generation: {ctx.action_type}",
                    session_id=ctx.session_id, level="DEBUG",
                    event="IMMUNE_SKIPPED",
                    extra_data={"action_type": ctx.action_type, "text_len": len(ctx.text)},
                )
                await next_fn()
                return

            # 只有代码生成/执行类任务才进行安全检查
            rule_ctx = RuleContext(session_id=ctx.session_id)
            
            result = await self._immune.check_content(rule_ctx, ctx.text)
            _t_check = time.time() - _t0
            if result and not result.allowed:
                ctx.short_circuit(f"⚠️ 规则检查发现违规: {result.message}\n\n请调整您的请求。")
                PipelineObservability.get_instance().timer(f"[Timer] ImmuneSystem: total={_t_check:.3f}s (BLOCKED)",
                                                            action_type=ctx.action_type)
                PipelineObservability.get_instance().log(
                    "ImmuneSystem", f"BLOCKED: {result.message}",
                    session_id=ctx.session_id, level="WARNING",
                    event="IMMUNE_BLOCKED",
                    extra_data={
                        "action_type": ctx.action_type,
                        "text_len": len(ctx.text),
                        "rule_action": rule_ctx.current_action or "default",
                        "violations": [
                            {"rule": v.rule_name, "severity": v.severity}
                            for v in (result.violations or [])
                        ],
                    },
                )
                return
            
            # 通过时的诊断日志
            if result and result.violations:
                # 有 WARNING 级违规但未 BLOCK（如 no_print_statements）
                warnings = [v.rule_name for v in result.violations]
                PipelineObservability.get_instance().log(
                    "ImmuneSystem", f"PASS with warnings: {warnings}",
                    session_id=ctx.session_id, level="DEBUG",
                    event="IMMUNE_PASS_WARNINGS",
                    extra_data={
                        "action_type": ctx.action_type,
                        "warnings": warnings,
                    },
                )
            PipelineObservability.get_instance().timer(f"[Timer] ImmuneSystem: total={_t_check:.3f}s",
                                                        action_type=ctx.action_type)
        except Exception as e:
            print(f"[Pipeline] Immune check error, allowing pass: {e}", flush=True)
            PipelineObservability.get_instance().timer(f"[Timer] ImmuneSystem: total={time.time()-_t0:.3f}s (ERROR, pass-through)",
                                                        action_type=ctx.action_type)

        await next_fn()


class PlannerMiddleware(BaseMiddleware):

    def __init__(self, planner):
        self._planner = planner

    # 这些 action_type 明确不需要 Planner（避免 PPT prompt 中"步骤""设计"等词触发误判）
    _skip_planner_types = {"ppt", "translate", "polish", "fix", "evaluation", "revision", "custom"}
    _answer_first_types = {"chat", "code_review", "explain", "planning"}

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()
        runtime_flags = self._extract_runtime_flags(ctx)

        # 按 action_type 快速跳过不需要 Planner 的请求
        if ctx.action_type in self._skip_planner_types:
            PipelineObservability.get_instance().timer(
                f"[Timer] Planner: total={time.time()-_t0:.3f}s (skipped, action_type={ctx.action_type})",
                action_type=ctx.action_type,
            )
            await next_fn()
            return

        if runtime_flags.get("disable_planner", False):
            PipelineObservability.get_instance().timer(
                f"[Timer] Planner: total={time.time()-_t0:.3f}s (skipped, runtime_flag=disable_planner)",
                action_type=ctx.action_type,
            )
            await next_fn()
            return

        try:
            if self._is_complex_task(ctx.text) or SessionSetupMiddleware._is_ppt_editor_request(ctx):
                answer_first = self._should_answer_first(ctx)
                _t1 = time.time()
                plan = await self._planner.create_plan(
                    task=ctx.text,
                    context={"session_id": ctx.session_id},
                )
                _t_plan = time.time() - _t1
                if plan and plan.steps:
                    ctx.metadata["plan"] = {
                        "plan_id": plan.plan_id,
                        "task": plan.task,
                        "steps": [
                            {"description": s.description, "type": s.step_type.value}
                            for s in plan.steps
                        ],
                    }
                    if answer_first:
                        ctx.metadata["answer_first"] = True
                        ctx.enable_web_search = False
                        plan_text = self._format_answer_first_plan(ctx.metadata["plan"])
                        self._inject_system_message(ctx, plan_text)
                    else:
                        plan_text = self._format_plan_for_system(ctx.metadata["plan"])
                        ctx.enriched_system = ctx.enriched_system + plan_text
                PipelineObservability.get_instance().timer(f"[Timer] Planner: total={time.time()-_t0:.3f}s | create_plan={_t_plan:.3f}s (complex)",
                                                            action_type=ctx.action_type)
            else:
                PipelineObservability.get_instance().timer(f"[Timer] Planner: total={time.time()-_t0:.3f}s (skipped, not complex)",
                                                            action_type=ctx.action_type)
        except Exception as e:
            print(f"[Pipeline] Planner error, skipping: {e}", flush=True)
            PipelineObservability.get_instance().timer(f"[Timer] Planner: total={time.time()-_t0:.3f}s (ERROR)",
                                                        action_type=ctx.action_type)

        await next_fn()

    @staticmethod
    def _extract_runtime_flags(ctx: PipelineContext) -> Dict[str, Any]:
        context_meta = ctx.request.get("context_meta", {})
        if not isinstance(context_meta, dict):
            return {}
        runtime_flags = context_meta.get("runtime_flags", {})
        return runtime_flags if isinstance(runtime_flags, dict) else {}

    def _is_complex_task(self, text: str) -> bool:
        complexity_indicators = [
            "步骤", "流程", "方案", "规划",
            "设计", "架构", "分析并", "多个",
            "首先", "然后", "接着", "最后",
            "实现一个", "开发一个", "帮我做",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in complexity_indicators if kw in text_lower)
        return score >= 2

    def _should_answer_first(self, ctx: PipelineContext) -> bool:
        if SessionSetupMiddleware._is_ppt_editor_request(ctx):
            return True

        if ctx.action_type not in self._answer_first_types:
            return False

        text_lower = ctx.text.lower()
        search_intents = [
            "搜索", "检索", "联网", "调研", "research", "最新", "查一下", "查找", "搜集资料",
            "web search", "google", "搜索相关", "先搜索",
        ]
        if any(keyword in text_lower for keyword in search_intents):
            return False

        deliverable_intents = [
            "给出", "输出", "方案", "执行摘要", "行动清单", "风险清单", "待确认",
            "审查", "改进建议", "示例代码", "重构", "总结", "分析",
        ]
        return any(keyword in text_lower for keyword in deliverable_intents)

    def _format_plan_for_system(self, plan: dict) -> str:
        lines = ["\n\n[Task Plan - Auto-generated]"]
        lines.append(f"Task: {plan['task']}")
        lines.append("Steps:")
        for i, s in enumerate(plan["steps"], 1):
            lines.append(f"  {i}. [{s['type']}] {s['description']}")
        return "\n".join(lines)

    def _format_answer_first_plan(self, plan: dict) -> str:
        lines = [
            "[Internal Response Strategy]",
            "You must provide the final answer directly in this turn.",
            "Do not expose tool calls, search plans, or intermediate reasoning.",
            "Do not output JSON tool requests unless the user explicitly asks for them.",
            "Assume the prompt and provided context already contain the required information.",
            "Do not claim that you need to inspect files, query knowledge_search, or browse the web unless the user explicitly asks for external lookup.",
            "Use the internal plan only to organize the final answer structure.",
            f"Task: {plan['task']}",
            "Suggested answer structure:",
        ]
        for idx, step in enumerate(plan["steps"], 1):
            desc = self._sanitize_answer_first_step(step["description"])
            lines.append(f"{idx}. {desc}")
        return "\n".join(lines)

    @staticmethod
    def _sanitize_answer_first_step(description: str) -> str:
        replacements = {
            "搜索相关知识": "梳理与任务相关的关键信息",
            "收集完成任务所需的信息": "提炼完成任务所需的必要信息",
            "收集信息": "提炼必要信息",
            "调用工具": "组织必要素材",
            "检索": "梳理",
            "搜索": "梳理",
            "工具": "信息",
        }
        sanitized = description
        for source, target in replacements.items():
            sanitized = sanitized.replace(source, target)
        return sanitized

    @staticmethod
    def _inject_system_message(ctx: PipelineContext, content: str) -> None:
        system_message = {"role": "system", "content": content}
        if not ctx.messages:
            ctx.messages.append(system_message)
            return
        if ctx.messages[-1].get("role") == "user":
            ctx.messages.insert(len(ctx.messages) - 1, system_message)
        else:
            ctx.messages.append(system_message)


class StateTrackingMiddleware(BaseMiddleware):

    def __init__(self, state_manager):
        self._state = state_manager

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()
        try:
            # 用户消息已在 SessionSetupMiddleware 中添加，此处不再重复添加
            if ctx.metadata.get("plan"):
                task_result = self._state.create_task(
                    session_id=ctx.session_id,
                    task_id=ctx.metadata["plan"]["plan_id"],
                    task_type="agent_request",
                    description=ctx.metadata["plan"]["task"],
                    metadata={"plan": ctx.metadata["plan"]},
                )
                if inspect.isawaitable(task_result):
                    await task_result
        except Exception as e:
            print(f"[Pipeline] State tracking error: {e}", flush=True)

        PipelineObservability.get_instance().timer(f"[Timer] StateTracking: total={time.time()-_t0:.3f}s",
                                                    action_type=ctx.action_type)
        await next_fn()


class CapabilityRouterMiddleware(BaseMiddleware):

    def __init__(
        self,
        code_executor,
        knowledge_retrieval,
        search_capability,
        skill_executor,
        skill_registry,
        skill_router,
        detect_request_type: Callable,
    ):
        self._code_executor = code_executor
        self._knowledge_retrieval = knowledge_retrieval
        self._search_capability = search_capability
        self._skill_executor = skill_executor
        self._skill_registry = skill_registry
        self._skill_router = skill_router
        self._detect_request_type = detect_request_type

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()

        # 优先使用 action_type（API 层已明确路由），避免文本检测误判
        # 例：coding 端点的 prompt 中含代码，detect_request_type 会误判为 code_execution
        llm_types = {"chat", "ppt", "coding", "code_review", "evaluation", "planning", "skill", "translate", "explain", "fix", "polish", "revision", "custom", "review"}
        if ctx.action_type in llm_types:
            _t_detect = time.time() - _t0
            PipelineObservability.get_instance().timer(f"[Timer] CapabilityRouter: total={time.time()-_t0:.3f}s | detect={_t_detect:.3f}s type={ctx.action_type} → LLM (action_type)",
                                                        action_type=ctx.action_type)
            await next_fn()
            return

        request_type = self._detect_request_type(ctx.text)
        _t_detect = time.time() - _t0

        if request_type in llm_types:
            PipelineObservability.get_instance().timer(f"[Timer] CapabilityRouter: total={time.time()-_t0:.3f}s | detect={_t_detect:.3f}s type={request_type} → LLM",
                                                        action_type=ctx.action_type)
            await next_fn()
            return

        result = None
        try:
            _t1 = time.time()
            if request_type == "code_execution":
                result = await self._handle_code_execution(ctx)
            elif request_type == "knowledge_query":
                result = self._handle_knowledge_query(ctx)
            elif request_type == "search":
                result = self._handle_search(ctx)
            elif request_type == "security":
                result = self._handle_security_status(ctx)
            else:
                PipelineObservability.get_instance().timer(f"[Timer] CapabilityRouter: total={time.time()-_t0:.3f}s | detect={_t_detect:.3f}s type={request_type} → passthrough",
                                                            action_type=ctx.action_type)
                await next_fn()
                return
            _t_handle = time.time() - _t1
            PipelineObservability.get_instance().timer(f"[Timer] CapabilityRouter: total={time.time()-_t0:.3f}s | detect={_t_detect:.3f}s handle={_t_handle:.3f}s type={request_type}",
                                                        action_type=ctx.action_type)
        except Exception as e:
            print(f"[Pipeline] Capability error: {e}", flush=True)
            traceback.print_exc()
            PipelineObservability.get_instance().timer(f"[Timer] CapabilityRouter: total={time.time()-_t0:.3f}s (ERROR)",
                                                        action_type=ctx.action_type)
            await next_fn()
            return

        if result:
            ctx.short_circuit(result)
        else:
            await next_fn()

    async def _handle_code_execution(self, ctx: PipelineContext) -> Optional[str]:
        code = self._generate_code(ctx.text)
        result = await self._code_executor.execute_code(
            code=code,
            language="python",
            timeout=30,
            working_directory=".",
            use_sandbox=True,
        )
        if result.success:
            return f"✅ 代码执行成功:\n```\n{result.output}\n```"
        else:
            return f"❌ 代码执行失败:\n```\n{result.error}\n```"

    def _generate_code(self, task: str) -> str:
        import os
        try:
            lines = []
            if "tushare" in task.lower():
                lines.append("import tushare as ts")
                token = os.getenv("TUSHARE_TOKEN", "")
                if token:
                    lines.append(f'ts.set_token("{token}")')
                lines.append("pro = ts.pro_api()")
            lines.append("# Generated code for: " + task)
            lines.append(task)
            return "\n".join(lines)
        except Exception:
            return "# Failed to generate code\n" + task

    def _handle_knowledge_query(self, ctx: PipelineContext) -> str:
        if not self._knowledge_retrieval._initialized:
            self._knowledge_retrieval.initialize()
        result = self._knowledge_retrieval.query(ctx.text)
        if result:
            return f"🔍 知识检索结果:\n\n{json.dumps(result, ensure_ascii=False, indent=2)}"
        return "❌ 未找到相关知识"

    def _handle_search(self, ctx: PipelineContext) -> str:
        from opencopilot.capabilities.search import SearchType
        results = self._search_capability.search(ctx.text, search_type=SearchType.ALL, count=5)
        if results:
            lines = ["🔍 搜索结果:\n"]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.title}")
                lines.append(f"   {r.content[:200]}...\n")
            return "\n".join(lines)
        return "❌ 未找到相关搜索结果"

    def _handle_security_status(self, ctx: PipelineContext) -> str:
        try:
            from opencopilot.safety.security import SecurityModule
            return "🔒 安全模块已启用"
        except Exception:
            return "🔒 安全模块状态: 正常"


class ReviewMiddleware(BaseMiddleware):
    """审查中间件 — 当 action_type=review 时注入审查 Prompt

    在 CapabilityRouter 之后、LLMProvider 之前执行。
    从 context_meta 提取审查参数，构建专门的审查 Prompt 注入到 system message。
    """

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()

        if ctx.action_type != "review":
            await next_fn()
            return

        try:
            from opencopilot.review import ReviewEngine

            engine = ReviewEngine()
            meta = ctx.request.get("context_meta", {})
            review_type = meta.get("review_type", "hallucination")
            reference_path = meta.get("reference_path", "")
            reference_description = meta.get("reference_description", "")
            style_target = meta.get("style_target", "")
            context_text = meta.get("context_text", "")

            # 如果用户提供了参考文档描述但没给具体路径，尝试搜索
            if review_type == "data_check" and not reference_path and reference_description:
                from opencopilot.review.reference_loader import search_local_files
                candidates = search_local_files(reference_description)
                if candidates:
                    reference_path = candidates[0]
                    print(f"[ReviewMiddleware] Auto-found reference: {reference_path}", flush=True)

            # 构建审查 Prompt
            system_prompt, user_prompt = engine.build_llm_prompt(
                text=ctx.text,
                review_type=review_type,
                context=context_text,
                reference_path=reference_path,
                style_target=style_target,
            )

            # 注入到 system message
            if system_prompt:
                ctx.enriched_system = f"{ctx.enriched_system}\n\n{system_prompt}".strip()

            # 替换 user message 为审查专用的 user prompt
            if user_prompt and ctx.messages:
                # 找到最后一个 user message 并替换
                for i in range(len(ctx.messages) - 1, -1, -1):
                    if ctx.messages[i].get("role") == "user":
                        ctx.messages[i]["content"] = user_prompt
                        break

            # 将解析器信息存入 metadata，供 LLM 返回后解析
            ctx.metadata["review"] = {
                "review_type": review_type,
                "source_text": ctx.text,
            }

            PipelineObservability.get_instance().timer(
                f"[Timer] Review: total={time.time()-_t0:.3f}s | type={review_type} "
                f"ref={reference_path[:30] if reference_path else 'none'}",
                action_type=ctx.action_type,
            )
            print(f"[ReviewMiddleware] review_type={review_type}, text_len={len(ctx.text)}", flush=True)

        except Exception as e:
            print(f"[Pipeline] ReviewMiddleware error: {e}", flush=True)
            traceback.print_exc()
            PipelineObservability.get_instance().timer(
                f"[Timer] Review: total={time.time()-_t0:.3f}s (ERROR)",
                action_type=ctx.action_type,
            )

        await next_fn()


class LLMProviderMiddleware(BaseMiddleware):
    """LLM 直接调用中间件（原生异步）

    使用 LLM Provider 的原生 async 方法，全链路运行在事件循环中。
    """


    def __init__(self, memory, get_base_llm: Callable):
        self._memory = memory
        self._get_base_llm = get_base_llm

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()
        try:
            _t1 = time.time()
            llm = self._get_base_llm()
            _t_init = time.time() - _t1

            # 构建 web search 参数
            ws_kwargs = {}
            if ctx.enable_web_search:
                ws_kwargs = {
                    "enable_web_search": True,
                    "force_search": ctx.web_search_force,
                    "max_keyword": ctx.web_search_max_keyword,
                    "limit": ctx.web_search_limit,
                    "user_location": ctx.web_search_user_location,
                }
                print(f"[Pipeline] Web search enabled (force={ctx.web_search_force})", flush=True)

            _t_first_chunk = None
            _chunk_count = 0
            full_reply = ""

            # 原生异步流式消费
            async for chunk in llm.async_stream_chat_with_history(ctx.messages, **ws_kwargs):
                if _t_first_chunk is None:
                    _t_first_chunk = time.time()

                # 处理 annotations（搜索来源引用，增量到达）
                if isinstance(chunk, tuple) and chunk[0] == "__annotations__":
                    annotations = chunk[1]
                    ctx.web_search_annotations.extend(annotations)
                    await ctx.awrite_sse_annotations(annotations)
                    print(f"[Pipeline] Web search annotations: +{len(annotations)} sources (total: {len(ctx.web_search_annotations)})", flush=True)
                    continue

                full_reply += chunk
                _chunk_count += 1
                await ctx.awrite_sse(chunk)

            _t_llm = time.time() - _t1
            _t_ttfb = (_t_first_chunk - _t1) if _t_first_chunk else _t_llm

            self._memory.add_message(ctx.session_id, "assistant", full_reply)
            await ctx.awrite_sse_done()

            _elapsed = (time.time() - _t0) * 1000
            PipelineObservability.get_instance().ai_response(
                ctx.session_id, full_reply, paradigm="llm",
                chunk_count=_chunk_count, elapsed_ms=_elapsed,
                action_type=ctx.action_type,
            )
            PipelineObservability.get_instance().timer(f"[Timer] LLMProvider: total={time.time()-_t0:.3f}s | init={_t_init:.3f} ttfb={_t_ttfb:.3f} stream={_t_llm-_t_ttfb:.3f} chunks={_chunk_count} chars={len(full_reply)}",
                                                        action_type=ctx.action_type)
        except asyncio.CancelledError:
            # Pipeline 被取消（通常是用户发新消息中断旧请求），加速清理
            # 不再 raise——由 caller.py 的 cancel_event 机制负责通知消费者
            print(f"[Pipeline] LLMProvider cancelled after {time.time()-_t0:.1f}s", flush=True)
            PipelineObservability.get_instance().timer(f"[Timer] LLMProvider: total={time.time()-_t0:.3f}s (CANCELLED)",
                                                        action_type=ctx.action_type)
            try:
                await asyncio.wait_for(ctx.awrite_sse_done(), timeout=0.5)
            except Exception:
                pass
        except Exception as e:
            print(f"[Pipeline] LLM error: {e}", flush=True)
            traceback.print_exc()
            PipelineObservability.get_instance().timer(f"[Timer] LLMProvider: total={time.time()-_t0:.3f}s (ERROR: {e})",
                                                        action_type=ctx.action_type)
            await ctx.awrite_sse(f"\n[Agent Error]: {str(e)}")
            await ctx.awrite_sse_done()


# ============================================================
# Phase 2: LLMAgentMiddleware — Agent Loop 引擎
# ============================================================

class LLMAgentMiddleware(BaseMiddleware):
    """LLM 驱动的 Agent 循环中间件

    根据任务复杂度动态选择推理范式：
    - SIMPLE → One-Shot：直接 LLM 回答
    - MEDIUM → Plan-and-Solve：生成计划 → 逐步执行 → 汇总
    - COMPLEX → Plan+ReAct：计划执行偏差时启用 ReAct 回退

    用法：替代 LLMProviderMiddleware，在管线末端使用。
    """

    # 类级默认值（实例化时会被 ConfigManager 覆盖）
    DEFAULT_MAX_TURNS = 10
    DEFAULT_MAX_PLAN_STEPS = 5
    DEFAULT_COMPLEXITY_TEXT_THRESHOLD = 200
    DEFAULT_REACT_RETRY_COUNT = 1

    def __init__(self, memory, get_base_llm: Callable):
        self._memory = memory
        self._get_base_llm = get_base_llm

        # 从 ConfigManager 读取 Agent 可配置参数（P0+P1）
        agent_cfg = _get_config_manager().get_agent()
        self.max_turns = agent_cfg.get("max_turns", self.DEFAULT_MAX_TURNS)
        self.max_plan_steps = agent_cfg.get("max_plan_steps", self.DEFAULT_MAX_PLAN_STEPS)
        self.complexity_text_threshold = agent_cfg.get("complexity_text_threshold", self.DEFAULT_COMPLEXITY_TEXT_THRESHOLD)
        self.react_retry_count = agent_cfg.get("react_retry_count", self.DEFAULT_REACT_RETRY_COUNT)

        print(f"[LLMAgentMiddleware] Config loaded: max_turns={self.max_turns}, "
              f"max_plan_steps={self.max_plan_steps}, "
              f"complexity_threshold={self.complexity_text_threshold}, "
              f"react_retry_count={self.react_retry_count}", flush=True)

    # ---- 复杂度判断 ----

    def _is_complex(self, ctx: PipelineContext) -> TaskComplexity:
        """快速复杂度判断（规则，不调 LLM）

        判断依据：
        1. action_type 为 coding/ppt 且文本较长 → MEDIUM
        2. 含多步骤关键词 → MEDIUM/COMPLEX
        3. 含探索性关键词 → COMPLEX
        4. 默认 → SIMPLE
        """
        text = ctx.text
        action_type = ctx.action_type

        # 规则 1: 编码/PPT 任务 + 较长内容 → MEDIUM
        if action_type in {"coding", "ppt"}:
            return TaskComplexity.MEDIUM if len(text) > self.complexity_text_threshold else TaskComplexity.SIMPLE

        # 规则 2: 多步骤关键词
        multi_step = [
            "先", "然后", "接着", "最后", "再", "并且", "同时",
            "第一步", "第二步", "第三步", "首先", "其次", "之后",
        ]
        if any(kw in text for kw in multi_step):
            return TaskComplexity.MEDIUM

        # 规则 3: 探索性关键词 → COMPLEX
        exploration = [
            "研究", "探索", "分析并优化", "帮我优化", "帮我改进",
            "不确定", "尝试", "调查",
        ]
        if any(kw in text for kw in exploration):
            return TaskComplexity.COMPLEX

        return TaskComplexity.SIMPLE

    # ---- 主入口 ----

    async def process(self, ctx: PipelineContext, next_fn: Callable[[], object]) -> None:
        _t0 = time.time()
        complexity = self._is_complex(ctx)

        # 初始化 Agent 上下文元数据
        ctx.metadata["agent"] = AgentContextMeta(
            complexity=complexity,
            max_turns=self.max_turns,
        )

        try:
            if complexity == TaskComplexity.SIMPLE:
                await self._run_one_shot(ctx, _t0)
            elif complexity == TaskComplexity.MEDIUM:
                await self._run_plan_solve(ctx, _t0)
            else:  # COMPLEX
                await self._run_plan_react(ctx, _t0)
        except Exception as e:
            print(f"[Pipeline] LLMAgent error: {e}", flush=True)
            traceback.print_exc()
            PipelineObservability.get_instance().timer(
                f"[Timer] LLMAgent: total={time.time()-_t0:.3f}s (ERROR: {e})",
                action_type=ctx.action_type,
            )
            await ctx.awrite_sse(f"\n[Agent Error]: {str(e)}")
            await ctx.awrite_sse_done()

    # ---- One-Shot 模式 ----

    async def _run_one_shot(self, ctx: PipelineContext, _t0: float):
        """简单任务：直接 LLM 流式回答"""
        ctx.metadata["agent"].paradigm = AgentParadigm.ONE_SHOT
        llm = self._get_base_llm()

        ws_kwargs = self._build_ws_kwargs(ctx)
        full_reply = ""
        _chunk_count = 0
        _t_first_chunk = None

        _t1 = time.time()
        async for chunk in llm.async_stream_chat_with_history(ctx.messages, **ws_kwargs):
            if _t_first_chunk is None:
                _t_first_chunk = time.time()
            if isinstance(chunk, tuple) and chunk[0] == "__annotations__":
                await ctx.awrite_sse_annotations(chunk[1])
                continue
            full_reply += chunk
            _chunk_count += 1
            await ctx.awrite_sse(chunk)

        self._memory.add_message(ctx.session_id, "assistant", full_reply)
        await ctx.awrite_sse_done()

        _t_total = time.time() - _t0
        _t_ttfb = (_t_first_chunk - _t1) if _t_first_chunk else 0
        obs = PipelineObservability.get_instance()
        obs.ai_response(ctx.session_id, full_reply, paradigm="one_shot",
                        chunk_count=_chunk_count, elapsed_ms=_t_total * 1000,
                        action_type=ctx.action_type)
        obs.timer(f"[Timer] LLMAgent(One-Shot): total={_t_total:.3f}s | ttfb={_t_ttfb:.3f}s chunks={_chunk_count} chars={len(full_reply)}",
                  action_type=ctx.action_type)
        obs.agent_turn(ctx, paradigm="one_shot", turns=1, tool_calls=0)

    # ---- Plan-and-Solve 模式 ----

    async def _run_plan_solve(self, ctx: PipelineContext, _t0: float):
        """中等复杂任务：Plan-and-Solve"""
        ctx.metadata["agent"].paradigm = AgentParadigm.PLAN_SOLVE
        llm = self._get_base_llm()

        # Step 1: 生成计划
        await ctx.awrite_sse("\n📋 **分析任务，生成执行计划...**\n\n")
        plan = await self._generate_plan(ctx, llm)
        ctx.metadata["agent"].plan = plan

        if not plan.steps:
            # 无法生成计划，回退到 One-Shot
            await ctx.awrite_sse("（未能生成明确计划，直接回答）\n\n")
            await self._run_one_shot(ctx, _t0)
            return

        # Step 2: 逐步执行
        for step in plan.steps:
            plan.total_turns += 1
            if plan.total_turns > self.max_turns:
                await ctx.awrite_sse(f"\n⚠️ 已达最大轮次上限({self.max_turns})，进行汇总。\n")
                break

            step.status = "running"
            await ctx.awrite_sse(f"\n🔹 **步骤 {step.step_id}/{len(plan.steps)}**: {step.description}\n")

            try:
                result = await self._execute_step(step, ctx, llm)
                step.status = "done"
                step.result = result
                await ctx.awrite_sse(f"\n{result}\n")
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                await ctx.awrite_sse(f"\n❌ 步骤失败: {e}\n")
                # Plan-and-Solve 模式下不重试，继续下一步

        # Step 3: 最终汇总
        await ctx.awrite_sse("\n---\n📝 **汇总结果...**\n\n")
        final = await self._generate_final_answer(ctx, llm, plan)
        self._memory.add_message(ctx.session_id, "assistant", final)
        await ctx.awrite_sse_done()

        _t_total = time.time() - _t0
        obs = PipelineObservability.get_instance()
        obs.ai_response(ctx.session_id, final, paradigm="plan_solve",
                        chunk_count=0, elapsed_ms=_t_total * 1000,
                        action_type=ctx.action_type)
        obs.timer(
            f"[Timer] LLMAgent(Plan-Solve): total={_t_total:.3f}s turns={plan.total_turns} steps={len(plan.steps)}",
            action_type=ctx.action_type,
        )

    # ---- Plan + ReAct 模式 ----

    async def _run_plan_react(self, ctx: PipelineContext, _t0: float):
        """复杂任务：Plan-and-Solve + ReAct 偏差回退"""
        ctx.metadata["agent"].paradigm = AgentParadigm.REACT
        llm = self._get_base_llm()

        # Step 1: 生成计划
        await ctx.awrite_sse("\n🧠 **分析复杂任务，生成执行计划（支持动态调整）...**\n\n")
        plan = await self._generate_plan(ctx, llm)
        ctx.metadata["agent"].plan = plan

        if not plan.steps:
            await ctx.awrite_sse("（未能生成明确计划，直接回答）\n\n")
            await self._run_one_shot(ctx, _t0)
            return

        # Step 2: 逐步执行 + ReAct 回退
        for step in plan.steps:
            plan.total_turns += 1
            if plan.total_turns > self.max_turns:
                await ctx.awrite_sse(f"\n⚠️ 已达最大轮次上限({self.max_turns})，进行汇总。\n")
                break

            step.status = "running"
            await ctx.awrite_sse(f"\n🔹 **步骤 {step.step_id}/{len(plan.steps)}**: {step.description}\n")

            try:
                result = await self._execute_step(step, ctx, llm)
                step.status = "done"
                step.result = result
                await ctx.awrite_sse(f"\n{result}\n")
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                await ctx.awrite_sse(f"\n⚠️ 步骤遇到问题: {e}\n")

                # ReAct 回退：分析错误，尝试纠正
                await ctx.awrite_sse("\n🔄 **启动 ReAct 纠错...**\n")
                corrected = await self._react_correct(step, str(e), ctx, llm)
                if corrected:
                    step.status = "done"
                    step.result = corrected
                    await ctx.awrite_sse(f"\n✅ 纠错结果:\n{corrected}\n")
                    ctx.metadata["agent"].tool_calls_failed = \
                        ctx.metadata["agent"].tool_calls_failed + 1

        # Step 3: 最终汇总
        await ctx.awrite_sse("\n---\n📝 **综合汇总结果...**\n\n")
        final = await self._generate_final_answer(ctx, llm, plan)
        self._memory.add_message(ctx.session_id, "assistant", final)
        await ctx.awrite_sse_done()

        _t_total = time.time() - _t0
        obs = PipelineObservability.get_instance()
        obs.ai_response(ctx.session_id, final, paradigm="plan_react",
                        chunk_count=0, elapsed_ms=_t_total * 1000,
                        action_type=ctx.action_type)
        obs.timer(
            f"[Timer] LLMAgent(Plan+ReAct): total={_t_total:.3f}s turns={plan.total_turns} steps={len(plan.steps)}",
            action_type=ctx.action_type,
        )

    # ---- 内部方法 ----

    def _build_ws_kwargs(self, ctx: PipelineContext) -> dict:
        """构建 web search 关键词参数"""
        if not ctx.enable_web_search:
            return {}
        return {
            "enable_web_search": True,
            "force_search": ctx.web_search_force,
            "max_keyword": ctx.web_search_max_keyword,
            "limit": ctx.web_search_limit,
            "user_location": ctx.web_search_user_location,
        }

    async def _llm_non_stream(self, llm, messages: list, system_prompt: str = "") -> str:
        """非流式 LLM 调用（用于计划和中间步骤）"""
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)

        full = ""
        async for chunk in llm.async_stream_chat_with_history(msgs):
            if isinstance(chunk, tuple):
                continue
            full += chunk
        return full.strip()

    async def _generate_plan(self, ctx: PipelineContext, llm) -> ExecutionPlan:
        """使用 LLM 生成执行计划"""
        planning_messages = [
            {"role": "user", "content": f"""你需要为以下任务制定分步执行计划。

任务内容：{ctx.text}

请按以下格式输出计划（每行一个步骤，最多{self.max_plan_steps}步）：
步骤1：<描述>
步骤2：<描述>
...

注意：
- 每个步骤要具体、可执行
- 如果任务是简单的问答，输出 "无需计划"
- 不要输出其他内容"""},
        ]

        plan_text = await self._llm_non_stream(
            llm, planning_messages,
            system_prompt="你是一个任务规划助手。分析任务并生成清晰的分步执行计划。"
        )

        # 解析计划
        import re
        steps = []
        for line in plan_text.split("\n"):
            line = line.strip()
            if "无需计划" in line:
                break
            match = re.match(r"步骤\s*(\d+)[：:]\s*(.+)", line)
            if match:
                step_num = int(match.group(1))
                desc = match.group(2).strip()
                if desc:
                    steps.append(PlanStep(step_id=step_num, description=desc))

        return ExecutionPlan(
            paradigm=AgentParadigm.PLAN_SOLVE,
            steps=steps,
            reasoning=plan_text,
        )

    async def _execute_step(self, step: PlanStep, ctx: PipelineContext, llm) -> str:
        """执行单个计划步骤（通过 LLM 执行）"""
        step_messages = [
            {"role": "user", "content": f"""原始任务：{ctx.text}

当前需要执行的步骤：{step.description}

请完成这个步骤并给出结果。只输出步骤结果，不要多余内容。
如果你需要执行代码、搜索信息或查询知识，请在响应中用以下标记标明：
- 需要执行代码时输出：`[TOOL:run_code] 代码内容 [/TOOL]`
- 需要搜索时输出：`[TOOL:search] 搜索关键词 [/TOOL]`
- 如果可以直接完成，直接输出结果。"""},
        ]

        result = await self._llm_non_stream(llm, step_messages)

        # 检查是否有工具调用需求
        if "[TOOL:run_code]" in result:
            # 提取代码并真实执行（不再用 LLM 模拟）
            import re
            code_match = re.search(r'\[TOOL:run_code\]\s*(.*?)\s*\[/TOOL\]', result, re.DOTALL)
            if code_match:
                code = code_match.group(1).strip()
                try:
                    from opencopilot.capabilities.coding import CodeExecutor, ExecutorConfig
                    executor = CodeExecutor(ExecutorConfig(default_timeout=10))
                    exec_result = await executor.execute_code(code, "python")
                    if exec_result.exit_code == 0 and exec_result.stdout:
                        replacement = f"代码执行结果：\n{exec_result.stdout.strip()}"
                    elif exec_result.error:
                        replacement = f"代码执行错误：{exec_result.error}"
                    else:
                        replacement = f"代码执行完成（退出码: {exec_result.exit_code}）"
                    result = result.replace(code_match.group(0), replacement)
                except Exception as exec_err:
                    result = result.replace(code_match.group(0), f"代码执行异常: {str(exec_err)}")

        if "[TOOL:search]" in result:
            search_match = re.search(r'\[TOOL:search\]\s*(.*?)\s*\[/TOOL\]', result, re.DOTALL)
            if search_match:
                query = search_match.group(1).strip()
                result = result.replace(
                    search_match.group(0),
                    f"（搜索关键词：{query}，基于知识回答）"
                )

        return result

    async def _react_correct(self, step: PlanStep, error: str,
                              ctx: PipelineContext, llm) -> Optional[str]:
        """ReAct 纠错：分析错误并提出替代方案"""
        react_messages = [
            {"role": "user", "content": f"""原始任务：{ctx.text}

之前尝试执行步骤「{step.description}」时遇到了问题：
错误信息：{error}

请分析错误原因并提出替代方案来完成任务。输出：
1. 错误原因分析
2. 替代方案 / 修正后的结果

如果无法纠正，请说明原因并给出最佳建议。"""},
        ]

        corrected = await self._llm_non_stream(
            llm, react_messages,
            system_prompt="你是问题解决助手，当任务执行遇到问题时，分析原因并提出替代方案。"
        )

        return corrected if corrected else None

    async def _generate_final_answer(self, ctx: PipelineContext, llm,
                                      plan: ExecutionPlan) -> str:
        """基于所有步骤结果，生成最终汇总答案"""
        steps_summary = ""
        for step in plan.steps:
            status_icon = "✅" if step.status == "done" else "❌"
            result_text = step.result or step.error or "无结果"
            steps_summary += f"\n{status_icon} 步骤{step.step_id}: {step.description}\n  结果: {result_text[:200]}"

        final_messages = [
            {"role": "user", "content": f"""原始任务：{ctx.text}

执行过程和结果汇总：{steps_summary}

请基于以上信息，给出一个完整、清晰、结构化的最终答案。如果需要，请标注要点。"""},
        ]

        full = ""
        async for chunk in llm.async_stream_chat_with_history(final_messages):
            if isinstance(chunk, tuple):
                continue
            full += chunk
            await ctx.awrite_sse(chunk)

        return full
