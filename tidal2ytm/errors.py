from __future__ import annotations


class Tidal2YtmError(Exception):
    """Base for all tidal2ytm errors."""


class PlanNotFoundError(Tidal2YtmError, FileNotFoundError):
    """Raised when data/transfer_plan.toml is missing."""


class InvalidScopeError(Tidal2YtmError, ValueError):
    """Raised for bad/ambiguous transfer scope."""
