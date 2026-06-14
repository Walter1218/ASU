# V5Plus + ONLYOFFICE 集成设计方案

## 文档信息

- **项目名称**：OpenCopilot V5Plus 共创模式
- **集成目标**：ONLYOFFICE Document Server
- **设计日期**：2026-06-13
- **版本**：v1.0
- **作者**：WorkBuddy

---

## 一、设计目标

### 1.1 核心目标

将 ONLYOFFICE PPT 编辑器无缝集成到 V5Plus 共创模式的 **Stage 2（编辑打磨）** 阶段，实现：

1. **保留 V5Plus 外壳**：阶段指示器、标题栏、右侧原文面板、底部导出栏
2. **替换编辑器内核**：用 ONLYOFFICE 替换当前的 `SlideRenderer`
3. **增强编辑能力**：从"预览式编辑"升级为"完整 PPT 编辑"
4. **保持协作特性**：利用 ONLYOFFICE 的原生协作能力

### 1.2 设计原则

- ✅ **最小改动**：尽量复用现有 v5plus 代码架构
- ✅ **渐进增强**：先替换编辑器，再逐步增强协作功能
- ✅ **保持一致**：保持 v5plus 的视觉语言和交互习惯
- ✅ **可回退**：保留原有 `SlideRenderer` 作为降级方案

---

## 二、架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    V5Plus CoCreationWindow                  │
│  ┌──────────────────────────────────────────────────┐    │
│  │  标题栏（保留）                                 │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  阶段指示器（保留）                               │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────┬──────────────────────────┐    │
│  │  Stage 2 核心区域                            │    │
│  │  ┌────────────────┐  ┌─────────────────────┐  │    │
│  │  │ 左侧 PPT 编辑  │  │ 右侧原文面板          │  │    │
│  │  │              │  │ (保留现有设计)        │  │    │
│  │  │  [关键替换]   │  │                     │  │    │
│  │  │  SlideRenderer │  │  - 映射标签          │  │    │
│  │  │  →            │  │  - 覆盖率进度         │  │    │
│  │  │  ONLYOFFICE    │  │  - 重新提炼          │  │    │
│  │  │  编辑器        │  │  - AI 输入           │  │    │
│  │  └────────────────┘  └─────────────────────┘  │    │
│  └──────────────────────┴──────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  底部导出栏（保留）                               │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 技术栈变化

| 组件 | 现有实现 | 新实现 | 说明 |
|------|---------|--------|------|
| **PPT 预览/编辑** | `SlideRenderer` (自定义) | ONLYOFFICE Editor | 核心替换 |
| **幻灯片数据** | `slides_data: list` (JSON) | `.pptx` 文件 | 数据格式变化 |
| **编辑交互** | 内联编辑 (`InlineEditor`) | ONLYOFFICE 原生编辑 | 能力增强 |
| **导出功能** | `ppt_generator.py` | ONLYOFFICE 导出 API | 更简单可靠 |
| **协作能力** | 无 | ONLYOFFICE 原生协作 | 新增能力 |

---

## 三、详细设计方案

### 3.1 Stage 2 布局 redesign

#### 现有布局（stage_editor.py）

```python
# 现有代码第 104-124 行
splitter = QSplitter(Qt.Orientation.Horizontal)
splitter.setHandleWidth(2)

# 中间 PPT 编辑区（60%）
self._center_panel = self._create_center_panel()
splitter.addWidget(self._center_panel)

# 右侧原文面板（40%）
self._right_panel = self._create_right_panel()
splitter.addWidget(self._right_panel)

# 设置比例
splitter.setStretchFactor(0, T.SPLIT_CENTER)  # 60%
splitter.setStretchFactor(1, T.SPLIT_RIGHT)    # 40%
```

#### 新设计方案

**核心思路**：将 `_center_panel` 中的 `SlideRenderer` 替换为 ONLYOFFICE 编辑器

```python
# 新的 _create_center_panel() 实现

def _create_center_panel(self) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    
    # 方案 A：使用 QWebEngineView 嵌入 ONLYOFFICE
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    self._onlyoffice_view = QWebEngineView()
    self._onlyoffice_view.setUrl(QUrl("http://localhost:8080/web-apps/apps/api/documents/api.js"))
    layout.addWidget(self._onlyoffice_view, stretch=1)
    
    # 方案 B：使用 QPushButton 触发外部编辑器（备选）
    # self._open_editor_btn = QPushButton("在 ONLYOFFICE 中打开")
    # self._open_editor_btn.clicked.connect(self._open_onlyoffice_external)
    # layout.addWidget(self._open_editor_btn)
    
    return panel
```

