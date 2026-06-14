# ONLYOFFICE PPT 编辑器接入文档

## 📋 文档信息

- **项目名称**：OpenCopilot 共创模式
- **集成目标**：ONLYOFFICE Document Server
- **文档版本**：v1.0
- **更新日期**：2026-06-13
- **作者**：WorkBuddy

---

## 一、集成方案概述

### 1.1 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenCopilot 前端应用                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ 导航栏    │  │ 工具栏    │  │ AI 面板   │  │ 协作状态  │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         ONLYOFFICE 编辑器 (iframe)                    │   │
│  │  ┌────────────────────────────────────────────────┐  │   │
│  │  │           PPT 编辑画布                          │  │   │
│  │  └────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                          ↕ postMessage / API
┌─────────────────────────────────────────────────────────────┐
│              ONLYOFFICE Document Server                       │
│  - 文档编辑引擎                                              │
│  - 协作同步服务                                              │
│  - 文件转换服务                                              │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 技术栈

| 组件 | 技术选型 | 说明 |
|------|---------|------|
| **前端框架** | React 18+ | 与 OpenCopilot 保持一致 |
| **ONLYOFFICE SDK** | @onlyoffice/document-editor-react | 官方 React 组件 |
| **状态管理** | Zustand / Redux | 管理协作状态和用户信息 |
| **实时通信** | WebSocket / Server-Sent Events | 同步协作状态和 AI 生成进度 |
| **文件存储** | 对象存储 (OSS/S3) | 存储 PPT 源文件 |

---

## 二、快速接入指南

### 2.1 环境准备

#### 方案 A：使用官方 CDN（快速验证）

```html
<script type="text/javascript" src="https://documentserver.onlyoffice.com/web-apps/apps/api/documents/api.js"></script>
```

**优点**：零配置，立即可用  
**缺点**：依赖外部服务，不适合生产环境

#### 方案 B：自建 Document Server（推荐）

```bash
# 1. 拉取 Docker 镜像
docker pull onlyoffice/documentserver

# 2. 启动服务
docker run -i -t -d -p 8080:80 \
  -v /app/onlyoffice/data:/var/www/onlyoffice/Data \
  -v /app/onlyoffice/logs:/var/log/onlyoffice \
  onlyoffice/documentserver

# 3. 验证服务
curl http://localhost:8080/healthcheck
```

**生产环境建议**：
- 使用 HTTPS（配置 SSL 证书）
- 启用 JWT 保护（防止未授权访问）
- 配置持久化存储（Docker 卷映射）

---

### 2.2 React 项目集成

#### 安装依赖

```bash
npm install @onlyoffice/document-editor-react
# 或
yarn add @onlyoffice/document-editor-react
```

#### 基础组件封装

```tsx
// components/OnlyOfficeEditor.tsx
import React, { useRef, useEffect } from 'react';
import { DocumentEditor } from '@onlyoffice/document-editor-react';

interface EditorProps {
  documentUrl: string;      // 文档 URL
  documentKey: string;      // 文档唯一标识
  userName: string;         // 当前用户名
  userId: string;           // 当前用户 ID
  onSave?: (url: string) => void;  // 保存回调
  onReady?: () => void;     // 就绪回调
}

export const OnlyOfficeEditor: React.FC<EditorProps> = ({
  documentUrl,
  documentKey,
  userName,
  userId,
  onSave,
  onReady
}) => {
  const editorRef = useRef<any>(null);

  const config = {
    document: {
      fileType: 'pptx',
      key: documentKey,
      title: 'presentation.pptx',
      url: documentUrl,
      permissions: {
        edit: true,
        download: true,
        print: true,
        review: true,
        comment: true
      }
    },
    documentType: 'slide',
    editorConfig: {
      mode: 'edit',
      lang: 'zh-CN',
      user: {
        id: userId,
        name: userName
      },
      customization: {
        autosave: true,
        forcesave: true,
        chat: true,
        comments: true
      }
    },
    events: {
      onAppReady: () => {
        console.log('Editor ready');
        onReady?.();
      },
      onDocumentReady: () => {
        console.log('Document loaded');
      },
      onDocumentStateChange: (state: boolean) => {
        if (state) {
          console.log('Document modified');
        }
      },
      onError: (error: any) => {
        console.error('Editor error:', error);
      }
    }
  };

  return (
    <div style={{ width: '100%', height: '100%' }}>
      <DocumentEditor
        id="onlyoffice-editor"
        documentServerUrl={process.env.REACT_APP_ONLYOFFICE_URL}
        config={config}
        ref={editorRef}
      />
    </div>
  );
};
```

#### 在共创页面中使用

