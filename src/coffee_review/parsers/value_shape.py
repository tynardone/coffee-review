"""The shape of a value: what kind of characters it's made of, and in what order.

Used by the parse report to show the formats each field comes in, so the clean
stage (M3) can be designed from real data: ``$34.00/12 ounces`` has the shape
``$99.99/99 aaaaaa``, and ``NA`` has the shape ``aa``.
"""

from __future__ import annotations


def value_shape(text: str) -> str:
    """Every digit becomes ``9`` and every letter ``a``; everything else stays."""
    return "".join(
        "9" if char.isdigit() else "a" if char.isalpha() else char for char in text
    )
