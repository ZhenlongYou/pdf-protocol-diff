"""验证桌面 GUI 在不同操作系统上的字体、缩放和窗口布局契约。"""

from __future__ import annotations

import os  # DISPLAY 判断让 Linux 无桌面环境时明确跳过真实 Tk 窗口测试。
import subprocess  # 通过真实 main.py 子进程验证 PyCharm/命令行 GUI smoke 入口。
import sys  # 当前平台用于判断 macOS 与 Windows 是否能直接创建桌面窗口。
import threading
import tempfile
import tkinter as tk  # 真实 Tk 根窗口用于验证普通 tk.Entry 也收到统一字体。
import tkinter.font as tkfont  # 把控件字体描述解析为实际 family，避免比较平台相关字符串格式。
import unittest  # 沿用项目现有标准库测试框架，避免为 GUI 样式测试增加运行依赖。
from argparse import (
    Namespace,  # 构造与 build_desktop.parse_args 等价的无副作用打包参数。
)
from pathlib import Path  # 定位项目内 Windows manifest，验证构建命令没有引用临时文件。
from tkinter import (
    ttk,
)  # 读取生产 ttk 样式最终解析出的字体，防止只验证原生 Tk 控件而假绿。
from unittest import mock  # 只替换 Win32/Tk 创建边界，验证 DPI 调用严格早于第一个窗口。

from build_desktop import (
    build_command,  # 通过纯命令生成器验证 Windows EXE 一定嵌入 DPI manifest。
)
from protocol_pdf_diff.desktop_gui import (  # 导入真实应用和纯选择逻辑，覆盖配置到控件的完整路径。
    ProtocolDiffDesktopApp,
    UI_THEME,
    configure_windows_dpi_awareness,
    create_tk_root,
    page_range_values,
    responsive_window_size,
    select_ui_font,
)
from protocol_pdf_diff.progress import ProgressEvent

PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)  # 子进程从实际项目根运行，复现用户入口和 .venv 切换。