**⚠️ 关键挑战**：PyQt6 与 ONLYOFFICE 的集成方式

---

### 3.2 集成方案：直接嵌入（QWebEngineView）

**最终决策**：✅ **直接嵌入 ONLYOFFICE 编辑器**（使用 QWebEngineView）

**实现方式**：
- 在 PyQt6 中使用 `QWebEngineView` 加载 ONLYOFFICE 编辑器页面
- 通过 `QWebChannel` 实现 PyQt6 与 JavaScript 的双向通信
- 无缝集成到 v5plus 的 Stage 2 界面

**优势**：
- ✅ 无缝集成到 v5plus 界面（保留双面板布局）
- ✅ 用户体验最佳（无需切换窗口）
- ✅ 可直接访问 ONLYOFFICE 完整 API

---

#### 具体实施步骤

**第一步：修改 `_create_center_panel()` 函数**

```python
# 文件：gui/v5plus/stage_editor.py
# 修改函数：_create_center_panel()（约第 104 行）

def _create_center_panel(self) -> QWidget:
    """创建中心面板 - 嵌入 ONLYOFFICE 编辑器"""
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    
    # 创建 ONLYOFFICE 编辑器视图
    self._onlyoffice_view = QWebEngineView()
    self._onlyoffice_view.setMinimumHeight(400)
    
    # 加载 ONLYOFFICE 编辑器
    self._load_onlyoffice_editor()
    
    layout.addWidget(self._onlyoffice_view, stretch=1)
    return panel
```

**第二步：实现 `_load_onlyoffice_editor()` 函数**

```python
def _load_onlyoffice_editor(self):
    """加载 ONLYOFFICE 编辑器"""
    # 生成 HTML 页面
    html_content = self._generate_editor_html()
    
    # 加载到 WebEngineView
    self._onlyoffice_view.setHtml(html_content, QUrl("http://localhost:8080"))
```

**第三步：实现 `_generate_editor_html()` 函数**

```python
def _generate_editor_html(self) -> str:
    """生成包含 ONLYOFFICE 编辑器的 HTML 页面"""
    # 将当前 slides_data 转换为 .pptx 并上传
    document_url = self._prepare_document()
    
    html = f"""
    <!DOCTYPE html>
    <html style="height: 100%;">
    <head>
        <title>PPT 编辑器</title>
        <script type="text/javascript" src="http://localhost:8080/web-apps/apps/api/documents/api.js"></script>
        <style>
            html, body {{
                height: 100%;
                margin: 0;
                padding: 0;
                overflow: hidden;
            }}
            #editor {{
                height: 100%;
                width: 100%;
            }}
        </style>
    </head>
    <body>
        <div id="editor"></div>
        <script>
            var docEditor = new DocsAPI.DocEditor("editor", {{
                "document": {{
                    "fileType": "pptx",
                    "key": "{self._session_id}",
                    "title": "presentation.pptx",
                    "url": "{document_url}"
                }},
                "documentType": "slide",
                "editorConfig": {{
                    "mode": "edit",
                    "lang": "zh-CN",
                    "user": {{
                        "id": "user-123",
                        "name": "当前用户"
                    }}
                }},
                "events": {{
                    "onAppReady": function() {{
                        console.log("Editor ready");
                    }},
                    "onDocumentReady": function() {{
                        console.log("Document loaded");
                    }}
                }}
            });
        </script>
    </body>
    </html>
    """
    return html
```

**第四步：实现 `_prepare_document()` 函数**

```python
def _prepare_document(self) -> str:
    """将 slides_data 转换为 .pptx 并上传到服务器"""
    # 1. 导出 slides_data 为 .pptx
    from ppt_generator import generate_ppt_from_json
    import tempfile
    import os
    
    # 创建临时文件
    temp_dir = tempfile.gettempdir()
    pptx_path = os.path.join(temp_dir, f"{self._session_id}.pptx")
    
    # 生成 PPTX
    generate_ppt_from_json({"slides": self._slides_data}, pptx_path)
    
    # 2. 启动本地 HTTP 服务（如果未启动）
    self._ensure_http_server(temp_dir)
    
    # 3. 返回可访问的 URL
    document_url = f"http://localhost:8000/{self._session_id}.pptx"
    
    return document_url

def _ensure_http_server(self, directory: str):
    """确保 HTTP 服务已启动"""
    import subprocess
    import socket
    
    # 检查端口是否被占用
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', 8000))
    sock.close()
    
    if result != 0:
        # 端口未被占用，启动 HTTP 服务
        subprocess.Popen(
            ["python", "-m", "http.server", "8000", "--directory", directory],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("✅ 已启动本地 HTTP 服务（端口 8000）")
```

