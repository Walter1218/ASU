# V5Plus PPT 共创模式功能增强 - 2026-06-14

## 本次更新内容

### 5. 新增审查修正引擎 (Review Engine)

新增 `opencopilot/review/` 模块，提供三类 LLM 驱动的文本审查能力：

- **幻觉检测** (`hallucination`)：检测无源数据、逻辑矛盾、不合理数值、虚构引用、时间线错误
- **数据交叉验证** (`data_check`)：将文本数值声明与 Excel/CSV 参考数据逐条比对
- **风格检查** (`style`)：检查语气不当、用词不专业、句式问题、一致性、冗余表达

核心模块：
- `models.py` - ReviewResult/ReviewIssue 数据结构
- `hallucination.py` - 幻觉检测 prompt 构建与响应解析
- `data_validator.py` - 数据验证 prompt 构建与响应解析
- `style_checker.py` - 风格检查 prompt 构建与响应解析
- `reference_loader.py` - Excel/CSV/文档参考数据加载
- `engine.py` - ReviewEngine 统一调度入口

### 6. 审查引擎 Few-Shot 优化

基于 200-case 全量 AB 实验，对三类审查 prompt 进行 few-shot 正反例教学优化：

**优化策略：**
- 幻觉检测：添加 3 条「不应标记」规则 + 3 个 few-shot 示例（2 正例 + 1 反例）
- 数据核对：添加精确匹配规则 + 3 个 few-shot 示例（含单位错误示例）
- 风格检查：添加 6 条风格豁免规则 + 3 个 few-shot 示例（含合同条款反例）

**AB 实验结果（200-case 全量）：**

| 指标 | 优化前 | 优化后 | 变化 |
|------|--------|--------|------|
| PASS 数 | 117 | 144 | +27 (+14%) |
| 负向 PASS | 60/85 | 83/85 | +23 (+27%) |
| 总 Issue 数 | 208 | 135 | -73 (-35%) |
| 风格 PASS | 35/60 | 55/60 | +20 |
| 幻觉 PASS | 55/70 | 62/70 | +7 |

### 7. 审查修正 UI 与 Pipeline 集成

- 新增 `gui/v5/review_tab.py` - Review Tab UI 组件
- 新增 `personas/review.md` - 审查修正专用 persona
- 新增 5 个 AB 实验测试脚本（`tests/e2e/test_review_*.py`）
- 新增 200-case 测试数据集（`review_test_cases.json` + `review_test_cases_v2.json`）

### 1. 新增渲染指令操作类型

在 `render_command.py` 和 `render_executor.py` 中新增以下操作类型支持：

- `delete_slide` - 删除幻灯片
- `delete_item` - 删除元素
- `modify_layout` - 修改排版布局
- `move_item` - 移动元素到其他幻灯片
- `reorder_slides` - 重新排序幻灯片
- `update_item` - 更新元素内容

### 2. 拖拽功能增强与修复

**preview_panel.py 改进：**

- 修复拖拽后悬停高亮框位置残留问题
- 修复 `_draw_hover_highlight` 和 `_draw_drop_target_highlight` 中 `custom_x/custom_y` 的支持
- 修复 `_drag_item_offset` 计算中默认 Y 坐标的动态计算（跳过已自定义位置的 item）
- 添加拖拽坐标链路埋点：`[DRAG]`, `[GHOST]`, `[DRAW]`, `[HIT]`, `[RELEASE]`

### 3. PPT 导出修复

**cocreation_window.py：**
- 修复 `generate_ppt_from_json` 调用参数格式（从 `{"slides": data}` 改为 `data`）

### 4. Prompt 模板更新

**ppt_prompt.py 和 render_prompt_generator.py：**
- 更新静态回退模板，添加新操作类型的说明
- 更新格式说明文档，包含操作类型示例

## 涉及文件

| 文件 | 修改内容 |
|------|----------|
| `gui/v5/ppt_prompt.py` | Prompt 模板更新 |
| `gui/v5plus/cocreation_window.py` | 导出参数修复 |
| `opencopilot/capabilities/ppt/preview_panel.py` | 拖拽增强、高亮修复、埋点 |
| `opencopilot/capabilities/ppt/render_command.py` | 新增操作类型 |
| `opencopilot/capabilities/ppt/render_executor.py` | 新增操作执行逻辑 |
| `opencopilot/capabilities/ppt/render_prompt_generator.py` | 格式说明更新 |

**审查修正引擎新增文件：**

| 文件 | 修改内容 |
|------|----------|
| `opencopilot/review/__init__.py` | 模块入口 |
| `opencopilot/review/models.py` | ReviewResult/ReviewIssue 数据结构 |
| `opencopilot/review/hallucination.py` | 幻觉检测 + few-shot prompt |
| `opencopilot/review/data_validator.py` | 数据核对 + few-shot prompt |
| `opencopilot/review/style_checker.py` | 风格检查 + few-shot prompt |
| `opencopilot/review/reference_loader.py` | 参考数据加载 |
| `opencopilot/review/engine.py` | ReviewEngine 统一调度 |
| `gui/v5/review_tab.py` | Review Tab UI 组件 |
| `personas/review.md` | 审查修正专用 persona |
| `tests/e2e/review_test_cases.json` | 100-case 测试数据集 |
| `tests/e2e/review_test_cases_v2.json` | 100-case 测试数据集 v2 |
| `tests/e2e/test_review_fewshot_ab.py` | Few-Shot AB 实验脚本 |
| `tests/e2e/test_review_iteration_ab.py` | 迭代优化 AB 实验脚本 |
| `tests/e2e/test_review_ab_experiment.py` | Prompt 微调 AB 实验脚本 |

## 测试状态

- ✅ 渲染指令解析测试通过
- ✅ 渲染指令执行测试通过
- ✅ 拖拽功能独立测试通过
- ✅ 审查引擎 200-case AB 实验通过（PASS 117→144，+14%）
- ⚠️ V5Plus 共创模式端到端测试待用户验证
