"""Experimental memoriesQL public contract-catalog package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("memoriesql")
except PackageNotFoundError:
    __version__ = "0.0.5"

__all__ = ["__version__"]