---

### 3.3 数据流转设计

#### 现有数据流

```
原文文本 (text)
  ↓ Stage 0: 用户输入
策略配置 (strategy_config)
  ↓ Stage 1: AI 生成
幻灯片数据 (slides_data: list)
  ↓ Stage 2: SlideRenderer 预览/编辑
导出 PPTX
```

#### 新数据流（集成 ONLYOFFICE）

```
原文文本 (text)
  ↓ Stage 0: 用户输入
策略配置 (strategy_config)
  ↓ Stage 1: AI 生成
幻灯片数据 (slides_data: list)
  ↓ 转换为 .pptx
ONLYOFFICE 编辑器
  ↓ 用户编辑
更新后的 .pptx
  ↓ 解析回 slides_data（可选）
更新右侧原文映射
  ↓
导出 PPTX
```

**关键问题**：如何将 ONLYOFFICE 的编辑结果同步回 v5plus 的数据模型？

**解决方案**：

1. **单向流转**（推荐用于 MVP）
   - v5plus 生成初始 PPT → 在 ONLYOFFICE 中编辑 → 导出最终 PPTX
   - 不需要解析回 `slides_data`

2. **双向同步**（完整方案）
   - 使用 ONLYOFFICE 的 `onDocumentStateChange` 事件
   - 定期保存 .pptx 并解析为 `slides_data`
   - 更新右侧原文面板的映射状态

---

## 四、实施计划

### 第一阶段：环境准备与基础集成（3-5 天）

**目标**：完成 ONLYOFFICE 部署和最基本的嵌入验证

**任务清单**：

- [ ] **部署 ONLYOFFICE Document Server**（1 天）
  ```bash
  # 拉取镜像
  docker pull onlyoffice/documentserver
  
  # 启动服务
  docker run -i -t -d -p 8080:80 \
    --name onlyoffice \
    -v $(pwd)/onlyoffice_data:/var/www/onlyoffice/Data \
    -v $(pwd)/onlyoffice_logs:/var/log/onlyoffice \
    onlyoffice/documentserver
  
  # 验证服务
  curl http://localhost:8080/healthcheck
  ```

- [ ] **修改 `stage_editor.py`：替换中心面板**（2 天）
  - 修改 `_create_center_panel()` 函数
  - 添加 `QWebEngineView` 加载 ONLYOFFICE 编辑器
  - 实现 `_generate_editor_html()` 函数
  - 实现 `_prepare_document()` 函数

- [ ] **测试基本加载**（1 天）
  - 启动 OpenCopilot，进入 v5plus 共创模式
  - 完成 Stage 0 和 Stage 1
  - 进入 Stage 2，验证 ONLYOFFICE 编辑器是否正常加载
  - 测试基本编辑功能（文字、图片、形状）

**交付物**：
- 可运行的 v5plus + ONLYOFFICE 集成原型（基础版）
- 环境配置文档

---

### 第二阶段：数据流转与同步（5-7 天）

**目标**：实现 `slides_data` 与 ONLYOFFICE 编辑器之间的数据同步

**任务清单**：

- [ ] **实现 `slides_data` → .pptx 转换**（2 天）
  - 确保现有 `ppt_generator.py` 能正确生成 .pptx
  - 处理特殊情况（空幻灯片、复杂布局等）

- [ ] **实现 .pptx → `slides_data` 解析**（3 天）
  - 使用 `python-pptx` 库解析编辑后的 .pptx
  - 提取文本内容、布局信息、图片等
  - 转换为 v5plus 的 `slides_data` 格式

- [ ] **实现自动同步逻辑**（2 天）
  - 添加"同步到共创模式"按钮
  - 实现点击事件：导出 .pptx → 解析 → 更新 `slides_data` → 更新右侧原文面板

**交付物**：
- 完整的数据同步功能
- 支持从 ONLYOFFICE 编辑结果同步回 v5plus

---

### 第三阶段：PyQt6 与 ONLYOFFICE 通信（3-5 天）

**目标**：实现双向通信，提升用户体验

**任务清单**：

- [ ] **学习 QWebChannel 机制**（1 天）
  - 阅读 PyQt6 WebChannel 文档
  - 创建简单的测试项目，验证通信机制

