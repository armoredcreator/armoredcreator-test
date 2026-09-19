from __future__ import annotations
import importlib
import os
from dataclasses import dataclass
from typing import Any, Callable

class RuntimeConfigurationError(RuntimeError):
    pass

def load_symbol(spec: str) -> Any:
    if ":" not in spec:
        raise RuntimeConfigurationError(f"invalid adapter spec: {spec!r}; expected package.module:Symbol")
    module_name, symbol_name = spec.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        return getattr(module, symbol_name)
    except Exception as exc:
        raise RuntimeConfigurationError(f"cannot load adapter {spec!r}: {exc}") from exc

def load_factory(env_name: str) -> Callable[..., Any]:
    spec = os.getenv(env_name)
    if not spec:
        raise RuntimeConfigurationError(f"missing required environment variable {env_name}")
    obj = load_symbol(spec)
    if not callable(obj):
        raise RuntimeConfigurationError(f"{env_name} must point to a callable factory")
    return obj

@dataclass(frozen=True)
class ProductionBindings:
    vision: Any
    studio: Any
    publisher: Any
    source: Any

def build_production_bindings(**kwargs: Any) -> ProductionBindings:
    factories = {
        "vision": "ARMORED_VISION_FACTORY",
        "studio": "ARMORED_STUDIO_FACTORY",
        "publisher": "ARMORED_HUB_FACTORY",
        "source": "ARMORED_SYNC_FACTORY",
    }
    built = {}
    for name, env_name in factories.items():
        factory = load_factory(env_name)
        built[name] = factory(**kwargs)
    return ProductionBindings(**built)
