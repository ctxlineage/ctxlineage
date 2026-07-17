"""SDK instrumentation orchestration. Each provider module is best-effort."""

from __future__ import annotations

import threading

from ctxlineage._instrument import anthropic_patch, openai_patch

_installed_providers: list[str] | None = None
# Guards check-then-act on _installed_providers so two concurrent init() calls
# cannot both run the patch step and double-wrap every SDK method.
_install_lock = threading.Lock()


def install() -> list[str]:
    """Patch every available SDK once per process. Returns installed provider names."""
    global _installed_providers
    if _installed_providers is not None:
        return _installed_providers
    with _install_lock:
        if _installed_providers is not None:  # another thread won the race
            return _installed_providers
        providers = []
        if openai_patch.install():
            providers.append("openai")
        if anthropic_patch.install():
            providers.append("anthropic")
        _installed_providers = providers
        return providers