- [ ] **实现 PyQt6 → JavaScript 通信**（1-2 天）
  - 在 PyQt6 中创建 `OnlyOfficeBridge` 类
  - 实现幻灯片切换、内容更新等方法
  - 在 JavaScript 中接收并响应

- [ ] **实现 JavaScript → PyQt6 通信**（1-2 天）
  - 在 JavaScript 中调用 PyQt6 方法
  - 实现幻灯片选中事件、内容变化事件等
  - 更新右侧原文面板的映射状态

**交付物**：
- 完整的双向通信机制
- 幻灯片状态实时同步

---

### 第四阶段：协作功能（3-5 天）

**目标**：利用 ONLYOFFICE 的原生协作能力

**任务清单**：

- [ ] **配置协作模式**（1 天）
  - 确保同一文档使用相同的 `document key`
  - 验证多用户同时编辑功能

- [ ] **实现协作状态展示**（2 天）
  - 在 Stage 2 顶部或底部添加协作状态栏
  - 显示在线用户列表、编辑状态等
  - 参考 `onlyoffice-demo.html` 的设计

- [ ] **测试协作功能**（2 天）
  - 模拟多用户同时编辑
  - 验证冲突处理（依赖 ONLYOFFICE 内置的 OT 算法）
  - 记录协作延迟、同步速度等指标

**交付物**：
- 支持多人协作的 v5plus 共创模式
- 协作状态 UI

---

### 第五阶段：优化与测试（5-7 天）

**目标**：确保产品质量和用户体验

**任务清单**：

- [ ] **性能优化**（2-3 天）
  - 优化 ONLYOFFICE 加载速度（预加载、缓存）
  - 优化大文档的编辑性能
  - 减少内存占用

- [ ] **用户体验优化**（2 天）
  - 添加加载动画、错误提示
  - 优化编辑模式切换交互（如果需要保留 SlideRenderer 作为备选）
  - 完善帮助文档和提示

- [ ] **测试**（3 天）
  - 功能测试：编辑、保存、导出、协作
  - 兼容性测试：不同操作系统、不同屏幕分辨率
  - 压力测试：大文档、多用户、长时间运行
  - 编写单元测试和 E2E 测试

**交付物**：
- 生产可用的 v5plus + ONLYOFFICE 集成版本
- 完整测试覆盖
- 用户手册和技术文档

---

## 五、时间线与里程碑

### 总体时间估算

| 阶段 | 工作量 | 累计时间 |
|------|--------|----------|
| 第一阶段：环境准备与基础集成 | 3-5 天 | 1 周 |
| 第二阶段：数据流转与同步 | 5-7 天 | 2-3 周 |
| 第三阶段：PyQt6 与 ONLYOFFICE 通信 | 3-5 天 | 3-4 周 |
| 第四阶段：协作功能 | 3-5 天 | 4-5 周 |
| 第五阶段：优化与测试 | 5-7 天 | 6-7 周 |
| **总计** | **19-29 天** | **约 5-6 周** |

### 关键里程碑

- **✅ MVP 原型**（第 1 周）：基本嵌入成功，能加载 ONLYOFFICE 编辑器
- **✅ 数据同步**（第 3 周）：能双向同步 `slides_data` 和 ONLYOFFICE 编辑结果
- **✅ 双向通信**（第 4 周）：PyQt6 与 ONLYOFFICE 能实时通信
- **✅ 协作功能**（第 5 周）：支持多人协作编辑
- **✅ 生产可用**（第 6-7 周）：优化完成，通过完整测试

---

## 六、关键技术点

### 6.1 ONLYOFFICE Document Server 部署

**推荐方式**：Docker

```bash
# 拉取镜像
docker pull onlyoffice/documentserver

# 启动服务
docker run -i -t -d -p 8080:80 \
  --name onlyoffice \
  -v $(pwd)/onlyoffice_data:/var/www/onlyoffice/Data \
  -v $(pwd)/onlyoffice_logs:/var/log/onlyoffice \
  onlyoffice/documentserver

# 验证服务
curl http://localhost:8080/healthcheck
```

**生产环境建议**：
- 使用 HTTPS（配置 SSL 证书）
- 启用 JWT 保护（防止未授权访问）
- 配置持久化存储

---

### 6.2 PyQt6 与 ONLYOFFICE 通信

**核心机制**：`QWebChannel`

