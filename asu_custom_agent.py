import os
import sys
import json
import uuid
import sqlite3
import time
import asyncio
import subprocess
import tempfile
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from llm_provider import MiniMaxProvider, LocalProvider, MiMoProvider, load_config

# 导入记忆系统改进模块
from opencopilot.capabilities.memory.config import ConfigManager, MemoryType
from opencopilot.capabilities.memory.quota_manager import QuotaManager
from opencopilot.capabilities.memory.core import MemoryManager

# 导入统一 Prompt 构建服务
from prompt_builder import (
    CONTEXT_DESCRIPTIONS,
    CONTEXT_SOURCE_PRIORITY,
    PERSONA_CONFLICT_PATTERNS,
    build_context_prefix,
    sanitize_persona_for_context,
    load_persona,
)

# 导入代码执行引擎模块
from opencopilot.capabilities.coding import CodeExecutor, ExecutorConfig

# 导入上下文管理模块
from context_manager import ContextWindowManager as ContextWindowManagerModule

# 导入知识检索模块
from opencopilot.capabilities.knowledge import KnowledgeRetrieval

# 导入搜索能力模块
from opencopilot.capabilities.search import SearchCapability, SearchType

# 导入状态管理模块
from opencopilot.capabilities.state import StateManager, get_default_manager as get_state_manager

# 导入规划器模块
from opencopilot.safety.planner import Planner, PlanRequest

# 导入安全模块
from opencopilot.safety.security import SecurityModule, SecurityConfig

# 导入可观测性模块
from opencopilot.observability import ObservabilityModule, ObservabilityConfig, LogLevel

# 导入AGENTS.md免疫机制模块
from opencopilot.safety.immune import ImmuneSystem, RuleEngine

# 导入Skill化架构模块
from opencopilot.capabilities.skill import SkillRegistry, IntentRouter, SkillExecutor, SkillDiscovery

# 导入中间件管线
from opencopilot.agent import (
    PipelineContext, MiddlewarePipeline,
    SecurityGuardMiddleware, ImmuneSystemMiddleware,
    PlannerMiddleware, StateTrackingMiddleware,
    CapabilityRouterMiddleware, LLMProviderMiddleware, ReviewMiddleware,
    SessionSetupMiddleware,
)


# ==========================================
# Context Window 管理（P0）
# ==========================================

