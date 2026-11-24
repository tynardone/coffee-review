"""The shape of a value: what kind of characters it's made of, and in what order.

Used by the parse report to show the formats each field comes in, so the clean
stage can be designed from real data.

Every digit becomes ``9``, so the number structure shows (``$99.99`` against
``$9.99``). Each run of letters becomes a single ``a``, because the length of a
word rarely matters: ``November 2011`` and ``June 2003`` are the same format,
``a 9999``. Everything else (punctuation, spaces, symbols) stays as it is.

- ``$34.00/12 ounces`` → ``$99.99/99 a``
- ``58/80`` → ``99/99``
- ``Antigua, Guatemala`` → ``a, a``
"""

from __future__ import annotations


def value_shape(text: str) -> str:
    """Digits become ``9``; each run of letters becomes one ``a``."""
    shape: list[str] = []
    for char in text:
        if char.isdigit():
            shape.append("9")
        elif char.isalpha():
            if not shape or shape[-1] != "a":
                shape.append("a")
        else:
            shape.append(char)
    return "".join(shape)
