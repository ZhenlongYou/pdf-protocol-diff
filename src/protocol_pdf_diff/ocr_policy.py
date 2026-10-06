"""一次比较共享 OCR 语言与累计识别时间；缓存命中不消耗识别预算。"""

from contextlib import contextmanager  # 异常/取消后也恢复调用前的 OCR 设置。
from contextvars import ContextVar  # 不同任务、线程间不共享语言或已用时间。
from dataclasses import dataclass
from functools import wraps
from inspect import signature  # 单文件公开 API 仍支持历史位置参数。
from time import monotonic  # 预算使用单调时钟，不受系统时间校准影响。


@dataclass
class OcrBudget:
    """累计的是识别调用时间，不含 PDF 解析、渲染和报告耗时。"""

    seconds: float | None = 300.0  # None 仅由调用方显式选择为不限时。
    used: float = 0.0
    skipped: int = 0


_BUDGET = ContextVar("pdf_ocr_budget", default=None)  # 同一次 old/new 共用额度。
_LANGUAGE = ContextVar("pdf_ocr_language", default=None)  # 表格与整页使用同一语言。


def current_ocr_language():
    """供未显式传语言的表格识别复用当前输入设置。"""
    return _LANGUAGE.get()


@contextmanager
def ocr_scope(language=None, seconds=300.0):
    """嵌套抽取继承同一预算，退出时不留下任务状态。"""
    budget = _BUDGET.get() or OcrBudget(seconds)  # 单文件 API 也有独立额度。
    budget_token = _BUDGET.set(budget)
    language_token = _LANGUAGE.set(language)
    try:
        yield budget  # 调用方可读取实际用时和预算耗尽后的跳过次数。
    finally:
        _LANGUAGE.reset(language_token)
        _BUDGET.reset(budget_token)


def ocr_extraction(function):
    """让公开单文件抽取的语言参数同时到达深层表格 OCR。"""
    call_signature = signature(function)  # 每个函数只绑定一次签名。
    @wraps(function)
    def run(*args, **kwargs):
        language = call_signature.bind(*args, **kwargs).arguments.get("ocr_language")
        with ocr_scope(language):  # 外层比较已建立预算时仅继承。
            return function(*args, **kwargs)
    return run


@contextmanager
def ocr_call_timeout(timeout):
    """限制一次真实识别的等待时间，并把失败调用也计入累计预算。"""
    budget = _BUDGET.get()
    remaining = None if budget is None or budget.seconds is None else budget.seconds - budget.used
    if remaining is not None and remaining <= 0:
        budget.skipped += 1  # 调用方会保留当前页/表格的失败原因。
        raise RuntimeError("累计 OCR 识别时间已用完；此区域未识别，可缩小页窗或增加识别时限。")
    started = monotonic()
    try:
        yield min(timeout, remaining) if remaining is not None else timeout
    finally:
        if budget is not None:
            budget.used += monotonic() - started  # 超时、失败均消耗额度，不能无限重试。
