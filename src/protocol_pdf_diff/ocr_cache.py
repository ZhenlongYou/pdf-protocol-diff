"""Persistent OCR result cache keyed by the exact rendered image bytes.

Purpose
-------
A full-document diff calls Tesseract once per table screenshot (``--psm 6``)
and once per scan-like page (``--psm 3``).  The same page window or the same
document is frequently processed again (re-running a validation, opening
overlapping page windows, tuning report options in the GUI).  Tesseract is
deterministic: for identical image pixels, language, PSM configuration and
engine version it returns identical text.  Storing that text therefore cannot
change any diff result; it only skips repeated recognition work.

Cache identity
--------------
The key is a SHA-256 over the image mode, size and raw pixel bytes, plus the
Tesseract CLI configuration (``--psm ...``), the language expression, and the
installed Tesseract identity (version line plus ``TESSDATA_PREFIX``).  Any
mismatch is a miss, so text produced under one engine or configuration can
never be served for a different request.  The per-call timeout is deliberately
not part of the key: a successful recognition does not depend on it, while a
timed-out call raises before anything could be stored.

Failure policy
--------------
Caching is strictly best-effort: unreadable cache directories, images without
PIL-style ``tobytes`` (unit-test doubles), missing engine identity, or write
errors all fall back to the live Tesseract call.  Nothing in this module may
change OCR behavior on a miss, and no cache problem may raise into extraction.

Environment
-----------
``PROTOCOL_PDF_DIFF_OCR_CACHE`` selects the cache directory:

* unset -- ``Path.home() / ".cache" / "protocol_pdf_diff" / "ocr"``
* a filesystem path -- that directory
* ``0`` / ``off`` / ``false`` / ``no`` / ``disable`` -- caching disabled

Under ``unittest``/``pytest`` the default directory is disabled even when the
variable is unset: mocked recognizers must not pollute the real cache, and
historical entries must not shadow a mock.  An explicit variable (for example a
temporary directory used by the cache's own tests) always wins.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_DISABLED_VALUES = frozenset({"0", "off", "false", "no", "disable", "disabled"})

# 仅用于测试与诊断的计数；不参与任何结果判断，线程间丢失一次计数无影响。
statistics: dict[str, int] = {"calls": 0, "hits": 0, "misses": 0, "writes": 0}

_ENGINE_CACHE: dict[str, str | None] = {}


def cached_image_to_string(
    image: object,
    *,
    config: str,
    timeout: int,
    lang: str | None = None,
) -> str:
    """Return Tesseract text for ``image``, reusing an identical earlier call.

    The live call is routed through ``_call_tesseract`` so tests can substitute
    a deterministic recognizer without touching this cache's contract.
    """

    statistics["calls"] += 1
    directory = _cache_directory()
    engine = _engine_identity() if directory is not None else None
    key = (
        _cache_key(image, config=config, lang=lang, engine=engine)
        if directory is not None and engine is not None
        else None
    )
    if key is not None:
        cache_path = _cache_path(directory, key)
        cached = _read_cached_text(cache_path)
        if cached is not None:
            statistics["hits"] += 1
            return cached
        statistics["misses"] += 1
    text = _call_tesseract(image, config=config, timeout=timeout, lang=lang)
    if key is not None:
        _write_cached_text(_cache_path(directory, key), text)
        statistics["writes"] += 1
    return text


def reset_statistics() -> None:
    """Reset diagnostic counters; used by tests and by manual profiling."""

    for name in statistics:
        statistics[name] = 0


def _call_tesseract(
    image: object,
    *,
    config: str,
    timeout: int,
    lang: str | None = None,
) -> str:
    """Run the real recognizer exactly as the pre-cache code did."""

    import pytesseract  # 延迟导入，缺失时由调用方保持原有错误语义。

    arguments: dict[str, object] = {"config": config, "timeout": timeout}
    if lang is not None:
        arguments["lang"] = lang
    return pytesseract.image_to_string(image, **arguments)


def _cache_directory() -> Path | None:
    """Resolve the configured cache directory, or ``None`` when disabled."""

    raw = os.environ.get("PROTOCOL_PDF_DIFF_OCR_CACHE")
    try:
        if raw is not None and raw.strip().lower() in _DISABLED_VALUES:
            return None
        if raw:
            return Path(raw).expanduser().resolve()
        if "unittest" in sys.modules or "pytest" in sys.modules:
            # 测试进程默认不触碰真实用户缓存：mock 的识别结果不能写进缓存，
            # 历史缓存也不能掩盖 mock 调用。显式环境变量在上方已优先处理。
            return None
        return Path.home() / ".cache" / "protocol_pdf_diff" / "ocr"
    except (OSError, RuntimeError):
        return None  # 主页不可解析或路径非法时按禁用处理，绝不阻断 OCR。


def _engine_identity() -> str | None:
    """Return the Tesseract engine identity used as part of every key.

    ``None`` means the engine cannot be identified (for example Tesseract is
    not installed); callers then skip caching entirely rather than risk stale
    text from an unknown engine.
    """

    if "value" not in _ENGINE_CACHE:
        _ENGINE_CACHE["value"] = _detect_engine_identity()
    return _ENGINE_CACHE["value"]


def _detect_engine_identity() -> str | None:
    binary = shutil.which("tesseract")
    if binary is None:
        return None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (completed.stdout or completed.stderr or "").splitlines()
    if not lines:
        return None
    tessdata_prefix = os.environ.get("TESSDATA_PREFIX", "")
    return f"{lines[0].strip()}|{tessdata_prefix}"


def _cache_key(
    image: object,
    *,
    config: str,
    lang: str | None,
    engine: str | None,
) -> str | None:
    """Hash the exact recognition inputs; ``None`` when the image is unusable."""

    try:
        mode = str(getattr(image, "mode"))
        width, height = (int(value) for value in getattr(image, "size"))
        digest = hashlib.sha256()
        digest.update(
            f"{mode}|{width}x{height}|{config}|{lang or ''}|{engine}".encode("utf-8")
        )
        digest.update(image.tobytes())
    except Exception:  # 非 PIL 图像（测试替身）或像素读取失败一律退化为不缓存。
        return None
    return digest.hexdigest()


def _cache_path(directory: Path, key: str) -> Path:
    """Shard cache files by the first two hex digits to keep directories small."""

    return directory / key[:2] / f"{key}.txt"


def _read_cached_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _write_cached_text(path: Path, text: str) -> None:
    """Write one entry atomically so concurrent runs never read a partial file."""

    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # 缓存不可写时静默降级为实时识别。
