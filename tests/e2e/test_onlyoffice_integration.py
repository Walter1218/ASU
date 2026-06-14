#!/usr/bin/env python3
"""OnlyOffice 集成验证测试 - 轻量版

验证关键点：
1. OnlyOffice Server 健康
2. OnlyOfficeWidget 可导入且 is_available() 返回 True
3. stage_editor 的 _HAS_ONLYOFFICE 为 True
4. 渲染器选择逻辑代码可达
"""

import sys
import os
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def main():
    print("\n🔧 OnlyOffice 集成验证测试\n")
    
    passed = 0
    failed = 0
    
    # 测试 1: OnlyOffice Server
    print("=" * 50)
    print("测试 1: OnlyOffice Document Server 健康检查")
    print("=" * 50)
    try:
        url = "http://localhost:9090/healthcheck"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200 and resp.read().decode().strip() == "true":
                print("  ✅ OnlyOffice Server 健康\n")
                passed += 1
            else:
                print("  ❌ 响应异常\n")
                failed += 1
    except Exception as e:
        print(f"  ❌ 连接失败: {e}\n")
        failed += 1
    
    # 测试 2: PyQt6-WebEngine
    print("=" * 50)
    print("测试 2: PyQt6-WebEngine 可用性")
    print("=" * 50)
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        print("  ✅ QWebEngineView 可用\n")
        passed += 1
    except ImportError as e:
        print(f"  ❌ 导入失败: {e}\n")
        failed += 1
    
    # 测试 3: OnlyOfficeWidget 导入
    print("=" * 50)
    print("测试 3: OnlyOfficeWidget 导入")
    print("=" * 50)
    try:
        from gui.v5plus.onlyoffice_widget import OnlyOfficeWidget
        print("  ✅ OnlyOfficeWidget 导入成功\n")
        passed += 1
    except ImportError as e:
        print(f"  ❌ 导入失败: {e}\n")
        failed += 1
    
    # 测试 4: stage_editor _HAS_ONLYOFFICE
    print("=" * 50)
    print("测试 4: stage_editor 模块级检测")
    print("=" * 50)
    try:
        # 确保项目根目录在路径中
        project_root = '/Users/onetwo/Documents/trae_projects/OpenCopilot'
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        # 不创建 GUI 组件，只检查模块级变量
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'stage_editor',
            '/Users/onetwo/Documents/trae_projects/OpenCopilot/gui/v5plus/stage_editor.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        has_onlyoffice = module._HAS_ONLYOFFICE
        print(f"  _HAS_ONLYOFFICE = {has_onlyoffice}")
        
        if has_onlyoffice:
            print("  ✅ OnlyOfficeWidget 已导入\n")
            passed += 1
        else:
            print("  ❌ OnlyOfficeWidget 导入失败\n")
            failed += 1
    except Exception as e:
        print(f"  ❌ 测试失败: {e}\n")
        failed += 1
    
    # 测试 5: 渲染器选择逻辑验证
    print("=" * 50)
    print("测试 5: 渲染器选择逻辑（代码检查）")
    print("=" * 50)
    try:
        with open('/Users/onetwo/Documents/trae_projects/OpenCopilot/gui/v5plus/stage_editor.py', 'r') as f:
            content = f.read()
        
        # 检查关键代码模式
        checks = [
            ('_HAS_ONLYOFFICE', '_HAS_ONLYOFFICE 变量'),
            ('widget.is_available()', 'is_available() 调用'),
            ('_use_onlyoffice = True', '渲染器选择'),
            ('🟢 ONLYOFFICE', '状态标签'),
        ]
        
        all_ok = True
        for pattern, desc in checks:
            if pattern in content:
                print(f"  ✅ {desc}")
            else:
                print(f"  ❌ {desc} 未找到")
                all_ok = False
        
        if all_ok:
            print("\n  ✅ 渲染器选择逻辑完整\n")
            passed += 1
        else:
            print("\n  ❌ 渲染器选择逻辑不完整\n")
            failed += 1
    except Exception as e:
        print(f"  ❌ 测试失败: {e}\n")
        failed += 1
    
    # 测试 6: OnlyOfficeWidget.is_available() 实际调用
    print("=" * 50)
    print("测试 6: OnlyOfficeWidget.is_available() 实际调用")
    print("=" * 50)
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        
        from gui.v5plus.onlyoffice_widget import OnlyOfficeWidget
        widget = OnlyOfficeWidget()
        available = widget.is_available()
        
        print(f"  is_available() = {available}")
        
        if available:
            print("  ✅ OnlyOffice 可用\n")
            passed += 1
        else:
            print("  ❌ OnlyOffice 不可用\n")
            failed += 1
    except Exception as e:
        print(f"  ❌ 测试失败: {e}\n")
        failed += 1
    
    # 汇总
    print("=" * 50)
    print("测试结果汇总")
    print("=" * 50)
    print(f"  通过: {passed}")
    print(f"  失败: {failed}")
    print(f"  总计: {passed + failed}")
    
    if failed == 0 and passed == 6:
        print("\n🎉 所有测试通过！OnlyOffice 集成正常！")
        print("\n📌 结论：")
        print("   - OnlyOffice Server 运行正常 (9090)")
        print("   - PyQt6-WebEngine 可用")
        print("   - OnlyOfficeWidget 可导入")
        print("   - is_available() 返回 True")
        print("   - 渲染器选择逻辑正确")
        print("   - GUI 应显示 🟢 ONLYOFFICE 编辑器")
        return 0
    else:
        print("\n⚠️  存在失败项，请检查")
        return 1


if __name__ == "__main__":
    sys.exit(main())
