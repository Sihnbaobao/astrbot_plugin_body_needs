"""Tiny stand-in for the AstrBot runtime, used only when the real one is absent.

The plugin imports a handful of AstrBot names at module import time. Running the
test suite on a checkout outside an AstrBot install would otherwise fail during
collection, so conftest.py puts this package on sys.path only when
`import astrbot` does not resolve. Never shipped with the plugin.
"""