class ContextWindowManager:
    """基于预算的上下文窗口管理器（字符预算近似 token 预算）。"""

    # 模型上下文限制映射（token 数）
    MODEL_CONTEXT_LIMITS = {
        "minimax-m2.7": 200000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
        "claude-3-haiku": 200000,
    }

    def __init__(self, max_input_chars=120000, reserve_output_chars=30000,
                 recent_turns=12, max_history_msg_chars=8000,
                 model_name: str = None):
        self.max_input_chars = max_input_chars
        self.reserve_output_chars = reserve_output_chars
        self.recent_turns = recent_turns
        self.max_history_msg_chars = max_history_msg_chars
        self.model_name = model_name
        
        # 添加配置管理器
        self.config_manager = ConfigManager()
        
        # 如果指定了模型，动态调整配置
        if model_name:
            self.adjust_for_model(model_name)
    
    def adjust_for_model(self, model_name: str):
        """根据模型能力动态调整配置"""
        # 使用配置管理器获取模型限制
        budget_config = self.config_manager.get_context_budget()
        model_limit = budget_config.model_limits.get(model_name, 200000)
        
        # 将 token 限制转换为字符限制（中文约 1.5-2 token/字符）
        # 使用保守估计：1 token ≈ 1.5 字符
        max_chars = int(model_limit * 0.75)  # 75% 的 token 限制作为字符限制
        
        # 调整配置
        self.max_input_chars = max_chars
        self.reserve_output_chars = max_chars // 4  # 预留 25% 给输出
        self.max_history_msg_chars = min(8000, max_chars // 15)  # 单条消息不超过总预算的 1/15
        
        # 根据模型能力调整轮数
        if model_limit >= 100000:
            self.recent_turns = 12
        elif model_limit >= 32000:
            self.recent_turns = 8
        elif model_limit >= 8000:
            self.recent_turns = 4
        else:
            self.recent_turns = 2
        
        return self

    def _truncate_text(self, text, limit):
        if not text or limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        marker = "\n\n...[已截断]...\n\n"
        marker_len = len(marker)
        if limit <= marker_len + 20:
            return text[:limit]
        head = int((limit - marker_len) * 0.7)
        tail = limit - marker_len - head
        return text[:head] + marker + text[-tail:]

    def _clip_by_source(self, source, text, limit):
        """按来源做裁剪策略：IDE 保留头尾，Browser 偏头部，其他常规截断。"""
        if not text or limit <= 0:
            return ""
        if len(text) <= limit:
            return text

        if source == "ide":
            marker = "\n\n...[IDE内容已裁剪，保留头尾关键片段]...\n\n"
            marker_len = len(marker)
            if limit <= marker_len + 20:
                return text[:limit]
            head = int((limit - marker_len) * 0.55)
            tail = limit - marker_len - head
            return text[:head] + marker + text[-tail:]

        if source == "browser":
            marker = "\n\n...[网页正文已裁剪]...\n\n"
            marker_len = len(marker)
            if limit <= marker_len + 20:
                return text[:limit]
            head = limit - marker_len
            return text[:head] + marker

        return self._truncate_text(text, limit)

    def _build_user_payload(self, envelope, budget):
        source = envelope.get("source", "drag")
        content = envelope.get("content", "")
        selection = envelope.get("selection", "")
        task = envelope.get("task", "")
        custom_instruction = envelope.get("custom_instruction", "")
        meta = envelope.get("meta", {}) or {}

        # 元信息摘要
        meta_parts = []
        for k in ("file_name", "language", "app_name", "title", "url"):
            v = meta.get(k)
            if v:
                meta_parts.append(f"{k}={v}")
        # custom_instruction 优先从 envelope 顶层取，其次从 meta 取
        if not custom_instruction:
            custom_instruction = meta.get("custom_instruction", "")
        meta_text = "；".join(meta_parts)

        # 先构建骨架，再把正文按剩余预算裁剪
        payload_parts = [f"[context_source] {source}"]
        if task:
            payload_parts.append(f"[task] {task}")
        if meta_text:
            payload_parts.append(f"[meta] {meta_text}")
            
        # 注入高级 IDE 上下文
        diagnostics = meta.get("diagnostics")
        if diagnostics and isinstance(diagnostics, list):
            diag_lines = []
            for d in diagnostics:
                sev_idx = d.get("severity", 0)
                severity = ["Error", "Warning", "Information", "Hint"][sev_idx] if isinstance(sev_idx, int) and 0 <= sev_idx <= 3 else "Error"
                diag_lines.append(f"- Line {d.get('line')}: [{severity}] {d.get('message')}")
            if diag_lines:
                payload_parts.append("[diagnostics] (当前文件存在的诊断报错)\n" + "\n".join(diag_lines))
        
        git_diff = meta.get("git_diff")
        if git_diff and isinstance(git_diff, str) and git_diff.strip():
            payload_parts.append(f"[git_diff] (当前文件的未提交变更)\n{git_diff[:2000]}") # 限制长度防止超限

        if custom_instruction:
            payload_parts.append(
                f"[custom_instruction]\n{custom_instruction}\n\n"
                f"请严格按照上述指令对 [selection] 或当前代码块中的文本进行修改，只输出修改后的文本，不要输出任何解释或说明。"
            )
        if selection:
            payload_parts.append(f"[selection]\n{selection}")

        skeleton = "\n\n".join(payload_parts)
        remaining = max(0, budget - len(skeleton) - 20)
        clipped_content = self._clip_by_source(source, content, remaining)
        payload_parts.append(f"[content]\n{clipped_content}")

        return "\n\n".join(payload_parts)

    def _pick_recent_history(self, history_messages, budget):
        """保留最近若干轮历史，并对单条消息做上限截断。"""
        if budget <= 0:
            return []

        selected = []
        recent_msgs = history_messages[-(self.recent_turns * 2):] if self.recent_turns > 0 else history_messages

        # 从最近往前装，保证时序时再翻转
        for msg in reversed(recent_msgs):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            clipped = self._truncate_text(content, self.max_history_msg_chars)
            unit = len(role) + len(clipped) + 16
            if unit > budget:
                break
            selected.append({"role": role, "content": clipped})
            budget -= unit

        selected.reverse()
        return selected

    def build_messages(self, system_prompt, envelope, history_messages):
        """生成最终发给模型的消息列表。"""
        sys_unit = len(system_prompt)
        total_budget = max(1500, self.max_input_chars - self.reserve_output_chars)
        remaining = max(0, total_budget - sys_unit)

        # 历史与当前输入按 45/55 分配预算
        history_budget = int(remaining * 0.45)
        user_budget = max(500, remaining - history_budget)

        history_msgs = self._pick_recent_history(history_messages, history_budget)
        user_payload = self._build_user_payload(envelope, user_budget)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history_msgs)
        messages.append({"role": "user", "content": user_payload})
        return messages


