"""比较范围是运行配置；原始文档的角色、文字和坐标不随配置改写。"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature

_PROFILE = ContextVar("pdf_comparison_profile", default="protocol")


def is_general_document():
    return _PROFILE.get() == "general"


def includes_role(role):
    return role == "technical" or is_general_document()


@contextmanager
def comparison_profile(profile):
    """嵌套调用共享范围，退出后恢复，避免一次通用比较影响下一次协议比较。"""
    if profile not in {"protocol", "general"}:
        raise ValueError("比较用途必须是 protocol（协议）或 general（通用文档）。")
    token = _PROFILE.set(profile)
    try:
        yield
    finally:
        _PROFILE.reset(token)


def configured_comparison(function):
    """由公开入口的 DiffOptions 建立策略，深层过滤器无需新增零散开关。"""
    call_signature = signature(function)
    @wraps(function)
    def run(*args, **kwargs):
        options = call_signature.bind(*args, **kwargs).arguments["options"]
        with comparison_profile(options.comparison_profile):
            return function(*args, **kwargs)
    return run


def configured_view(function):
    """输出只能继承固定视图的配置，不能用环境中的另一轮配置重新解释。"""
    @wraps(function)
    def run(view, *args, **kwargs):
        with comparison_profile(view.options.comparison_profile):
            return function(view, *args, **kwargs)
    return run
