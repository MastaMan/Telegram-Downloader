"""Telegram File Hunter."""

# Resolve this standard-library lazy import before Qt loads its import hooks.
# Python 3.15 otherwise exposes a lazy_import object to asyncio's default pool.
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
__version__ = "1.0.11"
