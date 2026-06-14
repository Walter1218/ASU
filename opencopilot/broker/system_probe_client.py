import os
import httpx
import json

BROKER_URL = "http://127.0.0.1:18889"
TOKEN_FILE = os.path.expanduser("~/.asu_broker_token")

class SystemProbeClient:
    """ASU 主程序用于与 Privileged Broker 通信的 HTTP 客户端。"""
    
    def __init__(self):
        self._token = self._load_token()
        self.headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _load_token(self):
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, 'r') as f:
                    return f.read().strip()
        except Exception:
            pass
        return ""

    def is_broker_alive(self) -> bool:
        """检查 Broker 是否在运行并且 Token 有效。"""
        if not self._token:
            return False
        try:
            # timeout 设短一点，用于探活
            resp = httpx.get(f"{BROKER_URL}/health", headers=self.headers, timeout=0.5)
            return resp.status_code == 200
        except Exception:
            return False

    def get_frontmost_app(self) -> str:
        """获取当前前台应用名称。如果失败或 Broker 未运行则返回空字符串。"""
        try:
            resp = httpx.get(f"{BROKER_URL}/api/v1/system/frontmost", headers=self.headers, timeout=2.0)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("app_name", "")
        except Exception:
            pass
        return ""

    def get_clipboard(self) -> str:
        """[预埋] 获取系统剪贴板内容"""
        try:
            resp = httpx.get(f"{BROKER_URL}/api/v1/system/clipboard", headers=self.headers, timeout=2.0)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("content", "")
        except Exception:
            pass
        return ""

    def get_selection(self) -> str:
        """[预埋] 获取当前高亮的选区内容"""
        try:
            resp = httpx.get(f"{BROKER_URL}/api/v1/system/selection", headers=self.headers, timeout=3.0)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("content", "")
        except Exception:
            pass

    def replace_selection(self, text: str) -> bool:
        """替换当前选中的文本（通过 AX API 或剪贴板降级）"""
        try:
            resp = httpx.post(
                f"{BROKER_URL}/api/v1/system/selection/replace",
                json={"text": text},
                headers=self.headers,
                timeout=5.0
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("replaced", False)
        except Exception:
            pass
        return False
            
    def get_front_window_screenshot(self) -> str:
        """获取前台窗口截图的 Base64 字符串"""
        try:
            resp = httpx.get(f"{BROKER_URL}/api/v1/system/screen/front", headers=self.headers, timeout=5.0)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("image", "")
            else:
                err_detail = resp.json().get("detail", "Unknown error")
                raise Exception(f"Broker 返回错误 ({resp.status_code}): {err_detail}")
        except httpx.ReadTimeout:
            raise Exception("截图请求超时，请检查是否已在系统设置中授予屏幕录制权限。")
        except Exception as e:
            raise Exception(f"请求 Broker 失败: {str(e)}")

    def get_browser_dom(self, browser_name: str) -> str:
        """获取指定浏览器的当前标签页全文。"""
        try:
            payload = {"browser_name": browser_name}
            # 读取 DOM 可能较慢，超时设长一点
            resp = httpx.post(f"{BROKER_URL}/api/v1/browser/dom", json=payload, headers=self.headers, timeout=10.0)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("content", "")
            else:
                err_detail = resp.json().get("detail", "Unknown error")
                raise Exception(f"Broker 返回错误 ({resp.status_code}): {err_detail}")
        except httpx.ReadTimeout:
            raise Exception("读取浏览器内容超时，请检查浏览器是否无响应或存在权限弹窗。")
        except Exception as e:
            raise Exception(f"请求 Broker 失败: {str(e)}")

    def read_office_file(self, file_path: str) -> dict:
        """解析 .docx 或 .pptx 文件为纯文本。返回 {"type","content","...}。"""
        try:
            payload = {"file_path": file_path}
            resp = httpx.post(f"{BROKER_URL}/api/v1/system/fs/office/read",
                              json=payload, headers=self.headers, timeout=15.0)
            if resp.status_code == 200:
                return resp.json().get("data", {})
            else:
                detail = resp.json().get("message", resp.json().get("detail", "Unknown"))
                raise Exception(f"Broker 返回错误: {detail}")
        except Exception as e:
            raise Exception(f"读取 Office 文件失败: {str(e)}")
