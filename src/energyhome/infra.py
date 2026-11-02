"""Explicit logging setup and synchronous infrastructure decorators."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from functools import wraps
from inspect import Parameter, signature
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def configure_logging(level: int | str = logging.INFO) -> logging.Logger:
    """Configure one EnergyHome stderr handler; leave root logging untouched."""
    logger = logging.getLogger("energyhome")
    logger.setLevel(level)
    handler = next((h for h in logger.handlers if h.name == "energyhome.console"), None)

    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name("energyhome.console")
        logger.addHandler(handler)
    elif getattr(handler.stream, "closed", False):
        handler.stream = sys.stderr
    else:
        handler.setStream(sys.stderr)

    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.propagate = False
    return logger


def timed(func: Callable[P, R]) -> Callable[P, R]:
    """Log elapsed seconds at DEBUG, preserving the result or original exception."""
    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        start = perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            logging.getLogger("energyhome.timing").debug(
                "%s.%s took %.6f s", func.__module__, func.__qualname__,
                perf_counter() - start
            )
    return wrapped


def validate_positive(parameter: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Validate a named finite int/float > 0; reject bool and invalid values with ValueError."""
    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        parameters = signature(func)
        if parameter not in parameters.parameters:
            raise ValueError(f"Unknown parameter: {parameter}")
        kind = parameters.parameters[parameter].kind
        if kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD):
            raise ValueError("Select a single parameter, not *args or **kwargs")

        @wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = parameters.bind(*args, **kwargs)
            bound.apply_defaults()
            value = bound.arguments[parameter]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or value <= 0
                    or (isinstance(value, float) and not isfinite(value))):
                raise ValueError(f"{parameter} must be a finite positive number")
            return func(*args, **kwargs)

        return wrapped
    return decorate

def _cache_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return type(value), value.isoformat(), str(value.tzinfo), value.fold
    if is_dataclass(value) and not isinstance(value, type):
        members = tuple((f.name, _cache_value(getattr(value, f.name))) for f in fields(value))
        return type(value), members
    if isinstance(value, Mapping):
        return type(value), frozenset((_cache_value(k), _cache_value(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return type(value), tuple(_cache_value(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return type(value), frozenset(_cache_value(v) for v in value)
    hash(value)
    return type(value), value


def cache_profile(func: Callable[P, R]) -> Callable[P, R]:
    """Cache reusable results by file version and bound arguments; skip iterators."""
    parameters = signature(func)
    cache: dict[Path, tuple[object, R]] = {}

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = parameters.bind(*args, **kwargs)
        bound.apply_defaults()
        path_arg = bound.arguments.get('path', bound.arguments.get('input_path'))
        if path_arg is None:
            path_arg = next(iter(bound.arguments.values()), None)
        if not isinstance(path_arg, (str, Path)):
            return func(*args, **kwargs)
        try:
            path = Path(path_arg).resolve()
            stat = path.stat()
        except OSError:
            return func(*args, **kwargs)
        try:
            arguments = _cache_value(bound.arguments)
        except TypeError:
            return func(*args, **kwargs)
        key = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, arguments)
        cached = cache.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        result = func(*args, **kwargs)
        if not isinstance(result, Iterator):
            cache[path] = (key, result)
        return result

    return wrapped


def log_call(func: Callable[P, R]) -> Callable[P, R]:
    """Log calls at DEBUG and preserve returned values, exceptions and metadata."""
    logger = logging.getLogger("energyhome.calls")

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        logger.debug("Calling %s with args=%s kwargs=%s", func.__qualname__, args, kwargs)
        try:
            result = func(*args, **kwargs)
            logger.debug("%s completed successfully", func.__qualname__)
            return result
        except Exception as e:
            logger.debug("%s raised %s: %s", func.__qualname__, type(e).__name__, e)
            raise

    return wrapped
