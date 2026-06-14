from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, List, Any, Optional
import sys
import os
import asyncio
import time
import signal

import sys
import os

# 将 asu_broker 的父目录添加到 sys.path 中，以便允许绝对导入 asu_broker.probes 等模块

from opencopilot.broker.core.auth import verify_token
from opencopilot.broker.probes.browser_probe import get_browser_tabs, get_active_tab_dom
from opencopilot.broker.probes.window_probe import get_frontmost_app
from opencopilot.broker.probes.selection_probe import get_clipboard_content, set_clipboard_content, get_selected_text, replace_selected_text
from opencopilot.broker.probes.app_control_probe import get_notes_content, create_note
from opencopilot.broker.probes.screen_probe import capture_front_window
from opencopilot.broker.probes.fs_probe import read_file_as_context
from opencopilot.broker.probes.office_probe import read_office_file
from opencopilot.broker.probes.events_probe import start_events_probe

# ============================================================
# 默认超时配置（秒）
# ============================================================
TIMEOUT_BROWSER_DOM = 8.0      # DOM 提取可能因页面复杂度而异
TIMEOUT_BROWSER_TABS = 5.0     # 标签页枚举通常较快
TIMEOUT_FRONT_WINDOW = 3.0     # 截图是 I/O 操作
TIMEOUT_CLIPBOARD = 3.0        # pbpaste/pbcopy 很快
TIMEOUT_SELECTION = 4.0        # 模拟 Cmd+C 需要等待剪贴板更新
TIMEOUT_NOTES = 5.0            # 备忘录创建涉及 AppleScript
TIMEOUT_FRONT_APP = 3.0        # 前台应用探针轻量
TIMEOUT_FILE_READ = 5.0        # 文件 I/O
TIMEOUT_OFFICE_READ = 15.0     # Office 文档解析（含解压+遍历）
TIMEOUT_DEFAULT = 10.0         # 通用回退超时

# ============================================================
# FastAPI 应用实例
# ============================================================
app = FastAPI(
    title="ASU Privileged Broker",
    description="ASU 的高权限本地代理服务，用于沙盒穿透和跨进程系统探测。",
    version="1.1.0"
)

# ============================================================
# WebSocket 事件推送架构
# ============================================================
events_queue = asyncio.Queue()
active_websockets = set()

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_running_loop()
    # 启动 macOS 系统事件监听探针
    start_events_probe(loop, events_queue)
    # 启动 WebSocket 广播消费协程
    asyncio.create_task(broadcast_events())

async def broadcast_events():
    """将从探针收集到的事件广播给所有连接的客户端"""
    while True:
        event = await events_queue.get()
        dead_ws = set()
        for ws in active_websockets:
            try:
                await ws.send_json(event)
            except Exception:
                dead_ws.add(ws)
        # 移除已断开的连接
        for ws in dead_ws:
            active_websockets.remove(ws)

@app.websocket("/api/v1/events")
async def websocket_events(websocket: WebSocket):
    """Client (如 ASU) 连接此 WebSocket 接收实时系统事件"""
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        while True:
            # 保持连接活跃
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
    except Exception as e:
        print(f"[Broker] WebSocket error: {e}")
        if websocket in active_websockets:
            active_websockets.remove(websocket)

@app.get("/api/v1/system/screen/front", dependencies=[Depends(verify_token)])
async def api_get_front_window_screenshot():
    """获取当前前台窗口的截图 (Base64)"""
    try:
        base64_image = await asyncio.wait_for(capture_front_window(), timeout=TIMEOUT_FRONT_WINDOW)
        return {
            "status": "success",
            "data": {
                "format": "base64_png",
                "image": base64_image
            }
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="截图超时，可能缺少屏幕录制权限。")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取窗口截图失败: {str(e)}")

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """将 HTTPException 包装为统一 JSON 错误结构"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": f"BROKER_HTTP_{exc.status_code}",
            "message": str(exc.detail)
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """兜底异常处理器，防止 500 HTML 页面返回"""
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": "BROKER_INTERNAL_ERROR",
            "message": str(exc)
        }
    )


# ============================================================
# 请求日志中间件
# ============================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录每个请求的方法、路径、响应状态和耗时"""
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    if request.url.path != "/health":
        print(f"[Broker] {request.method} {request.url.path} → {response.status_code} ({duration:.2f}s)")
    return response


# ============================================================
# 路由
# ============================================================

# ---------- 能力发现 ----------

