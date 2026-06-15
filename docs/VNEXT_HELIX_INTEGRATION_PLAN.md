# OpenCopilot x Helix 终极融合方案：大一统泛编程引擎 (Unified Engine Blueprint)

> 版本 v2.0 | 结合“统一收口、动态降级、主动追问”的深度优化版

---

## 一、 宏观愿景：统一收口与热插拔 (The Unified Vision)

在终极融合架构中，我们**彻底放弃了双擎路由**的复杂设计。无论是开发代码、生成 PPT/Word、还是复杂的数据复核，**所有的 AI 意图统一收口，交由后台的 Helix Daemon 处理**。

- **OpenCopilot (前端 Client)**：化身为极度轻量的“UI 壳”。只负责富交互（PyQt6）、快捷键、工作区上下文采集（Broker），并将一切需求作为 `Macro Goal` 发送给后台引擎。
- **Helix (后端 Kernel)**：化身为“大一统泛编程引擎”。通过内部的意图分类器，对轻量级任务进行**动态降级**实现毫秒级响应；对重量级任务挂载**防呆沙箱**进行全量重构。

**热插拔设计原则**：OpenCopilot 与 Helix 之间通过标准 HTTP/WebSocket 或 MCP 协议通讯。这意味着 Helix 引擎可以在本地随 Client 拉起，也可以部署在云端作为企业私有化算力节点，实现引擎的随时插拔与平滑升级。

---

## 二、 Helix 侧核心升级需求 (Kernel Upgrades)

为了支撑 OpenCopilot 的泛办公需求与极致体验，Helix 底层需要进行以下 4 项关键升级：

### 1. 内部动态降级机制 (Dynamic Degradation)
- **痛点**：普通聊天或解释代码不需要拉起沉重的状态机和沙箱。
- **升级方案**：在 `session/processor.ts` 入口增加意图分类器。
  - `mode: "light"`：跳过沙箱创建、跳过 AST 分析，直接流式返回 LLM 结果（毫秒级响应）。
  - `mode: "heavy"`：拉起完整的混合状态机 (Hybrid FSM)、影子沙箱与数据飞轮。

### 2. 主动追问机制 (Proactive Clarification)
- **痛点**：用户输入意图模糊时，AI 盲猜会导致极大的 Token 浪费和执行失败。
- **升级方案**：在 FSM 中新增 `AskUserQuestion` Tool。当 LLM 在 Plan 阶段发现信息不足（如不知道数据库密码、不知道优化方向），主动触发该 Tool。通过 Event Bus 将问题抛给前端 UI，挂起状态机，等待用户答复后恢复。

### 3. 轻量级内存沙箱 (In-Memory VFS Sandbox)
- **痛点**：对于超大型 Monorepo（如 50GB 源码），传统 `git worktree` 创建沙箱耗时太久，甚至撑爆磁盘。
- **升级方案**：开发基于内存的虚拟文件系统 (VFS)。拦截大模型的 `write` 和 `bash` 工具，只对被修改的文件做“写时复制 (Copy-on-Write)”。确保沙箱创建时间控制在毫秒级。

### 4. 泛办公 Plugin 扩展 (Office Tools)
- **升级方案**：在 Helix 的 Tool 层，针对 PPT、Word、数据复核等场景提供专属能力。例如 `document_renderer` (封装 Pandoc/Python-pptx)，让大模型通过写脚本生成 PPT 也能享受到“报错自愈”的沙箱特权。

---

## 三、 OpenCopilot 侧改造需求 (Client Refactoring)

Python 客户端需要“做减法”，全面拥抱后端引擎。

### 1. 剥离繁重的 Agent 逻辑
- 彻底废除 `coding_agent/core.py` 中的 `PromptGenerator` 和直接调用 LLM API 的逻辑。
- 重写 `V5AgentWorker`：不再区分动作类型，统一调用 Helix 的 `/api/session/create` 接口，将用户的指令和选中的上下文打包发送。

### 2. 深度融合 UI 与可观测性 (Observability)
- **Trace 面板**：在 Workspace 侧边栏新增"思考树/终端"面板。通过 WebSocket 订阅 Helix 的 `TraceReporter`，实时渲染 AI 在后台的"AST 分析"、"报错"、"自愈重试"动画，提升硬核科技感。
- **追问弹窗**：监听 Helix 抛出的 `action.require_clarification` 事件，弹出输入框让用户补充信息。
- **任务偏离告警**：订阅 Helix 的 `observability.alignment_alert` 事件。当 AlignmentGuard 检测到 Agent 修改了与目标无关的文件或陷入兔子洞时，OpenCopilot 以系统通知/弹窗形式提醒用户："AI 似乎偏离了目标，是否需要干预？"

### 3. Diff 确认与局部采纳 (Partial Approval)
- Helix 在沙箱执行完毕后返回 `git diff`。
- OpenCopilot 的 `review_tab` 承接 Diff，提供类似于 SourceTree 的**交互式可勾选视图**。允许用户只采纳 AI 修改的 5 个文件中的 4 个，随后调用 Helix 的 `ApplyPatch` 接口打入真实工作区。

---

## 四、 通讯与热插拔协议 (Communication Protocol)

采用完全解耦的 C/S 架构，保障 Helix 引擎可以独立升级：

1. **守护进程管理**：OpenCopilot 启动时，通过子进程拉起打包好的 `mimo server` 二进制文件，并分配动态端口。
2. **鉴权透传**：OpenCopilot 的 UI 中配置的 API Key，在 HTTP 请求时通过 Header 传给 Helix，Helix 侧不保存用户态凭证。
3. **MCP 兼容**：长期来看，通讯协议向标准的 Model Context Protocol (MCP) 靠拢，使得 OpenCopilot 随时可以切换其他符合 MCP 标准的底层引擎，或 Helix 被其他 IDE 接入。

---

## 五、 迭代路线图 (Milestones)

- **Milestone 1: 引擎直连与轻重降级**
  - Helix 完成 `mode: "light" | "heavy"` 降级逻辑。
  - OpenCopilot 废弃本地 LLM 调用，打通 HTTP 请求。
- **Milestone 2: 体验闭环 (追问、偏离告警与沙箱)**
  - Helix 上线内存 VFS 沙箱、`AskUserQuestion` 机制与 `AlignmentGuard` 偏离观测者。
  - OpenCopilot 上线 Trace 观测面板、追问弹窗、偏离告警通知与 Diff 局部采纳视图。
- **Milestone 3: 泛办公泛化与飞轮对接**
  - Helix 上线文档处理 Plugin。
  - OpenCopilot 接入数据飞轮面板（管理 `AGENTS.md`，支持一键反思导出 DPO）。
