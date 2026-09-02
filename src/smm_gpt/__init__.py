"""SMM GPT application package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("smm-gpt")
except PackageNotFoundError:  # pragma: no cover - only relevant outside an installed checkout
    __version__ = "0.0.0"

__all__ = ["__version__"]