@app.get("/api/v1/system/capabilities", dependencies=[Depends(verify_token)])
async def api_get_capabilities():
    """返回 Broker 当前拥有的全部能力清单，方便 ASU 动态适配"""
    return {
        "status": "success",
        "data": {
            "version": "1.1.0",
            "broker": {
                "port": 18889,
                "auth": "bearer_token",
                "token_file": os.path.expanduser("~/.asu_broker_token")
            },
            "capabilities": {
                "browser": {
                    "dom_extraction": {
                        "supported": True,
                        "browsers": ["Google Chrome", "Safari", "Brave Browser", "Microsoft Edge", "Arc"],
                        "method": "applescript_javascript_injection",
                        "endpoint": "POST /api/v1/browser/dom"
                    },
                    "tabs_listing": {
                        "supported": True,
                        "browsers": ["Google Chrome"],
                        "endpoint": "GET /api/v1/browser/tabs"
                    }
                },
                "system": {
                    "frontmost_app": {
                        "supported": True,
                        "method": "applescript_system_events",
                        "endpoint": "GET /api/v1/system/frontmost",
                        "returns": ["app_name"]
                    },
                    "clipboard_read": {
                        "supported": True,
                        "method": "pbpaste",
                        "endpoint": "GET /api/v1/system/clipboard"
                    },
                    "clipboard_write": {
                        "supported": True,
                        "method": "pbcopy",
                        "endpoint": "POST /api/v1/system/clipboard"
                    },
                    "selection_read": {
                        "supported": True,
                        "method": "axuielement_with_applescript_fallback",
                        "endpoint": "GET /api/v1/system/selection",
                        "note": "优先使用 AXUIElement 无感读取，失败则降级到 Cmd+C"
                    },
                    "screenshot_front_window": {
                        "supported": True,
                        "format": "base64_png",
                        "endpoint": "GET /api/v1/system/screen/front"
                    },
                    "shutdown": {
                        "supported": True,
                        "endpoint": "POST /api/v1/system/shutdown"
                    }
                },
                "filesystem": {
                    "file_read": {
                        "supported": True,
                        "method": "python_open",
                        "endpoint": "POST /api/v1/system/fs/read"
                    },
                    "office_read": {
                        "supported": True,
                        "formats": ["docx", "pptx"],
                        "method": "python-docx / python-pptx",
                        "endpoint": "POST /api/v1/system/fs/office/read"
                    }
                },
                "apps": {
                    "notes_create": {
                        "supported": True,
                        "method": "applescript_notes",
                        "endpoint": "POST /api/v1/apps/notes"
                    }
                },
                "realtime": {
                    "websocket_events": {
                        "supported": True,
                        "endpoint": "ws://[host]:[port]/api/v1/events",
                        "description": "前台应用切换等事件主动推送"
                    }
                }
            }
        }
    }


# ---------- 健康检查 ----------

@app.get("/health", dependencies=[Depends(verify_token)])
async def health_check():
    """健康检查，同时验证探针是否真正可用"""
    probes_ok = False
    try:
        app_name = await asyncio.wait_for(get_frontmost_app(), timeout=TIMEOUT_FRONT_APP)
        probes_ok = bool(app_name)
    except Exception:
        probes_ok = False

    return {
        "status": "ok",
        "message": "Privileged Broker is running.",
        "probes_available": probes_ok,
        "version": "1.1.0"
    }


# ---------- 前台应用探测 ----------

@app.get("/api/v1/system/frontmost", dependencies=[Depends(verify_token)])
async def api_get_frontmost():
    try:
        app_name = await asyncio.wait_for(get_frontmost_app(), timeout=TIMEOUT_FRONT_APP)
        return {"status": "success", "data": {"app_name": app_name}}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe frontmost_app timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 剪贴板读写 ----------

@app.get("/api/v1/system/clipboard", dependencies=[Depends(verify_token)])
async def api_get_clipboard():
    try:
        content = await asyncio.wait_for(get_clipboard_content(), timeout=TIMEOUT_CLIPBOARD)
        return {"status": "success", "data": {"content": content}}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe clipboard_read timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ClipboardRequest(BaseModel):
    content: str


@app.post("/api/v1/system/clipboard", dependencies=[Depends(verify_token)])
async def api_set_clipboard(req: ClipboardRequest):
    try:
        success = await asyncio.wait_for(set_clipboard_content(req.content), timeout=TIMEOUT_CLIPBOARD)
        return {"status": "success" if success else "error"}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe clipboard_write timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 选区提取 ----------

