"""Bound pure-computation caches to one comparison, including nested calls.

Context variables isolate concurrent callers. Each function has an LRU ceiling;
leaving the outer call drops every cache, so a desktop session never retains
the previous PDF's text. Without a session the original function runs directly.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps

_caches: ContextVar[dict | None] = ContextVar("comparison_caches", default=None)


@contextmanager
def comparison_scope():
    if _caches.get() is not None:
        yield
        return
    token = _caches.set({})
    try:
        yield
    finally:
        _caches.reset(token)


def comparison_session(function):
    @wraps(function)
    def run(*args, **kwargs):
        with comparison_scope():
            return function(*args, **kwargs)
    return run


def memoize_comparison(maxsize=4096):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            caches = _caches.get()
            if caches is None:
                return function(*args, **kwargs)
            if function not in caches:
                caches[function] = lru_cache(maxsize=maxsize)(function)
            return caches[function](*args, **kwargs)
        return call
    return decorate