class CrossPlatformGuiTests(unittest.TestCase):
    """保护 Windows 与 macOS 使用等价但原生清晰的中文界面字体。"""

    def test_select_ui_font_uses_platform_candidates_and_default_fallback(self) -> None:
        """各平台选择已安装候选字体，候选缺失时必须使用 Tk 默认字体。"""

        windows_font = select_ui_font(
            {"Arial", "Microsoft YaHei UI", "Segoe UI"},
            default_family="Tk Default",
            platform_name="win32",
        )  # Windows 优先使用为中文界面优化的微软雅黑 UI。
        mac_font = select_ui_font(
            {"Arial", "PingFang SC", "SF Pro Text"},
            default_family="Tk Default",
            platform_name="darwin",
        )  # macOS 继续使用当前观感良好的苹方字体。
        fallback_font = select_ui_font(
            {"Unrelated Font"},
            default_family="Tk Default",
            platform_name="win32",
        )  # 精简 Windows 环境没有候选字体时不能返回不存在的字体名。

        self.assertEqual(
            "Microsoft YaHei UI", windows_font
        )  # Windows 字体顺序与回填工具保持一致。
        self.assertEqual(
            "PingFang SC", mac_font
        )  # macOS 不因跨平台修复而改变现有字体风格。
        self.assertEqual(
            "Tk Default", fallback_font
        )  # 最终回退必须来自当前 Tcl/Tk 的真实默认字体。

    def test_responsive_window_size_stays_inside_logical_screen(self) -> None:
        """高 DPI 后的逻辑屏幕较小时，窗口必须缩小但不能凭空超出屏幕。"""

        common_screen = responsive_window_size(
            1366,
            768,
            target_width=1080,
            target_height=760,
            floor_width=820,
            floor_height=620,
        )  # 常见 Windows 笔记本保留目标宽度，并给任务栏和标题栏留下垂直空间。
        small_screen = responsive_window_size(
            720,
            500,
            target_width=1080,
            target_height=760,
            floor_width=820,
            floor_height=620,
        )  # 极小逻辑屏幕不能因最低尺寸反而生成超出屏幕的窗口。

        self.assertEqual(
            (1080, 688), common_screen
        )  # 768px 高屏幕扣除 80px 安全边距后使用 688px。
        self.assertEqual(
            (720, 500), small_screen
        )  # 屏幕小于设计下限时以实际屏幕尺寸为硬上限。

    def test_all_pages_mode_ignores_but_preserves_range_values(self) -> None:
        self.assertEqual((None, None), page_range_values("all", "16", "18", "旧协议"))
        self.assertEqual((16, 18), page_range_values("range", "16", "18", "旧协议"))

    def test_windows_dpi_awareness_is_set_before_tk_and_other_platforms_are_noop(
        self,
    ) -> None:
        """Windows 请求 Per-Monitor V2；macOS/Linux 不能触碰 Win32 API。"""

        calls: list[int] = []  # 记录注入的 Win32 setter 收到的 DPI context 常量。

        def accept_context(context: int) -> bool:
            calls.append(
                context
            )  # 测试替身只记录调用，不依赖当前 macOS 是否存在 user32.dll。
            return True  # 模拟 Windows 在创建首个窗口前成功接受 DPI 模式。

        windows_status = configure_windows_dpi_awareness(
            platform_name="win32",
            modern_setter=accept_context,
        )  # 注入 setter 验证生产调用的参数和成功语义。
        mac_status = configure_windows_dpi_awareness(
            platform_name="darwin",
            modern_setter=lambda _context: self.fail(
                "macOS must not call Win32 DPI API"
            ),
        )  # 非 Windows 平台必须在加载或调用系统 DLL 前返回。

        self.assertEqual(
            [-4], calls
        )  # -4 是微软定义的 DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2。
        self.assertEqual(
            "per-monitor-v2", windows_status
        )  # 成功状态供 GUI smoke test 和排错输出使用。
        self.assertEqual(
            "not-applicable", mac_status
        )  # macOS 保持原有 Cocoa/Tk 缩放路径。

    def test_root_factory_sets_dpi_awareness_before_creating_tk(self) -> None:
        """源码启动和冻结 EXE 都必须在第一个 Tk 根窗口前完成 DPI 配置。"""

        call_order: list[str] = []  # 用顺序日志保护微软要求的“创建任何 UI 之前”约束。
        sentinel_root = object()  # 假根窗口只用于验证工厂返回值和调用顺序。
        with (
            mock.patch(
                "protocol_pdf_diff.desktop_gui.configure_windows_dpi_awareness",
                side_effect=lambda: call_order.append("dpi"),
            ),
            mock.patch(
                "protocol_pdf_diff.desktop_gui.tk.Tk",
                side_effect=lambda: call_order.append("tk") or sentinel_root,
            ),
        ):
            created_root = (
                create_tk_root()
            )  # 通过生产根窗口工厂执行，不直接测试私有实现细节。

        self.assertEqual(
            ["dpi", "tk"], call_order
        )  # DPI awareness 晚于 Tk 时 Windows 已无法安全更改模式。
        self.assertIs(
            sentinel_root, created_root
        )  # 工厂必须把真实 Tk 根窗口交还给应用构造器。

    def test_windows_build_embeds_per_monitor_v2_manifest(self) -> None:
        """PyInstaller Windows 产物必须通过 manifest 在进程启动阶段声明 DPI 模式。"""

        args = Namespace(
            clean=False, console=False, onefile=True
        )  # 模拟用户当前 build_windows.bat 的真实参数。
        windows_command = build_command(
            args, platform_name="win32"
        )  # 生成 Windows onefile 构建参数。
        mac_command = build_command(
            args, platform_name="darwin"
        )  # macOS 不应误带 Windows 专用 manifest。
        manifest_index = windows_command.index(
            "--manifest"
        )  # manifest 参数和值必须相邻传给 PyInstaller。
        manifest_path = Path(
            windows_command[manifest_index + 1]
        )  # 解析生产命令实际引用的项目文件。
        manifest_text = manifest_path.read_text(
            encoding="utf-8"
        )  # 验证内容而非仅验证文件名存在。

        self.assertTrue(
            manifest_path.is_file()
        )  # 构建不能依赖未提交或构建时才生成的临时 manifest。
        self.assertIn(
            "PerMonitorV2, unaware", manifest_text
        )  # Windows 10 1703+ 使用微软推荐的 Per-Monitor V2。
        self.assertIn(
            "true/pm", manifest_text
        )  # 旧 Windows 使用兼容 dpiAware 声明而非模糊缩放。
        self.assertNotIn(
            "--manifest", mac_command
        )  # macOS bundle 继续使用既有 Info.plist 路径。

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "main GUI smoke test needs a desktop session",
    )
    def test_main_exposes_noninteractive_gui_smoke_test(self) -> None:
        """PyCharm 主入口必须能无输入执行真实 GUI 构造和跨平台样式检查。"""

        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "main.py"), "--gui-smoke-test"],
            cwd=PROJECT_ROOT,
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )  # 使用当前 Python 启动真实脚本，脚本自身仍会按生产规则进入项目 .venv。

        self.assertEqual(
            0, result.returncode, result.stderr or result.stdout
        )  # 未识别参数、窗口构造或控件检查失败都必须暴露。
        self.assertIn(
            "GUI smoke test passed", result.stdout
        )  # 稳定成功标记供 Windows CI 和交付门禁识别。

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk font test needs a desktop session",
    )
    def test_application_applies_resolved_font_to_tk_and_ttk_controls(self) -> None:
        """同一已解析字体必须覆盖 ttk 样式和原生页码输入框。"""

        root = tk.Tk()  # 创建真实 Tcl/Tk 解释器，字体列表和最终回退才具有平台意义。
        root.withdraw()  # 测试只检查控件配置，不在桌面留下可见窗口。
        try:
            app = ProtocolDiffDesktopApp(
                root
            )  # 通过生产构造路径应用完整主题，而不是直接调用私有样式函数。
            root.update_idletasks()  # 让 ttk 和原生 Tk 控件完成字体解析。
            page_entry = next(
                iter(app.page_entry_widgets.values())
            )  # 取一个真实页码框验证 Tk 控件链路。
            entry_family = str(
                tkfont.Font(root=root, font=page_entry.cget("font")).actual("family")
            )  # 将 Tk 字体名称解析为系统最终采用的字体族。
            style = ttk.Style(
                root
            )  # 读取生产构造器已配置的真实 ttk 主题，而不是重新拼一套预期值。
            ttk_families = {
                style_name: str(
                    tkfont.Font(
                        root=root,
                        font=style.lookup(style_name, "font"),
                    ).actual("family")
                )
                for style_name in ("TLabel", "TEntry", "Primary.TButton")
            }  # 普通标签、路径输入框和主按钮覆盖三类最显眼的 ttk 控件。

            self.assertTrue(
                app.ui_font
            )  # 应用必须公开记录本次会话实际选择的字体，便于 smoke test 诊断。
            self.assertEqual(
                app.ui_font, entry_family
            )  # 原生页码框不能再独自回退为另一套 Windows 字体。
            self.assertEqual(
                {
                    "TLabel": app.ui_font,
                    "TEntry": app.ui_font,
                    "Primary.TButton": app.ui_font,
                },
                ttk_families,
            )  # ttk 样式也必须解析到同一真实字体，不能只让原生 Entry 看起来正确。
        finally:
            root.destroy()  # 始终释放窗口资源，避免后续 GUI 测试复用到已污染的默认根窗口。

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk responsive-layout test needs a desktop session",
    )
    def test_application_uses_responsive_scrollable_viewport(self) -> None:
        """窗口必须按逻辑屏幕定初始尺寸，并为缩放后的矮屏保留滚动入口。"""

        root = tk.Tk()  # 使用真实屏幕逻辑尺寸验证生产窗口构造路径。
        root.withdraw()  # 不显示窗口也能完成 geometry、Canvas 和滚动区域计算。
        try:
            app = ProtocolDiffDesktopApp(
                root
            )  # 构造完整界面，确保响应式能力不是孤立辅助函数。
            root.update_idletasks()  # 让 Canvas 根据所有卡片的请求尺寸生成 scrollregion。
            screen_width = (
                root.winfo_screenwidth()
            )  # 当前系统经过 DPI 换算后的逻辑屏幕宽度是窗口硬上限。
            screen_height = (
                root.winfo_screenheight()
            )  # 当前系统经过 DPI 换算后的逻辑屏幕高度是窗口硬上限。
            minimum_width, minimum_height = (
                root.minsize()
            )  # 读取应用实际采用的可调整窗口下限。

            self.assertLessEqual(
                app.initial_window_size[0], screen_width
            )  # 初始窗口不能横向落到屏幕外。
            self.assertLessEqual(
                app.initial_window_size[1], screen_height
            )  # 初始窗口不能纵向挡住底部操作。
            self.assertLessEqual(
                minimum_width, app.initial_window_size[0]
            )  # 最小宽度不能反过来强迫窗口超过初始宽度。
            self.assertLessEqual(
                minimum_height, app.initial_window_size[1]
            )  # 最小高度必须允许矮屏缩小。
            self.assertTrue(
                app.content_canvas.cget("yscrollcommand")
            )  # 内容超出视口时必须有真实滚动连接。
            self.assertTrue(
                app.content_scrollbar.winfo_exists()
            )  # 用户必须看得见可发现的垂直滚动控件。
            root.deiconify()  # 映射真实窗口后，Tk 才会派发与用户滚轮一致的窗口事件。
            root.geometry("1120x720+0+0")
            root.update()
            app._update_content_scrollregion()
            self.assertEqual(
                "", app.content_scrollbar.winfo_manager()
            )  # 默认尺寸内容适配时不应常驻一条无作用的滚动条。
            root.geometry(
                "760x520+0+0"
            )  # 压缩到响应式下限，确保完整表单高度超过当前视口。
            root.update()  # 让 Canvas 写入最终 scrollregion，并接收后续鼠标滚轮事件。
            app._update_content_scrollregion()
            self.assertEqual(
                "grid", app.content_scrollbar.winfo_manager()
            )  # 窄屏堆叠发生真实溢出时必须重新显示滚动条。
            event_target = app.old_document_card  # 默认隐藏的页码框不能接收用户滚轮；从当前可见文档卡验证事件冒泡。
            app.content_canvas.yview_moveto(
                0.0
            )  # 每组方向检查从顶部开始，避免前一事件污染边界条件。
            root.update()
            before_mousewheel = (
                app.content_canvas.yview()
            )  # 记录 Windows/macOS 滚轮前的可见区比例。
            event_target.event_generate(
                "<MouseWheel>", delta=-120
            )  # 模拟 Windows/macOS 向下滚动一格。
            root.update()  # 处理滚轮回调并刷新 Canvas 视口。
            after_mousewheel_down = (
                app.content_canvas.yview()
            )  # 读取向下滚动后的可见区比例。
            event_target.event_generate(
                "<MouseWheel>", delta=120
            )  # 正 delta 必须把内容向上移回，不能只验证一个方向。
            root.update()
            after_mousewheel_up = (
                app.content_canvas.yview()
            )  # 读取反向滚动后的可见区比例。

            app.content_canvas.yview_moveto(0.0)  # Linux/X11 方向检查同样从顶部开始。
            root.update()
            before_linux_wheel = (
                app.content_canvas.yview()
            )  # 记录 Button-5 派发前的可见区比例。
            event_target.event_generate("<Button-5>")  # Linux Button-5 表示内容向下移动。
            root.update()
            after_button_5 = (
                app.content_canvas.yview()
            )  # 读取 Linux 向下滚动后的可见区比例。
            event_target.event_generate("<Button-4>")  # Linux Button-4 表示内容向上移动。
            root.update()
            after_button_4 = (
                app.content_canvas.yview()
            )  # 读取 Linux 反向滚动后的可见区比例。

            self.assertGreater(
                after_mousewheel_down[0], before_mousewheel[0]
            )  # Windows/macOS 滚轮必须实际向下移动内容，不能只有一根无法使用的滚动条。
            self.assertLess(
                after_mousewheel_up[0], after_mousewheel_down[0]
            )  # Windows/macOS 反向滚轮必须把内容向上移回。
            self.assertGreater(
                after_button_5[0], before_linux_wheel[0]
            )  # Linux Button-5 必须向下，交换 Button-4/5 方向时本断言会失败。
            self.assertLess(
                after_button_4[0], after_button_5[0]
            )  # Linux Button-4 必须向上，并且事件从子 Entry 也能到达根窗口绑定。
        finally:
            root.destroy()  # 释放真实窗口，避免影响其它 Tk 测试的默认根状态。

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk workbench test needs a desktop session",
    )
    def test_application_exposes_dual_document_cards_and_collapsed_advanced_settings(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            app = ProtocolDiffDesktopApp(root)
            root.update_idletasks()

            self.assertEqual((1180, 720), app.design_window_size)
            self.assertEqual("#18192D", UI_THEME["canvas"])
            self.assertEqual("#8BDEF8", UI_THEME["accent"])
            self.assertEqual("#8273FF", UI_THEME["old_document_accent"])
            self.assertEqual("#EF75BD", UI_THEME["new_document_accent"])
            self.assertEqual("Protocol Comparison Tool", app.header_title_label.cget("text"))
            self.assertTrue(app.brand_mark.find_all())
            self.assertTrue(app.old_document_icon.find_all())
            self.assertTrue(app.new_document_icon.find_all())
            self.assertGreaterEqual(int(app.old_document_icon.cget("width")), 80)
            self.assertGreaterEqual(int(app.old_document_icon.cget("height")), 96)
            self.assertEqual(2, len(app.settings_chips))
            static_copy: list[str] = []
            pending = [app.content_container]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if "text" in widget.keys():
                    static_copy.append(str(widget.cget("text")))
            joined_copy = "\n".join(static_copy)
            self.assertEqual(1, static_copy.count("Protocol Comparison Tool"))
            for forbidden_copy in (
                "PROTOCOL DIFF STUDIO",
                "本地处理",
                "选择两个协议版本和页面范围",
                "基准版本与目标版本将按给定页面窗口建立强关联比较",
                "运行前快速复核",
                "可随时修改",
                "BASELINE",
                "REVISION",
            ):
                self.assertNotIn(forbidden_copy, joined_copy)
            self.assertEqual("0.72", app.profile_value_vars["threshold"].get())
            self.assertEqual("20", app.profile_value_vars["snippets"].get())
            self.assertEqual("all", app.old_page_mode_var.get())
            self.assertEqual("all", app.new_page_mode_var.get())
            self.assertFalse(app.auto_open_var.get())
            self.assertFalse(app.advanced_expanded)
            self.assertEqual("", app.advanced_body.master.winfo_manager())
            self.assertEqual("", app.old_range_frame.winfo_manager())
            self.assertEqual("", app.new_range_frame.winfo_manager())

            app.old_start_var.set("16")
            app.old_end_var.set("18")
            app.old_page_mode_var.set("range")
            app._sync_page_mode("old")
            root.update_idletasks()
            self.assertEqual("grid", app.old_range_frame.winfo_manager())
            app.old_page_mode_var.set("all")
            app._sync_page_mode("old")
            self.assertEqual("16", app.old_start_var.get())
            self.assertEqual("18", app.old_end_var.get())

            app._apply_responsive_layout(1180)
            self.assertEqual("side-by-side", app.document_layout_mode)
            self.assertEqual(1, int(app.new_document_card.grid_info()["column"]))
            app._apply_responsive_layout(760)
            self.assertEqual("stacked", app.document_layout_mode)
            self.assertEqual(1, int(app.new_document_card.grid_info()["row"]))
            self.assertEqual(2, int(app.advanced_bar.grid_info()["row"]))

            app._fit_content_to_viewport(mock.Mock(width=760))
            root.deiconify()
            root.geometry("760x520+0+0")
            root.update()
            self.assertGreaterEqual(app.advanced_toggle.winfo_height(), 32)
            self.assertEqual(0, int(app.advanced_toggle.grid_info()["row"]))

            app.output_dir_var.set(
                "/tmp/this-is-a-valid-but-extremely-long-output-directory-name-that-must-not-hide-actions"
            )
            root.update()
            compact_output = app.profile_value_vars["output"].get()
            self.assertIn("…", compact_output)
            self.assertLessEqual(len(compact_output), 24)
            self.assertLessEqual(
                app.advanced_bar.winfo_reqwidth(),
                app.content_canvas.winfo_width(),
            )
            self.assertLessEqual(
                app.advanced_toggle.winfo_rootx()
                + app.advanced_toggle.winfo_width(),
                app.advanced_bar.winfo_rootx() + app.advanced_bar.winfo_width(),
            )

            app.min_similarity_var.set("0.80")
            app.max_snippets_var.set("12")
            app.auto_open_var.set(True)
            self.assertEqual("0.80", app.profile_value_vars["threshold"].get())
            self.assertEqual("12", app.profile_value_vars["snippets"].get())
            self.assertTrue(app.auto_open_var.get())
        finally:
            root.destroy()

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk glass UI contract needs a desktop session",
    )
    def test_premium_startup_contract_rejects_redundant_persistent_copy(self) -> None:
        """The chosen A visual direction must not regress into an explanatory form."""

        root = tk.Tk()
        root.withdraw()
        try:
            app = ProtocolDiffDesktopApp(root)
            root.update_idletasks()
            static_copy: list[str] = []
            pending = [app.content_container]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if "text" in widget.keys():
                    static_copy.append(str(widget.cget("text")))

            self.assertEqual("#18192D", UI_THEME["canvas"])
            self.assertEqual("#8BDEF8", UI_THEME["accent"])
            self.assertEqual(1, static_copy.count("Protocol Comparison Tool"))
            self.assertFalse(
                {
                    "PROTOCOL DIFF STUDIO",
                    "本地处理",
                    "选择两个协议版本和页面范围",
                    "运行前快速复核",
                    "BASELINE",
                    "REVISION",
                }.intersection(static_copy)
            )
        finally:
            root.destroy()

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk approved studio contract needs a desktop session",
    )
    def test_approved_glass_studio_contract_has_minimal_copy_and_running_cue(self) -> None:
        """The production window must match the user-approved A prototype contract."""

        root = tk.Tk()
        root.withdraw()
        try:
            app = ProtocolDiffDesktopApp(root)
            root.update_idletasks()

            self.assertEqual("Protocol Comparison Tool", app.header_title_label.cget("text"))
            self.assertFalse(hasattr(app, "swap_button"))
            self.assertGreaterEqual(app.design_window_size[0], 1120)
            self.assertGreaterEqual(app.design_window_size[1], 700)
            self.assertGreaterEqual(int(app.old_document_icon.cget("width")), 120)
            self.assertGreaterEqual(int(app.old_document_icon.cget("height")), 136)
            self.assertEqual("全部页面", app.old_all_pages_button.cget("text"))
            self.assertEqual("选择页面", app.old_range_pages_button.cget("text"))
            self.assertEqual("全部页面", app.new_all_pages_button.cget("text"))
            self.assertEqual("选择页面", app.new_range_pages_button.cget("text"))

            static_copy: list[str] = []
            pending = [app.content_container]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if "text" in widget.keys():
                    static_copy.append(str(widget.cget("text")))
            joined_copy = "\n".join(static_copy)
            for forbidden_copy in (
                "协议 PDF 对比",
                "请选择旧版和新版 PDF",
                "请选择两份 PDF",
                "交换旧/新",
                "尚未选择",
                "旧版\n旧版 PDF",
                "新版\n新版 PDF",
            ):
                self.assertNotIn(forbidden_copy, joined_copy)

            app._set_running(True)
            root.update_idletasks()
            self.assertEqual("比较中", app.run_button.cget("text"))
            self.assertEqual("正在比较", app.running_label.cget("text"))
            self.assertEqual("indeterminate", str(app.activity_progress.cget("mode")))
            self.assertEqual("grid", app.activity_progress.winfo_manager())
            app._set_running(False)
            self.assertEqual("开始比较", app.run_button.cget("text"))
            self.assertEqual("", app.activity_progress.winfo_manager())
        finally:
            root.destroy()

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk state test needs a desktop session",
    )
    def test_running_state_locks_and_restores_all_inputs(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            app = ProtocolDiffDesktopApp(root)
            root.update_idletasks()
            app._set_running(True)
            self.assertTrue(app.is_running)
            self.assertIsNotNone(app._elapsed_after_id)
            first_timer_id = app._elapsed_after_id
            self.assertTrue(all(str(widget.cget("state")) == "disabled" for widget in app.input_widgets))
            app._set_running(False)
            self.assertFalse(app.is_running)
            self.assertIsNone(app._elapsed_after_id)
            self.assertNotIn(first_timer_id, root.tk.call("after", "info"))
            self.assertTrue(all(str(widget.cget("state")) != "disabled" for widget in app.input_widgets))

            app._set_running(True)
            second_timer_id = app._elapsed_after_id
            self.assertNotEqual(first_timer_id, second_timer_id)
            root.after_cancel(second_timer_id)
            app._elapsed_after_id = None
            app._tick_elapsed()
            rescheduled_timer_id = app._elapsed_after_id
            self.assertIsNotNone(rescheduled_timer_id)
            app._set_running(False)
            pending_timers = root.tk.call("after", "info")
            self.assertNotIn(second_timer_id, pending_timers)
            self.assertNotIn(rescheduled_timer_id, pending_timers)
        finally:
            root.destroy()

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk worker failure test needs a desktop session",
    )
    def test_thread_start_failure_restores_inputs_and_shows_inline_error(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            app = ProtocolDiffDesktopApp(root)
            app._set_running(True)
            with (
                mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("no thread")),
                mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showerror"),
            ):
                app._start_worker(mock.Mock())
            self.assertFalse(app.is_running)
            self.assertIn("no thread", app.summary_var.get())
            self.assertTrue(all(str(widget.cget("state")) != "disabled" for widget in app.input_widgets))
        finally:
            root.destroy()

    def test_open_html_failure_is_non_destructive_and_user_visible(self) -> None:
        app = object.__new__(ProtocolDiffDesktopApp)
        app._last_outputs = {"html": Path("/tmp/report.html")}
        with (
            mock.patch("protocol_pdf_diff.desktop_gui.webbrowser.open", return_value=False),
            mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showwarning") as warning,
        ):
            app.open_html_report()
        warning.assert_called_once()

        with (
            mock.patch(
                "protocol_pdf_diff.desktop_gui.webbrowser.open",
                side_effect=OSError("browser unavailable"),
            ),
            mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showwarning") as warning,
        ):
            app.open_html_report()
        warning.assert_called_once()

    def test_open_report_directory_failure_is_user_visible(self) -> None:
        app = object.__new__(ProtocolDiffDesktopApp)
        with tempfile.TemporaryDirectory() as temp_dir:
            app._last_outputs = {"report_dir": Path(temp_dir)}
            with (
                mock.patch("protocol_pdf_diff.desktop_gui.open_path", return_value=False),
                mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showwarning") as warning,
            ):
                app.open_report_directory()
            warning.assert_called_once()

            with (
                mock.patch(
                    "protocol_pdf_diff.desktop_gui.open_path",
                    side_effect=OSError("file manager unavailable"),
                ),
                mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showwarning") as warning,
            ):
                app.open_report_directory()
            warning.assert_called_once()

    def test_close_request_is_blocked_while_report_is_running(self) -> None:
        app = object.__new__(ProtocolDiffDesktopApp)
        app.root = mock.Mock()
        app.is_running = True
        with mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showwarning") as warning:
            app._on_close_requested()
        warning.assert_called_once()
        app.root.destroy.assert_not_called()

        app.is_running = False
        app._on_close_requested()
        app.root.destroy.assert_called_once_with()

    def test_macos_quit_uses_the_same_guarded_close_handler(self) -> None:
        """Command-Q 与 Dock 退出不能绕过正在写报告时的关闭保护。"""

        app = object.__new__(ProtocolDiffDesktopApp)
        app.root = mock.Mock()
        app.is_running = True

        with mock.patch("protocol_pdf_diff.desktop_gui.sys.platform", "darwin"):
            app._install_platform_close_handlers()

        app.root.protocol.assert_called_once_with(
            "WM_DELETE_WINDOW", app._on_close_requested
        )
        app.root.tk.createcommand.assert_called_once_with(
            "::tk::mac::Quit", app._on_close_requested
        )
        mac_quit_handler = app.root.tk.createcommand.call_args.args[1]
        with mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showwarning") as warning:
            mac_quit_handler()
        warning.assert_called_once()
        app.root.destroy.assert_not_called()

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk validation test needs a desktop session",
    )
    def test_empty_output_directory_is_rejected_instead_of_using_cwd(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            app = ProtocolDiffDesktopApp(root)
            with tempfile.TemporaryDirectory() as temp_dir:
                old_pdf = Path(temp_dir) / "old.pdf"
                new_pdf = Path(temp_dir) / "new.pdf"
                old_pdf.touch()
                new_pdf.touch()
                app.old_pdf_var.set(str(old_pdf))
                app.new_pdf_var.set(str(new_pdf))
                app.output_dir_var.set("   ")
                with self.assertRaisesRegex(ValueError, "输出目录不能为空"):
                    app.collect_config()
        finally:
            root.destroy()

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk progress test needs a desktop session",
    )
    def test_progress_uses_page_counts_only_for_extraction_and_failure_keeps_inputs(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            app = ProtocolDiffDesktopApp(root)
            app.old_pdf_var.set("kept-old.pdf")
            app._set_running(True)
            app._handle_progress(
                ProgressEvent(
                    stage="read_old",
                    side="old",
                    completed_pages=2,
                    total_pages=3,
                )
            )
            self.assertEqual("grid", app.progress.winfo_manager())
            self.assertEqual(2.0, float(app.progress.cget("value")))
            self.assertIn("第 2/3 页", app.status_var.get())

            app._handle_progress(ProgressEvent(stage="match_diff"))
            self.assertEqual("", app.progress.winfo_manager())
            self.assertNotIn("%", app.status_var.get())
            self.assertNotIn("页", app.status_var.get())

            app._set_running(False)
            with mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showerror"):
                app._handle_error(RuntimeError("明确错误"))
            self.assertEqual("kept-old.pdf", app.old_pdf_var.get())
            self.assertIn("明确错误", app.summary_var.get())
            self.assertIn("失败", app.status_var.get())

            long_message = "首行错误\n" + "x" * 300
            with mock.patch("protocol_pdf_diff.desktop_gui.messagebox.showerror") as error_dialog:
                app._handle_error(RuntimeError(long_message))
            inline_summary = app.summary_var.get()
            self.assertNotIn("\n", inline_summary)
            self.assertLessEqual(len(inline_summary), 180)
            self.assertIn("首行错误", inline_summary)
            self.assertIn(long_message, error_dialog.call_args.args[1])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()  # 支持在 PyCharm 中直接运行本测试文件。