```tsx
// pages/CollaborativeCreation.tsx
import React from 'react';
import { OnlyOfficeEditor } from '../components/OnlyOfficeEditor';
import { CollaborationPanel } from '../components/CollaborationPanel';
import { AiAssistantPanel } from '../components/AiAssistantPanel';

export const CollaborativeCreation: React.FC = () => {
  const [documentUrl, setDocumentUrl] = React.useState('');
  const [documentKey] = React.useState(() => `doc-${Date.now()}`);
  const [showAiPanel, setShowAiPanel] = React.useState(false);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* 顶部导航栏 */}
      <Header>
        <h1>OpenCopilot 共创模式</h1>
        <Actions>
          <Button onClick={() => setShowAiPanel(true)}>🤖 AI 助手</Button>
          <Button>💾 保存</Button>
          <Button>📤 导出</Button>
        </Actions>
      </Header>

      {/* 协作状态栏 */}
      <CollaborationPanel />

      {/* 主编辑区域 */}
      <div style={{ flex: 1, position: 'relative' }}>
        <OnlyOfficeEditor
          documentUrl={documentUrl}
          documentKey={documentKey}
          userName="当前用户"
          userId="user-123"
          onSave={(url) => console.log('Saved:', url)}
        />

        {/* AI 助手面板（侧边栏） */}
        {showAiPanel && (
          <AiAssistantPanel onClose={() => setShowAiPanel(false)} />
        )}
      </div>
    </div>
  );
};
```

---

## 三、与共创页面融合设计

### 3.1 界面布局建议

```
┌─────────────────────────────────────────────────────────┐
│  📊 OpenCopilot 共创模式    [在线: 3人]  💾 保存  🤖 AI │  ← 顶部导航栏
├─────────────────────────────────────────────────────────┤
│  👤 张三  👤 李四  👤 你  |  文档自动保存中...          │  ← 协作状态栏
├─────────────────────────────────────────────────────────┤
│          │                                              │
│  🎨 左侧  │    ONLYOFFICE 编辑器（主画布）               │
│  工具栏   │                                              │
│          │                                              │
│  • 插入  │    ┌──────────────────────────────┐          │
│  • 布局  │    │                              │          │
│  • 主题  │    │    PPT 编辑区域               │          │
│  • AI    │    │                              │          │
│          │    └──────────────────────────────┘          │
│          │                                              │
├──────────┼──────────────────────────────────────────────┤
│          │  🤖 AI 助手面板（可折叠）                      │  ← 可选：底部/侧边栏
│          │  • 生成幻灯片                                  │
│          │  • 优化文案                                    │
│          │  • 推荐模板                                    │
└──────────┴──────────────────────────────────────────────┘
```

### 3.2 关键交互设计

#### 设计 1：AI 辅助编辑触发方式

**方案 A：工具栏按钮**
```tsx
// 在 ONLYOFFICE 工具栏中添加自定义按钮
const config = {
  editorConfig: {
    customization: {
      features: {
        // 插入自定义按钮
        custom: [
          {
            name: 'ai_generate',
            tooltip: 'AI 生成幻灯片',
            icon: 'https://your-domain.com/ai-icon.png',
            callback: () => openAiPanel()
          }
        ]
      }
    }
  }
};
```

**方案 B：侧边栏面板（推荐）**
```tsx
// 从编辑器外部触发 AI 功能
const handleAiGenerate = () => {
  // 1. 获取当前选中内容
  const selection = editorRef.current.getSelectedText();
  
  // 2. 调用 AI 生成 API
  const generatedContent = await callAiApi({
    prompt: `基于以下内容生成幻灯片：${selection}`
  });
  
  // 3. 将生成的内容插入编辑器
  editorRef.current.insertText(generatedContent);
};
```

#### 设计 2：协作状态展示

```tsx
// components/CollaborationPanel.tsx
import React from 'react';

export const CollaborationPanel: React.FC = () => {
  const [onlineUsers, setOnlineUsers] = React.useState([
    { id: '1', name: '张三', avatar: '张', color: '#667eea' },
    { id: '2', name: '李四', avatar: '李', color: '#f093fb' },
    { id: '3', name: '你', avatar: '你', color: '#4facfe' }
  ]);

  return (
    <div style={styles.container}>
      <div style={styles.users}>
        <span style={styles.label}>在线协作：</span>
        {onlineUsers.map(user => (
          <div
            key={user.id}
            style={{
              ...styles.avatar,
              background: user.color
            }}
            title={user.name}
          >
            {user.avatar}
          </div>
        ))}
        <div style={styles.status}>
          <div style={styles.greenDot} />
          {onlineUsers.length} 人在线
        </div>
      </div>
      <div style={styles.saveStatus}>
        文档自动保存中...
      </div>
    </div>
  );
};

const styles = {
  container: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '12px 24px',
    background: white,
    borderBottom: '1px solid #e8e8e8'
  },
  users: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px'
  },
  avatar: {
    width: '32px',
    height: '32px',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: 'white',
    fontWeight: 600
  },
  status: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '13px',
    color: '#52c41a'
  },
  greenDot: {
    width: '8px',
    height: '8px',
    background: '#52c41a',
    borderRadius: '50%',
    animation: 'pulse 2s infinite'
  }
};
```