@app.get("/api/v1/system/selection", dependencies=[Depends(verify_token)])
async def api_get_selection():
    """获取当前用户在任意应用中选中的文本"""
    try:
        content = await asyncio.wait_for(get_selected_text(), timeout=TIMEOUT_SELECTION)
        return {
            "status": "success",
            "data": {
                "content": content
            }
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="获取选中内容超时，可能目标应用卡死或不支持无感读取。")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取选中内容失败: {str(e)}")


class ReplaceSelectionRequest(BaseModel):
    text: str


@app.post("/api/v1/system/selection/replace", dependencies=[Depends(verify_token)])
async def api_replace_selection(req: ReplaceSelectionRequest):
    """替换当前用户在任意应用中选中的文本"""
    try:
        success = await asyncio.wait_for(
            replace_selected_text(req.text), timeout=TIMEOUT_SELECTION
        )
        return {
            "status": "success" if success else "error",
            "data": {"replaced": success}
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="替换选中内容超时")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"替换选中内容失败: {str(e)}")


# ---------- 备忘录操作 ----------

class NoteRequest(BaseModel):
    title: str
    body: str


@app.post("/api/v1/apps/notes", dependencies=[Depends(verify_token)])
async def api_create_note(req: NoteRequest):
    try:
        success = await asyncio.wait_for(create_note(req.title, req.body), timeout=TIMEOUT_NOTES)
        return {"status": "success" if success else "error"}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe notes_create timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 屏幕截图 ----------

@app.get("/api/v1/system/screen/front", dependencies=[Depends(verify_token)])
async def api_capture_front_window():
    try:
        b64_image = await asyncio.wait_for(capture_front_window(), timeout=TIMEOUT_FRONT_WINDOW)
        return {"status": "success", "data": {"image_base64": b64_image}}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe screenshot timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 文件系统读取 ----------

class FileRequest(BaseModel):
    file_path: str


@app.post("/api/v1/system/fs/read", dependencies=[Depends(verify_token)])
async def api_read_file(req: FileRequest):
    try:
        content = await asyncio.wait_for(read_file_as_context(req.file_path), timeout=TIMEOUT_FILE_READ)
        return {"status": "success", "data": {"content": content}}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe file_read timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Office 文档解析（.docx / .pptx）----------

@app.post("/api/v1/system/fs/office/read", dependencies=[Depends(verify_token)])
async def api_read_office_file(req: FileRequest):
    """解析 .docx 或 .pptx 文件，提取纯文本内容供全文修订等场景使用。"""
    try:
        result = await asyncio.wait_for(read_office_file(req.file_path), timeout=TIMEOUT_OFFICE_READ)
        return {"status": "success", "data": result}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe office_read timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 浏览器标签页 ----------

@app.get("/api/v1/browser/tabs", dependencies=[Depends(verify_token)])
async def api_get_browser_tabs():
    try:
        tabs_info = await asyncio.wait_for(get_browser_tabs(), timeout=TIMEOUT_BROWSER_TABS)
        return {"status": "success", "data": {"tabs": tabs_info}}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe browser_tabs timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 浏览器 DOM 提取 ----------

class DOMRequest(BaseModel):
    browser_name: str


@app.post("/api/v1/browser/dom", dependencies=[Depends(verify_token)])
async def api_get_browser_dom(req: DOMRequest):
    try:
        content = await asyncio.wait_for(get_active_tab_dom(req.browser_name), timeout=TIMEOUT_BROWSER_DOM)
        return {"status": "success", "data": {"content": content}}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Probe browser_dom timed out")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 优雅关闭 ----------

@app.post("/api/v1/system/shutdown", dependencies=[Depends(verify_token)])
async def api_shutdown(background_tasks: BackgroundTasks):
    """优雅关闭 Broker 进程"""
    async def _graceful_shutdown():
        await asyncio.sleep(0.3)  # 确保 HTTP 响应已发出
        print("\n[Broker] 收到 shutdown 请求，正在优雅关闭...")
        os.kill(os.getpid(), signal.SIGTERM)

    background_tasks.add_task(_graceful_shutdown)
    return {"status": "success", "message": "Broker is shutting down gracefully"}


# ============================================================
# 权限诊断接口
# ============================================================

