"""Helpers shared by tool modules."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any


def safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn exceptions into ``ERROR: ...`` strings so the model can recover instead of crashing
    the whole graph run."""

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                return f"ERROR: {type(e).__name__}: {e}"

        return awrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"

    return wrapper