def normalize_context_envelope(req, fallback_text, fallback_source, fallback_meta):
    """兼容新旧协议：优先 context_envelope，其次旧字段。同时确保 custom_instruction 不丢失。"""
    env = req.get("context_envelope")
    safe_fallback_meta = fallback_meta if isinstance(fallback_meta, dict) else {}

    if isinstance(env, dict):
        raw_meta = env.get("meta", safe_fallback_meta)
        safe_meta = raw_meta if isinstance(raw_meta, dict) else {}
        envelope = {
            "source": env.get("source", fallback_source),
            "content": env.get("content", fallback_text),
            "selection": env.get("selection", ""),
            "task": env.get("task", safe_meta.get("task", "")),
            "meta": safe_meta,
            "timestamp": env.get("timestamp", time.time()),
        }
        # 确保 custom_instruction 从 context_meta 合并进 envelope（envelope meta 可能缺少）
        ci = safe_fallback_meta.get("custom_instruction", "")
        if ci and "custom_instruction" not in safe_meta:
            envelope["custom_instruction"] = ci
        elif ci and safe_meta.get("custom_instruction"):
            envelope["custom_instruction"] = safe_meta["custom_instruction"]
    else:
        envelope = {
            "source": fallback_source,
            "content": fallback_text,
            "selection": "",
            "task": safe_fallback_meta.get("task", ""),
            "meta": safe_fallback_meta,
            "timestamp": time.time(),
        }
        ci = safe_fallback_meta.get("custom_instruction", "")
        if ci:
            envelope["custom_instruction"] = ci

    # 兜底：弱类型输入统一转字符串，避免拼装阶段异常
    envelope["source"] = str(envelope.get("source", fallback_source) or fallback_source)
    envelope["content"] = str(envelope.get("content", "") or "")
    envelope["selection"] = str(envelope.get("selection", "") or "")
    envelope["task"] = str(envelope.get("task", "") or "")
    return envelope

# prompt_builder 模块已统一管理 CONTEXT_DESCRIPTIONS、build_context_prefix、
# sanitize_persona_for_context、load_persona 等函数
# 如需修改，请编辑 prompt_builder.py


