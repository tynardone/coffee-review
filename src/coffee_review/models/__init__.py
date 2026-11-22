"""The contracts between pipeline stages, one module per stage.

Every model is frozen (immutable once built) and every datetime is
timezone-aware UTC, so values can be passed between stages without surprises.
"""
