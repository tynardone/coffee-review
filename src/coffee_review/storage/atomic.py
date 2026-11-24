"""Writing a file so that readers never see half of it.

Shared by every store under ``storage/``: write to a temp file in the same
folder, then move it into place in one step.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    import polars as pl


def write_atomically(path: Path, write: Callable[[Path], object]) -> None:
    """Write to a temp file in the same folder, then move it into place.

    A failed write leaves the previous file untouched and no temp file behind.

    Args:
        path: Where the file ends up.
        write: Writes the content to the temp path it's given.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(handle)  # reopened by ``write``; mkstemp only reserves a unique name
    tmp = Path(name)
    try:
        write(tmp)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_parquet(frame: pl.DataFrame, path: Path) -> None:
    """Write a frame as a Parquet file, atomically.

    Args:
        frame: The table to write.
        path: Where the file ends up.
    """
    write_atomically(path, frame.write_parquet)
