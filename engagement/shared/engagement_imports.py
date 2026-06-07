"""Load engagement/lambdas modules when repo-root ``lambdas`` shadows the package."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

_ENG_ROOT = Path(__file__).resolve().parents[1]
_LOADED: dict[str, Any] = {}


def load_handler(relative_path: str, attr: str = "handler") -> Callable:
    """Load ``handler`` (or ``attr``) from ``engagement/<relative_path>.py``."""
    rel = relative_path.replace(".", "/")
    if not rel.endswith(".py"):
        rel = f"{rel}.py"
    path = _ENG_ROOT / rel
    if not path.is_file():
        raise ModuleNotFoundError(f"engagement handler not found: {path}")
    mod_name = f"_engagement_{rel.replace('/', '_').replace('.py', '')}"
    if mod_name not in _LOADED:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        _LOADED[mod_name] = mod
    mod = _LOADED[mod_name]
    fn = getattr(mod, attr, None)
    if not callable(fn):
        raise AttributeError(f"{path} has no callable {attr}")
    return fn