#### 设计 3：实时协作冲突处理

ONLYOFFICE 内置了 Operational Transformation (OT) 算法，自动处理协作冲突。但你可能需要：

```tsx
// 监听文档状态变化
const config = {
  events: {
    onDocumentStateChange: (state: boolean) => {
      if (state) {
        // 文档有未保存的更改
        showNotification('文档已修改，正在自动保存...');
      } else {
        // 文档已保存
        showNotification('✅ 保存成功');
      }
    }
  }
};
```

---

## 四、API 调用示例

### 4.1 基础操作

```typescript
// 获取编辑器实例
const editor = docEditor;

// 1. 下载文档
editor.downloadAs('pptx');

// 2. 打印文档
editor.print();

// 3. 获取文档 URL
const url = editor.getDocumentUrl();
console.log('Document URL:', url);

// 4. 获取当前用户信息
const userInfo = editor.getUserInfo();
console.log('Current user:', userInfo);

// 5. 设置文档标题
editor.setDocumentTitle('新的演示文稿.pptx');
```

### 4.2 高级功能

```typescript
// 1. 插入文本（AI 生成内容）
editor.insertText('这是 AI 生成的内容');

// 2. 插入图片
editor.insertImage({
  url: 'https://example.com/image.png',
  width: 300,
  height: 200
});

// 3. 执行命令
editor.executeCommand('fontName', 'Arial');
editor.executeCommand('fontSize', 24);

// 4. 显示/隐藏工具栏
editor.showToolbar();
editor.hideToolbar();

// 5. 强制保存
editor.forcesave();
```

### 4.3 事件监听

```typescript
const config = {
  events: {
    // 应用准备就绪
    onAppReady: () => {
      console.log('App ready');
    },

    // 文档加载完成
    onDocumentReady: () => {
      console.log('Document ready');
    },

    // 文档状态变化（是否有未保存的更改）
    onDocumentStateChange: (state: boolean) => {
      console.log('Document state:', state);
    },

    // 用户开始编辑
    onRequestEditRights: () => {
      console.log('User requested edit rights');
    },

    // 用户关闭编辑器
    onRequestClose: () => {
      console.log('User closed editor');
    },

    // 错误事件
    onError: (error: any) => {
      console.error('Error:', error.data.errorCode, error.data.errorDescription);
    },

    // 警告事件
    onWarning: (warning: any) => {
      console.warn('Warning:', warning.data.warningDescription);
    },

    // 信息事件
    onInfo: (info: any) => {
      console.info('Info:', info.data);
    }
  }
};
```

---

## 五、部署与配置

### 5.1 Docker 部署（推荐）

```yaml
# docker-compose.yml
version: '3'
services:
  onlyoffice:
    image: onlyoffice/documentserver
    container_name: onlyoffice
    ports:
      - "8080:80"
    volumes:
      - ./data:/var/www/onlyoffice/Data
      - ./logs:/var/log/onlyoffice
      - ./lib:/var/lib/onlyoffice
    environment:
      - JWT_ENABLED=true
      - JWT_SECRET=your-secret-key-here
    restart: always
```

启动服务：
```bash
docker-compose up -d
```

### 5.2 环境变量配置

```bash
# .env
REACT_APP_ONLYOFFICE_URL=http://localhost:8080
REACT_APP_ONLYOFFICE_JWT_SECRET=your-secret-key-here
```

### 5.3 Nginx 反向代理配置（生产环境）

```nginx
server {
    listen 80;
    server_name documentserver.your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name documentserver.your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## 六、常见问题与解决方案

### Q1：ONLYOFFICE 编辑器无法加载

**可能原因**：
1. Document Server URL 配置错误
2. 跨域问题（CORS）
3. 网络无法访问 Document Server

**解决方案**：
```javascript
// 1. 检查 Document Server 是否可访问
fetch('http://localhost:8080/healthcheck')
  .then(res => res.json())
  .then(data => console.log('Health check:', data));

