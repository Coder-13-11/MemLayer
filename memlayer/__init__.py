"""Compatibility shim: the package was renamed to ``nexus``. Existing local MCP and dashboard configurations
that run ``python -m memlayer.server`` or ``python -m memlayer.web`` keep working through these modules."""

from nexus import __version__  # noqa: F401
