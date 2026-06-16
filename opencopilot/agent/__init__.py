"""
Agent 模块 — OpenCopilot Agent 核心

提供：
- 上下文窗口管理 (ContextManager, ContextWindowManager)
- 上下文裁剪策略
- 模型适配
- Pipeline 管线 (PipelineContext, MiddlewarePipeline)
- 中间件 (Security, Immune, Planner, LLM等)
- 统一调用器 (call_agent_pipeline_sync/async)
"""

# 上下文管理
from .core import ContextManager, ContextWindowManager
from .context_envelope import ContextEnvelope, normalize_context_envelope

# Pipeline 管线
from .pipeline import PipelineContext, BaseMiddleware, MiddlewarePipeline
from .middlewares import (
    SecurityGuardMiddleware,
    ImmuneSystemMiddleware,
    PlannerMiddleware,
    StateTrackingMiddleware,
    CapabilityRouterMiddleware,
    LLMProviderMiddleware,
    LLMAgentMiddleware,
    SessionSetupMiddleware,
    ReviewMiddleware,
)

# 类型定义
from .types import (
    TaskComplexity,
    AgentParadigm,
    PlanStep,
    ExecutionPlan,
    AgentTurn,
    ToolSpec,
    SkillSpec,
    AgentContextMeta,
    AsyncBaseProvider,
)

# 统一调用器
from .caller import (
    call_agent_pipeline_sync,
    call_agent_pipeline_async,
)

__all__ = [
    # 上下文
    "ContextManager",
    "ContextWindowManager",
    "ContextEnvelope",
    "normalize_context_envelope",
    # Pipeline
    "PipelineContext",
    "BaseMiddleware",
    "MiddlewarePipeline",
    # 中间件
    "SecurityGuardMiddleware",
    "ImmuneSystemMiddleware",
    "PlannerMiddleware",
    "StateTrackingMiddleware",
    "CapabilityRouterMiddleware",
    "LLMProviderMiddleware",
    "LLMAgentMiddleware",
    "SessionSetupMiddleware",
    "ReviewMiddleware",
    # 调用器
    "call_agent_pipeline_sync",
    "call_agent_pipeline_async",
    # 类型
    "TaskComplexity",
    "AgentParadigm",
    "PlanStep",
    "ExecutionPlan",
    "AgentTurn",
    "ToolSpec",
    "SkillSpec",
    "AgentContextMeta",
    "AsyncBaseProvider",
]