def check_accessibility_permission() -> Dict[str, Any]:
    """
    检查辅助功能权限
    
    Returns:
        权限状态信息
    """
    try:
        # 尝试导入 PyObjC
        try:
            import ApplicationServices
            has_pyobjc = True
        except ImportError:
            has_pyobjc = False
        
        if not has_pyobjc:
            return {
                "available": False,
                "granted": False,
                "error": "PyObjC 未安装，无法检测辅助功能权限"
            }
        
        # 检查辅助功能权限
        granted = ApplicationServices.AXIsProcessTrusted()
        
        return {
            "available": True,
            "granted": granted,
            "description": "辅助功能权限用于读取高亮文本和 UI 元素",
            "impact": "无感划词、UI 元素读取" if granted else "无法使用无感划词功能"
        }
    except Exception as e:
        return {
            "available": False,
            "granted": False,
            "error": str(e)
        }


def check_screen_recording_permission() -> Dict[str, Any]:
    """
    检查屏幕录制权限
    
    Returns:
        权限状态信息
    """
    try:
        # 尝试导入 PyObjC
        try:
            from AppKit import NSWorkspace
            has_pyobjc = True
        except ImportError:
            has_pyobjc = False
        
        if not has_pyobjc:
            return {
                "available": False,
                "granted": False,
                "error": "PyObjC 未安装，无法检测屏幕录制权限"
            }
        
        # 尝试截图来检测屏幕录制权限
        try:
            from PIL import ImageGrab
            # 尝试截图
            screenshot = ImageGrab.grab()
            granted = screenshot is not None
        except Exception:
            granted = False
        
        return {
            "available": True,
            "granted": granted,
            "description": "屏幕录制权限用于截取屏幕截图",
            "impact": "窗口截图、屏幕录制" if granted else "无法截取屏幕截图"
        }
    except Exception as e:
        return {
            "available": False,
            "granted": False,
            "error": str(e)
        }


def check_automation_permission() -> Dict[str, Any]:
    """
    检查自动化权限
    
    Returns:
        权限状态信息
    """
    try:
        # 尝试导入 subprocess
        import subprocess
        
        # 尝试执行简单的 AppleScript 来检测自动化权限
        try:
            result = subprocess.run(
                ['osascript', '-e', 'tell application "System Events" to get name of first process'],
                capture_output=True,
                text=True,
                timeout=5
            )
            granted = result.returncode == 0
        except subprocess.TimeoutExpired:
            granted = False
        except Exception:
            granted = False
        
        return {
            "available": True,
            "granted": granted,
            "description": "自动化权限用于执行 AppleScript 控制其他应用",
            "impact": "浏览器控制、备忘录操作、系统事件监听" if granted else "无法控制其他应用"
        }
    except Exception as e:
        return {
            "available": False,
            "granted": False,
            "error": str(e)
        }


def check_full_disk_access() -> Dict[str, Any]:
    """
    检查完全磁盘访问权限
    
    Returns:
        权限状态信息
    """
    try:
        import os
        
        # 尝试读取受保护的目录来检测完全磁盘访问权限
        test_paths = [
            os.path.expanduser("~/Library/Mail"),
            os.path.expanduser("~/Library/Safari"),
            os.path.expanduser("~/Library/Calendars")
        ]
        
        accessible_paths = []
        for path in test_paths:
            if os.path.exists(path):
                try:
                    os.listdir(path)
                    accessible_paths.append(path)
                except PermissionError:
                    pass
        
        granted = len(accessible_paths) > 0
        
        return {
            "available": True,
            "granted": granted,
            "accessible_paths": accessible_paths,
            "description": "完全磁盘访问权限用于读取系统保护目录",
            "impact": "读取邮件、Safari 数据、日历等" if granted else "无法访问系统保护目录"
        }
    except Exception as e:
        return {
            "available": False,
            "granted": False,
            "error": str(e)
        }