// 2. 配置 CORS（在 Document Server 上）
# 编辑 /etc/onlyoffice/documentserver/nginx/onlyoffice-documentserver.conf
add_header Access-Control-Allow-Origin *;
add_header Access-Control-Allow-Methods GET,POST,OPTIONS;
```

### Q2：如何集成 AI 生成功能？

**方案**：通过 ONLYOFFICE 的插件机制或外部调用

```typescript
// 外部调用示例
const handleAiGenerate = async (prompt: string) => {
  // 1. 调用 AI API
  const response = await fetch('/api/ai/generate-slide', {
    method: 'POST',
    body: JSON.stringify({ prompt })
  });
  const slideData = await response.json();
  
  // 2. 将生成的内容转换为 PPTX
  // 3. 重新加载编辑器
  const newDocumentUrl = await uploadToStorage(slideData);
  reloadEditor(newDocumentUrl);
};
```

### Q3：如何实现多人协作？

**ONLYOFFICE 内置协作功能**，只需确保：
1. 所有用户访问同一个 `documentKey`
2. Document Server 配置正确
3. 网络连接稳定

```javascript
const config = {
  document: {
    key: 'unique-document-key',  // 关键：同一个 key 实现协作
    url: 'https://your-storage.com/document.pptx'
  },
  editorConfig: {
    user: {
      id: 'unique-user-id',  // 每个用户必须有唯一 ID
      name: 'User Name'
    }
  }
};
```

### Q4：如何自定义工具栏？

```javascript
const config = {
  editorConfig: {
    customization: {
      toolbar: true,          // 显示工具栏
      statusBar: true,         // 显示状态栏
      compactToolbar: false,   // 不使用紧凑模式
      features: {
        // 隐藏某些功能
        comments: false,      // 隐藏评论
        chat: false,           // 隐藏聊天
        help: false,           // 隐藏帮助
        // 自定义按钮
        custom: [
          {
            name: 'customButton',
            tooltip: '自定义按钮',
            icon: 'icon-url',
            callback: () => console.log('Clicked')
          }
        ]
      }
    }
  }
};
```

---

## 七、下一步行动

### 7.1 技术验证（第 1 周）

- [ ] 部署 ONLYOFFICE Document Server（Docker）
- [ ] 运行提供的 `onlyoffice-demo.html` 验证基础功能
- [ ] 测试 .pptx 导入导出
- [ ] 验证多人协作功能

### 7.2 集成开发（第 2-3 周）

- [ ] 封装 React 组件 `OnlyOfficeEditor`
- [ ] 实现与 OpenCopilot 的前端路由集成
- [ ] 开发协作状态面板
- [ ] 集成 AI 助手功能

### 7.3 测试与优化（第 4 周）

- [ ] 功能测试（编辑、保存、导出、协作）
- [ ] 性能测试（大文档加载速度、协作延迟）
- [ ] 安全测试（JWT 验证、文件权限）
- [ ] 用户体验优化（加载动画、错误提示）

---

## 八、参考资料

- **官方文档**：https://api.onlyoffice.com/docs/docs-api/
- **React 组件**：https://www.npmjs.com/package/@onlyoffice/document-editor-react
- **Docker 部署**：https://github.com/ONLYOFFICE/Docker-DocumentServer
- **在线演示**：https://www.onlyoffice.com/demo.aspx
- **API 参考**：https://api.onlyoffice.com/zh/editors/basic

---

## 附录：完整示例代码

### A. 验证 Demo（已创建）

文件位置：`onlyoffice-demo.html`

使用方法：
1. 直接用浏览器打开
2. 确保网络可以访问 `documentserver.onlyoffice.com`
3. 查看事件日志，确认 API 正常工作

### B. React 完整集成示例

```tsx
// App.tsx
import React, { useState } from 'react';
import { DocumentEditor } from '@onlyoffice/document-editor-react';

function App() {
  const [editorReady, setEditorReady] = useState(false);

  const config = {
    document: {
      fileType: 'pptx',
      key: `doc-${Date.now()}`,
      title: '共创演示文稿.pptx',
      url: 'https://your-storage.com/document.pptx'
    },
    documentType: 'slide',
    editorConfig: {
      mode: 'edit',
      lang: 'zh-CN',
      user: {
        id: 'user-' + Math.random().toString(36).substr(2, 9),
        name: '测试用户'
      }
    },
    events: {
      onAppReady: () => setEditorReady(true),
      onError: (error) => console.error(error)
    }
  };

  return (
    <div style={{ width: '100vw', height: '100vh' }}>
      {!editorReady && <div>加载中...</div>}
      <DocumentEditor
        id="editor"
        documentServerUrl={process.env.REACT_APP_ONLYOFFICE_URL}
        config={config}
      />
    </div>
  );
}

export default App;
```

---

**文档结束**

如有疑问，请联系：WorkBuddy
最后更新：2026-06-13
