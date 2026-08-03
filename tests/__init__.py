"""Make every test module import the local src-layout package independently."""

from pathlib import Path  # 从测试包位置解析规范项目根目录。
import sys  # 在导入任一测试模块前配置本地源码搜索路径。


PROJECT_ROOT = Path(__file__).resolve().parents[1]  # `tests/` 的父目录就是 PDF 工具根目录。
SRC_DIR = PROJECT_ROOT / "src"  # 生产包位于 src/protocol_pdf_diff。
if str(SRC_DIR) not in sys.path:  # 避免重复插入路径改变其它测试的模块优先级。
    sys.path.insert(0, str(SRC_DIR))  # 支持 `python -m unittest tests.test_xxx` 的独立运行方式。
