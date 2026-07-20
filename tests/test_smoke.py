from importlib.metadata import version

import ctxlineage


def test_version():
    # __version__ tracks the installed package metadata, not a hardcoded string,
    # so it can never drift from what `pip install` gives.
    assert ctxlineage.__version__ == version("ctxlineage")
    assert ctxlineage.__version__ != "0.0.1.dev0"  # the old stale placeholder
