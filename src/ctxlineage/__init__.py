from importlib.metadata import PackageNotFoundError, version

from ctxlineage._span import span
from ctxlineage._state import init

__all__ = ["init", "span", "__version__"]

try:
    # Single source of truth: the installed package metadata (pyproject version),
    # so __version__ can never drift from what `pip install` actually gives.
    __version__ = version("ctxlineage")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
