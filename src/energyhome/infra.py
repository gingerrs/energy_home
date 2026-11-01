"""Explicit logging setup and synchronous infrastructure decorators."""

from __future__ import annotations

import functools
import logging
import os
import sys
from collections.abc import Callable
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

def cache_profile(func: Callable[P, R]) -> Callable[P, R]:
    """
    Cache profile/data based on path + mtime + source_config.
    Does not cache raw single-pass iterators as ready records.
    """
    cache: dict[tuple[str, float, Any], Any] = {}

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        path_val = None
        for arg in args:
            if isinstance(arg, (str, Path)):
                path_val = Path(arg)
                break
        if not path_val and "path" in kwargs:
            path_val = Path(kwargs["path"])
        elif not path_val and "input_path" in kwargs:
            path_val = Path(kwargs["input_path"])
        if not path_val or not path_val.exists():
            return func(*args, **kwargs)

        try:
            mtime = path_val.stat().st_mtime
        except OSError:
            return func(*args, **kwargs)

        source_config = kwargs.get("tariff") or kwargs.get("config") or "default"
        cache_key = (str(path_val.resolve()), mtime, source_config)

        if cache_key in cache:
            return cache[cache_key]

        result = func(*args, **kwargs)
        cache[cache_key] = result
        return result

    return wrapped


def log_call(func: Callable[P, R]) -> Callable[P, R]:
    """
    Log function call execution, saving result or exception.
    Does not swallow user errors/exceptions.
    """
    logger = logging.getLogger("energyhome.calls")

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        logger.debug("Calling %s with args=%s kwargs=%s", func.__qualname__, args, kwargs)
        try:
            result = func(*args, **kwargs)
            logger.debug("%s completed successfully", func.__qualname__)
            return result
        except Exception as e:
            logger.error("%s raised %s: %s", func.__qualname__, type(e).__name__, e)
            raise

    return wrapped