```python
# PyQt6 端
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

class OnlyOfficeBridge(QObject):
    """PyQt6 <-> ONLYOFFICE JavaScript Bridge"""
    
    # PyQt6 → JavaScript
    updateSlide = pyqtSignal(int, dict)
    
    # JavaScript → PyQt6
    @pyqtSlot(int)
    def onSlideSelected(self, index: int):
        print(f"Slide selected: {index}")
        # 更新右侧原文面板
        self.parent()._highlight_source_paragraphs(index)
```

```javascript
// JavaScript 端
var bridge = pyqt.webChannelTransport;
var pyqtBridge = bridge.objects.pyqtBridge;

// 调用 PyQt6 方法
pyqtBridge.onSlideSelected(0);

// 接收 PyQt6 调用
pyqtBridge.updateSlide.connect(function(index, data) {
    console.log("Slide updated:", index, data);
});
```

---

### 6.3 数据格式转换

**关键问题**：如何将 ONLYOFFICE 的编辑结果同步回 v5plus 的数据模型？

**解决方案**：

| 方向 | 方法 | 说明 |
|------|------|------|
| `slides_data` → .pptx | 使用现有 `ppt_generator.py` | 直接生成 .pptx 文件 |
| .pptx → `slides_data` | 使用 `python-pptx` 库 | 需要开发解析器 |

**实施建议**：
- 第一阶段：单向流转（生成 → 编辑 → 导出），不需要解析回 `slides_data`
- 第二阶段：双向同步，需要开发 .pptx 解析器

---

## 七、风险评估

### 7.1 技术风险

| 风险 | 影响 | 缓解方案 |
|------|------|----------|
| **PyQt6 + WebEngine 集成复杂** | 高 | 分阶段实施，先实现基本嵌入，再逐步实现通信 |
| **ONLYOFFICE 加载速度慢** | 中 | 预加载、缓存、懒加载 |
| **数据同步逻辑复杂** | 高 | 第一阶段使用单向流转，不强制同步 |
| **ONLYOFFICE API 限制** | 中 | 提前验证 API 能力，必要时联系官方 |

---

### 7.2 用户体验风险

| 风险 | 影响 | 缓解方案 |
|------|------|----------|
| **学习成本** | 低 | 保持 v5plus 现有交互习惯，逐步引导 |
| **协作冲突处理** | 高 | 依赖 ONLYOFFICE 内置的 Operational Transformation (OT) 算法 |
| **性能问题** | 中 | 优化加载速度，减少内存占用 |

---

## 八、成本估算

### 8.1 开发成本

| 阶段 | 工作量 | 说明 |
|------|--------|------|
| 第一阶段：环境准备与基础集成 | 3-5 天 | 1 人全职 |
| 第二阶段：数据流转与同步 | 5-7 天 | 1 人全职 |
| 第三阶段：PyQt6 与 ONLYOFFICE 通信 | 3-5 天 | 1 人全职 |
| 第四阶段：协作功能 | 3-5 天 | 1 人全职 |
| 第五阶段：优化与测试 | 5-7 天 | 1 人全职 |
| **总计** | **19-29 天** | **约 5-6 周** |

### 8.2 授权成本

| 项目 | 成本 | 说明 |
|------|------|------|
| ONLYOFFICE 开源版 | 免费 | AGPL-3.0 协议，需要开源修改后的代码 |
| ONLYOFFICE 商业授权 | 约 $200/用户/年 | 避免开源协议限制 |
| Docker 服务器 | 低 | 可使用现有服务器 |

**建议**：
- 前期使用开源版进行开发和验证
- 产品化时购买商业授权，避免开源协议风险

---

## 九、决策检查清单

在开始实施前，请确认以下事项：

- [ ] 已部署 ONLYOFFICE Document Server 并验证基本功能
- [ ] 已确认 ONLYOFFICE API 能满足需求（编辑、导出、协作）
- [ ] 已评估技术风险，并制定应对措施
- [ ] 已获得必要的授权（商业授权或确认开源协议要求）
- [ ] 已制定详细的测试计划

---

## 十、参考资料

- **ONLYOFFICE API 文档**：https://api.onlyoffice.com/docs/docs-api/
- **PyQt6 WebChannel 文档**：https://doc.qt.io/qt-6/qtwebchannel-index.html
- **V5Plus 现有代码**：`gui/v5plus/stage_editor.py`
- **ONLYOFFICE 集成文档**（已创建）：`ONLYOFFICE集成接入文档.md`
- **验证 Demo**：`onlyoffice-demo.html`

---

**文档结束**

如有疑问，请联系：WorkBuddy  
最后更新：2026-06-13  
版本：v1.1（已调整为直接嵌入方案）