@app.get("/api/v1/system/permissions", dependencies=[Depends(verify_token)])
async def api_check_permissions():
    """
    权限诊断接口
    
    检查 Broker 运行所需的各种系统权限状态。
    """
    try:
        permissions = {
            "accessibility": check_accessibility_permission(),
            "screen_recording": check_screen_recording_permission(),
            "automation": check_automation_permission(),
            "full_disk_access": check_full_disk_access()
        }
        
        # 计算总体状态
        granted_count = sum(1 for p in permissions.values() if p.get("granted", False))
        total_count = len(permissions)
        
        # 确定哪些权限是必需的
        required_permissions = ["accessibility", "automation"]
        missing_required = [
            name for name in required_permissions 
            if not permissions[name].get("granted", False)
        ]
        
        # 确定功能影响
        feature_impact = []
        if not permissions["accessibility"].get("granted", False):
            feature_impact.append("无感划词功能不可用")
        if not permissions["screen_recording"].get("granted", False):
            feature_impact.append("屏幕截图功能不可用")
        if not permissions["automation"].get("granted", False):
            feature_impact.append("浏览器控制、备忘录操作不可用")
        if not permissions["full_disk_access"].get("granted", False):
            feature_impact.append("系统保护目录不可访问")
        
        return {
            "status": "success",
            "data": {
                "permissions": permissions,
                "summary": {
                    "granted_count": granted_count,
                    "total_count": total_count,
                    "missing_required": missing_required,
                    "feature_impact": feature_impact,
                    "overall_status": "ok" if not missing_required else "partial"
                },
                "recommendations": generate_permission_recommendations(permissions)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"权限诊断失败: {str(e)}")


def generate_permission_recommendations(permissions: Dict[str, Any]) -> List[str]:
    """
    生成权限建议
    
    Args:
        permissions: 权限状态字典
        
    Returns:
        建议列表
    """
    recommendations = []
    
    if not permissions["accessibility"].get("granted", False):
        recommendations.append(
            "请在 系统设置 > 隐私与安全性 > 辅助功能 中添加 Broker 应用"
        )
    
    if not permissions["screen_recording"].get("granted", False):
        recommendations.append(
            "请在 系统设置 > 隐私与安全性 > 屏幕录制 中添加 Broker 应用"
        )
    
    if not permissions["automation"].get("granted", False):
        recommendations.append(
            "请在 系统设置 > 隐私与安全性 > 自动化 中允许 Broker 控制其他应用"
        )
    
    if not permissions["full_disk_access"].get("granted", False):
        recommendations.append(
            "如需访问系统保护目录，请在 系统设置 > 隐私与安全性 > 完全磁盘访问 中添加 Broker 应用"
        )
    
    if not recommendations:
        recommendations.append("所有权限已正确配置，Broker 功能完整可用")
    
    return recommendations


@app.get("/api/v1/system/permissions/guide", dependencies=[Depends(verify_token)])
async def api_permission_guide():
    """
    权限引导接口
    
    获取权限配置的详细指南。
    """
    try:
        guide = {
            "accessibility": {
                "name": "辅助功能权限",
                "description": "允许 Broker 读取其他应用的 UI 元素和高亮文本",
                "steps": [
                    "打开 系统设置",
                    "进入 隐私与安全性 > 辅助功能",
                    "点击左下角的锁图标解锁",
                    "点击 + 按钮添加 Broker 应用",
                    "确保 Broker 已被勾选"
                ],
                "required_for": ["无感划词", "UI 元素读取", "高亮文本提取"]
            },
            "screen_recording": {
                "name": "屏幕录制权限",
                "description": "允许 Broker 截取屏幕截图",
                "steps": [
                    "打开 系统设置",
                    "进入 隐私与安全性 > 屏幕录制",
                    "点击左下角的锁图标解锁",
                    "点击 + 按钮添加 Broker 应用",
                    "确保 Broker 已被勾选"
                ],
                "required_for": ["窗口截图", "屏幕录制"]
            },
            "automation": {
                "name": "自动化权限",
                "description": "允许 Broker 通过 AppleScript 控制其他应用",
                "steps": [
                    "打开 系统设置",
                    "进入 隐私与安全性 > 自动化",
                    "找到 Broker 应用",
                    "勾选需要控制的应用（如浏览器、备忘录等）"
                ],
                "required_for": ["浏览器控制", "备忘录操作", "系统事件监听"]
            },
            "full_disk_access": {
                "name": "完全磁盘访问权限",
                "description": "允许 Broker 访问系统保护目录",
                "steps": [
                    "打开 系统设置",
                    "进入 隐私与安全性 > 完全磁盘访问",
                    "点击左下角的锁图标解锁",
                    "点击 + 按钮添加 Broker 应用",
                    "确保 Broker 已被勾选"
                ],
                "required_for": ["读取邮件数据", "Safari 数据", "日历数据"]
            }
        }
        
        return {
            "status": "success",
            "data": {
                "guide": guide,
                "notes": [
                    "权限更改后可能需要重启 Broker 才能生效",
                    "某些权限可能需要管理员密码才能修改",
                    "如果权限被拒绝，相关功能将无法使用"
                ]
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取权限指南失败: {str(e)}")