class ASUAgentMemory:
    """[DEPRECATED] 已由 memory_system.MemoryManager 替代，保留用于向后兼容。"""

    def __init__(self, db_path="asu_agent.db"):
        self.db_path = db_path
        self._init_db()
        # 添加配置和配额管理器
        self.config_manager = ConfigManager()
        self.quota_manager = QuotaManager(self.config_manager)

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    persona TEXT DEFAULT 'default',
                    updated_at REAL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp REAL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            ''')
            conn.commit()

    def get_context(self, session_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT persona FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                persona = row[0]
            else:
                persona = "default"
                cursor.execute("INSERT INTO sessions (session_id, persona, updated_at) VALUES (?, ?, ?)", 
                               (session_id, persona, time.time()))
                conn.commit()

            cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,))
            messages = [{"role": r[0], "content": r[1]} for r in cursor.fetchall()]
            
            return {"messages": messages, "persona": persona}

    def add_message(self, session_id, role, content):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO sessions (session_id, persona, updated_at) VALUES (?, 'default', ?)", 
                           (session_id, time.time()))
            cursor.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                           (session_id, role, content, time.time()))
            cursor.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (time.time(), session_id))
            conn.commit()
        
        # 新增：检查并执行配额
        self._check_and_enforce_quota(session_id)

    def _check_and_enforce_quota(self, session_id):
        """检查并执行配额"""
        try:
            # 获取当前会话的所有消息
            ctx = self.get_context(session_id)
            messages = ctx["messages"]
            
            # 按角色分类消息（简化处理，实际应按记忆类型分类）
            user_messages = [m for m in messages if m["role"] == "user"]
            assistant_messages = [m for m in messages if m["role"] == "assistant"]
            
            # 检查用户消息配额
            user_stats = self.quota_manager.get_memory_stats(
                [{"memory_id": f"user_{i}", "content": m["content"], "importance": 0.5, 
                  "access_count": 1, "created_at": time.time() - 86400 * (len(user_messages) - i)} 
                 for i, m in enumerate(user_messages)],
                MemoryType.SHORT_TERM
            )
            
            is_within_quota, reason = self.quota_manager.check_quota(MemoryType.SHORT_TERM, user_stats)
            if not is_within_quota:
                print(f"警告: 用户消息配额超出 - {reason}")
                # 可以在这里实现自动清理逻辑
        except Exception as e:
            # 配额检查失败不应影响正常功能
            pass

    def set_persona(self, session_id, persona):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            # SQLite upsert equivalent
            cursor.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE sessions SET persona = ?, updated_at = ? WHERE session_id = ?", 
                               (persona, time.time(), session_id))
            else:
                cursor.execute("INSERT INTO sessions (session_id, persona, updated_at) VALUES (?, ?, ?)", 
                               (session_id, persona, time.time()))
            conn.commit()

    def clear(self, session_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor.execute("UPDATE sessions SET persona = 'default', updated_at = ? WHERE session_id = ?", (time.time(), session_id))
            conn.commit()

    def session_count(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sessions")
            row = cursor.fetchone()
            return row[0] if row else 0

    def cleanup_old_messages(self, session_id, days_threshold=30):
        """清理旧消息"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cutoff_time = time.time() - (days_threshold * 24 * 60 * 60)
            cursor.execute("DELETE FROM messages WHERE session_id = ? AND timestamp < ?", 
                           (session_id, cutoff_time))
            conn.commit()

    def get_quota_usage(self, session_id):
        """获取配额使用情况"""
        ctx = self.get_context(session_id)
        messages = ctx["messages"]
        
        # 按角色分类消息
        user_messages = [m for m in messages if m["role"] == "user"]
        assistant_messages = [m for m in messages if m["role"] == "assistant"]
        
        # 获取统计信息
        user_stats = self.quota_manager.get_memory_stats(
            [{"memory_id": f"user_{i}", "content": m["content"], "importance": 0.5, 
              "access_count": 1, "created_at": time.time() - 86400 * (len(user_messages) - i)} 
             for i, m in enumerate(user_messages)],
            MemoryType.SHORT_TERM
        )
        
        assistant_stats = self.quota_manager.get_memory_stats(
            [{"memory_id": f"assistant_{i}", "content": m["content"], "importance": 0.6, 
              "access_count": 2, "created_at": time.time() - 86400 * (len(assistant_messages) - i)} 
             for i, m in enumerate(assistant_messages)],
            MemoryType.SHORT_TERM
        )
        
        return {
            "user": {
                "count": user_stats.count,
                "total_chars": user_stats.total_chars,
                "avg_importance": user_stats.avg_importance,
            },
            "assistant": {
                "count": assistant_stats.count,
                "total_chars": assistant_stats.total_chars,
                "avg_importance": assistant_stats.avg_importance,
            }
        }


memory = MemoryManager(db_path="asu_agent.db")
window_manager = ContextWindowManager(
    max_input_chars=int(os.getenv("ASU_MAX_INPUT_CHARS", "120000")),  # MiniMax M2.7 支持 200K token，约 120K 字符
    reserve_output_chars=int(os.getenv("ASU_RESERVE_OUTPUT_CHARS", "30000")),  # 预留 30K 字符给输出
    recent_turns=int(os.getenv("ASU_RECENT_TURNS", "12")),  # 增加到 12 轮对话
    max_history_msg_chars=int(os.getenv("ASU_MAX_HISTORY_MSG_CHARS", "8000")),  # 增加单条消息限制
)

# ==========================================
# 初始化核心能力模块
# ==========================================

# 代码执行引擎
code_executor = CodeExecutor(ExecutorConfig(
    default_timeout=30,
    max_timeout=60,
    working_directory=os.getcwd()
))

# 知识检索模块
knowledge_retrieval = KnowledgeRetrieval()

# 搜索能力模块
search_capability = SearchCapability(workspace=os.getcwd())

# 状态管理模块
state_manager = get_state_manager()

# 规划器模块
planner = Planner()

# 安全模块
security_config = SecurityConfig(
    enable_audit_logging=True,
    enable_rate_limiting=True,
    enable_permission_check=True
)
security_module = SecurityModule(security_config)

# 可观测性模块
observability_config = ObservabilityConfig(
    log_level=LogLevel.INFO.value,
    enable_tracing=True,
    enable_performance_monitoring=True
)
observability = ObservabilityModule(observability_config)

# AGENTS.md免疫机制
immune_system = ImmuneSystem()

# Skill化架构
skill_registry = SkillRegistry()
skill_router = IntentRouter(skill_registry)
skill_executor = SkillExecutor(skill_registry, skill_router)

# Skill自动发现
skill_discovery = SkillDiscovery(skill_registry)
discovered_skills = skill_discovery.discover()

print("✅ 核心能力模块初始化完成:")
print("  - CodeExecutor (代码执行)")
print("  - KnowledgeRetrieval (知识检索)")
print("  - SearchCapability (搜索能力)")
print("  - StateManager (状态管理)")
print("  - Planner (规划器)")
print("  - SecurityModule (安全模块)")
print("  - ObservabilityModule (可观测性)")
print("  - ImmuneSystem (AGENTS.md免疫机制)")
print("  - SkillRegistry (Skill化架构)")
print(f"  - 发现 {len(discovered_skills)} 个 Skills")

def get_base_llm():
    config = load_config()
    provider_type = config.get("provider_type", "mimo")
    if provider_type == "local":
        return LocalProvider(
            api_base=config.get("local_api_base", "http://localhost:11434/v1"),
            model=config.get("local_model", "llama3"),
            api_key=config.get("local_api_key", "sk-local")
        )
    if provider_type == "minimax":
        return MiniMaxProvider()
    # 默认使用 MiMo（按量计费，性价比高）
    return MiMoProvider()


def detect_request_type(text: str) -> str:
    """
    检测用户请求类型
    
    Returns:
        "code_execution" - 需要执行代码
        "knowledge_query" - 知识检索
        "search" - 搜索请求
        "planning" - 任务规划
        "security" - 安全相关
        "skill" - Skill执行
        "ppt" - PPT相关
        "coding" - 编码辅助
        "evaluation" - 评估相关
        "chat" - 普通对话
    """
    text_lower = text.lower()
    
    # 代码执行关键词
    code_keywords = [
        "执行代码", "运行代码", "运行一下", "执行一下",
        "查询tushare", "查询数据库", "查询数据",
        "运行python", "运行脚本", "执行脚本",
        "帮我算", "帮我计算", "帮我统计",
        "import ", "def ", "print(", "python代码"
    ]
    
    # 知识检索关键词
    knowledge_keywords = [
        "知识图谱", "知识检索", "查找关系", "查找实体",
        "项目结构", "模块关系", "代码依赖"
    ]
    
    # PPT相关关键词（优先级高：避免 PPT prompt 中含"规划"等词时被 planning/security 抢先命中）
    ppt_keywords = [
        "ppt", "幻灯片", "演示文稿", "大纲",
        "ppt编辑", "ppt共创", "ppt建议", "ppt生成",
        "slide", "presentation"
    ]
    
    # 搜索关键词
    search_keywords = [
        "搜索", "查找", "查一下", "搜一下",
        "网上搜索", "搜索一下", "帮我查"
    ]
    
    # 任务规划关键词
    planning_keywords = [
        "规划", "计划", "分解任务", "任务分解",
        "制定方案", "执行步骤", "工作流程"
    ]
    
    # 安全相关关键词
    security_keywords = [
        "权限", "安全", "审批", "审计",
        "访问控制", "rate limit", "速率限制"
    ]
    
    # 编码辅助关键词
    coding_keywords = [
        "代码审查", "code review", "bug修复", "代码解释",
        "代码重构", "refactor", "代码分析", "代码增强",
        "skill", "技能"
    ]
    
    # 评估关键词
    evaluation_keywords = [
        "评估", "评价", "评分", "质量检查",
        "evaluate", "score", "quality"
    ]
    
    # 检测优先级：code → knowledge → ppt → search → planning → security → coding → evaluation
    # PPT 检测提前到第 3 位，避免 PPT prompt 中含"规划""安全"等词时被误路由
    for keyword in code_keywords:
        if keyword in text_lower:
            return "code_execution"
    
    for keyword in knowledge_keywords:
        if keyword in text_lower:
            return "knowledge_query"
    
    for keyword in ppt_keywords:
        if keyword in text_lower:
            return "ppt"
    
    for keyword in search_keywords:
        if keyword in text_lower:
            return "search"
    
    for keyword in planning_keywords:
        if keyword in text_lower:
            return "planning"
    
    for keyword in security_keywords:
        if keyword in text_lower:
            return "security"
    
    for keyword in coding_keywords:
        if keyword in text_lower:
            return "coding"
    
    for keyword in evaluation_keywords:
        if keyword in text_lower:
            return "evaluation"
    
    return "chat"


# 构建中间件管线（注入 tracer 实现自动追踪）
from opencopilot.observability.tracer import DistributedTracer
tracer = DistributedTracer(observability_config)

pipeline = MiddlewarePipeline(tracer=tracer)
pipeline.use(SessionSetupMiddleware(
    memory=memory,
    window_manager=window_manager,
    normalize_context_envelope=normalize_context_envelope,
    load_persona=load_persona,
    build_context_prefix=build_context_prefix,
    sanitize_persona_for_context=sanitize_persona_for_context,
))
pipeline.use(SecurityGuardMiddleware(security_module))
pipeline.use(ImmuneSystemMiddleware(immune_system))
pipeline.use(PlannerMiddleware(planner))
pipeline.use(StateTrackingMiddleware(state_manager))
pipeline.use(CapabilityRouterMiddleware(
    code_executor=code_executor,
    knowledge_retrieval=knowledge_retrieval,
    search_capability=search_capability,
    skill_executor=skill_executor,
    skill_registry=skill_registry,
    skill_router=skill_router,
    detect_request_type=detect_request_type,
))
pipeline.use(ReviewMiddleware())
pipeline.use(LLMProviderMiddleware(
    memory=memory,
    get_base_llm=get_base_llm,
))
print("✅ 中间件管线构建完成 (8层 + 追踪):")
print("  0. DistributedTracer (自动追踪)")
print("  1. SessionSetupMiddleware (会话初始化)")
print("  2. SecurityGuardMiddleware (权限+限流)")
print("  3. ImmuneSystemMiddleware (规则检查)")
print("  4. PlannerMiddleware (任务自动规划)")
print("  5. StateTrackingMiddleware (状态追踪)")
print("  6. CapabilityRouterMiddleware (能力路由)")
print("  7. ReviewMiddleware (审查注入)")
print("  8. LLMProviderMiddleware (LLM调用)")




class AgentHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """健康检查端点。"""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = {
                "status": "ok",
                "active_sessions": memory.session_count()
            }
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode('utf-8'))
        elif self.path == '/quota':
            # 新增：配额使用情况端点
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            
            # 获取所有会话的配额使用情况
            quota_usage = {}
            with memory._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT session_id FROM sessions")
                sessions = cursor.fetchall()
                
                for (session_id,) in sessions:
                    quota_usage[session_id] = memory.get_quota_usage(session_id)
            
            resp = {
                "status": "ok",
                "quota_usage": quota_usage
            }
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode('utf-8'))
        elif self.path == '/capabilities':
            # 能力查询端点
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            
            capabilities = {
                "status": "ok",
                "agent_name": "OpenCopilot ASU Agent",
                "version": "3.0.0",
                "capabilities": {
                    "code_execution": {
                        "name": "代码执行",
                        "description": "执行 Python、JavaScript、Shell 代码",
                        "supported_languages": ["python", "javascript", "shell"],
                        "status": "active"
                    },
                    "knowledge_retrieval": {
                        "name": "知识检索",
                        "description": "查询项目知识图谱，查找实体关系和代码依赖",
                        "status": "active" if knowledge_retrieval._initialized else "ready"
                    },
                    "search": {
                        "name": "搜索能力",
                        "description": "网络搜索、代码搜索、文档搜索",
                        "search_types": ["web", "code", "doc", "knowledge"],
                        "status": "active"
                    },
                    "context_management": {
                        "name": "上下文管理",
                        "description": "智能上下文窗口管理，支持多种模型",
                        "status": "active"
                    },
                    "memory_system": {
                        "name": "记忆系统",
                        "description": "会话记忆和长期记忆管理",
                        "status": "active"
                    },
                    "state_management": {
                        "name": "状态管理",
                        "description": "任务状态管理、检查点、恢复机制",
                        "status": "active"
                    },
                    "planning": {
                        "name": "任务规划",
                        "description": "任务分解、计划生成、执行优化",
                        "strategies": ["sequential", "parallel", "adaptive", "react"],
                        "status": "active"
                    },
                    "security": {
                        "name": "安全模块",
                        "description": "权限管理、审计日志、审批流程、速率限制",
                        "features": ["rbac", "audit", "approval", "rate_limiting"],
                        "status": "active"
                    },
                    "observability": {
                        "name": "可观测性",
                        "description": "结构化日志、指标收集、分布式追踪、健康检查",
                        "features": ["logging", "metrics", "tracing", "health_check"],
                        "status": "active"
                    },
                    "agents_md": {
                        "name": "AGENTS.md免疫机制",
                        "description": "项目级行为规则系统，定义Agent行为规范",
                        "rule_types": ["behavior", "constraint", "preference", "workflow", "security"],
                        "status": "active"
                    },
                    "skill_architecture": {
                        "name": "Skill化架构",
                        "description": "意图路由、Skill执行引擎、自动发现",
                        "registered_skills": len(skill_registry.skills) if hasattr(skill_registry, 'skills') else 0,
                        "status": "active"
                    }
                },
                "request_types": {
                    "code_execution": "执行代码、查询数据库、运行脚本",
                    "knowledge_query": "知识图谱查询、实体关系查找",
                    "search": "网络搜索、代码搜索、文档搜索",
                    "planning": "任务规划、计划生成",
                    "security": "安全检查、权限管理",
                    "skill": "Skill执行、意图路由",
                    "ppt": "PPT生成、共创、建议、分析",
                    "coding": "代码审查、Bug修复、代码解释、重构",
                    "evaluation": "评估、评分、质量检查",
                    "chat": "普通对话"
                },
                "modules_status": {
                    "total_modules": 12,
                    "active_modules": 12,
                    "integration_level": "full"
                }
            }
            self.wfile.write(json.dumps(capabilities, ensure_ascii=False, indent=2).encode('utf-8'))
        elif self.path == '/traces':
            # 追踪查询端点
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            traces = tracer.get_traces(limit=50)
            resp = {
                "status": "ok",
                "count": len(traces),
                "traces": [
                    {
                        "trace_id": t.trace_id,
                        "status": t.status,
                        "duration_ms": t.duration_ms,
                        "spans": [
                            {
                                "span_id": s.span_id,
                                "operation": s.operation,
                                "status": s.status,
                                "duration_ms": s.duration_ms,
                                "tags": s.tags,
                            }
                            for s in t.spans
                        ],
                        "tags": t.tags,
                    }
                    for t in traces
                ],
            }
            self.wfile.write(json.dumps(resp, ensure_ascii=False, indent=2).encode('utf-8'))
        elif self.path == '/traces/stats':
            # 追踪统计端点（必须在 startswith('/traces/') 之前）
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(tracer.get_stats(), ensure_ascii=False, indent=2).encode('utf-8'))
        elif self.path.startswith('/traces/'):
            # 单条追踪查询
            trace_id = self.path[len('/traces/'):]
            t = tracer.get_trace(trace_id)
            if t:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                resp = {
                    "trace_id": t.trace_id,
                    "status": t.status,
                    "duration_ms": t.duration_ms,
                    "spans": [
                        {
                            "span_id": s.span_id,
                            "parent_id": s.parent_id,
                            "operation": s.operation,
                            "status": s.status,
                            "duration_ms": s.duration_ms,
                            "tags": s.tags,
                            "logs": s.logs,
                        }
                        for s in t.spans
                    ],
                    "tags": t.tags,
                }
                self.wfile.write(json.dumps(resp, ensure_ascii=False, indent=2).encode('utf-8'))
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/v1/agent/chat':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            req = json.loads(post_data.decode('utf-8'))

            action_type = req.get('action_type', 'default')
            text = req.get('text', '')
            session_id = req.get('session_id', str(uuid.uuid4()))
            is_new_task = req.get('is_new_task', False)

            # Web Search 参数
            enable_web_search = req.get('enable_web_search', False)
            web_search_force = req.get('web_search_force', False)
            web_search_max_keyword = req.get('web_search_max_keyword', 3)
            web_search_limit = req.get('web_search_limit', 3)
            web_search_user_location = req.get('web_search_user_location', None)

            ctx = PipelineContext(
                request=req,
                session_id=session_id,
                text=text,
                action_type=action_type,
                is_new_task=is_new_task,
                enable_web_search=enable_web_search,
                web_search_force=web_search_force,
                web_search_max_keyword=web_search_max_keyword,
                web_search_limit=web_search_limit,
                web_search_user_location=web_search_user_location,
            )
            ctx.metadata["_handler"] = self
            ctx.stream_writer = self.wfile

            print(f"[DEBUG] Pipeline processing: {text[:50]}...", flush=True)

            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()

            # 创建异步队列，桥接 async Pipeline → sync HTTP wfile
            # 通过线程安全队列 + 全局持久化 event loop 实现跨线程桥接
            import queue as thread_queue
            _async_queue = asyncio.Queue()
            ctx.use_async_queue(_async_queue)
            result_queue = thread_queue.Queue()

            async def _run_and_bridge():
                """在全局 loop 中运行管线，将结果桥接到线程安全队列"""
                try:
                    await pipeline.execute(ctx)
                except Exception as e:
                    result_queue.put(("error", str(e)))
                    return
                # 将 asyncio.Queue 转移到 thread-safe Queue
                while True:
                    try:
                        line = _async_queue.get_nowait()
                        result_queue.put(("sse", line))
                    except asyncio.QueueEmpty:
                        break
                result_queue.put(("done", None))

            from opencopilot.agent.caller import _EventLoopBridge
            loop = _EventLoopBridge.get_loop()
            future = asyncio.run_coroutine_threadsafe(_run_and_bridge(), loop)

            try:
                future.result(timeout=120)
            except Exception as e:
                print(f"[DEBUG] Pipeline fatal error: {e}", flush=True)
                traceback.print_exc()
                self.wfile.write(f"data: {json.dumps({'chunk': f'\\n[Agent Error]: {str(e)}'})}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                if hasattr(self.wfile, 'flush'):
                    self.wfile.flush()
                return

            # 从线程安全队列中取出所有 SSE 数据，写入 HTTP 响应流
            while True:
                try:
                    msg_type, msg = result_queue.get_nowait()
                except thread_queue.Empty:
                    break
                if msg_type == "done":
                    break
                elif msg_type == "error":
                    self.wfile.write(f"data: {json.dumps({'chunk': f'\\n[Agent Error]: {msg}'})}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    break
                elif msg_type == "sse":
                    self.wfile.write(msg.encode('utf-8'))
            if hasattr(self.wfile, 'flush'):
                self.wfile.flush()

            if ctx.should_short_circuit and ctx.response_content:
                ctx.write_sse(ctx.response_content)
                ctx.write_sse_done()
                if hasattr(self.wfile, 'flush'):
                    self.wfile.flush()
                memory.add_message(session_id, "assistant", ctx.response_content)

            print(f"[DEBUG] Pipeline complete", flush=True)
        else:
            self.send_response(404)
            self.end_headers()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """支持多线程的HTTP服务器"""
    daemon_threads = True
    allow_reuse_address = True


def run_server(port=18888):
    server_address = ('127.0.0.1', port)
    httpd = ThreadedHTTPServer(server_address, AgentHTTPRequestHandler)
    print(f"🚀 ASU 定制智能体已启动，监听在 http://127.0.0.1:{port}")
    httpd.serve_forever()


if __name__ == '__main__':
    run_server()
