"""Имена выходных файлов: overwrite / suffix / version (_v001 и инкремент).

`version`: `photo.jpg` -> `photo_v001.jpg` (или следующий свободный номер в папке);
`photo_v003.jpg` -> `photo_v004.jpg` (или дальше, если занято).
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

_VERSION_RE = re.compile(r"^(?P<base>.*)_v(?P<num>\d+)$")


class Naming(str, Enum):
    overwrite = "overwrite"
    suffix = "suffix"
    version = "version"


def split_version(stem: str) -> tuple[str, int]:
    m = _VERSION_RE.match(stem)
    return (m["base"], int(m["num"])) if m else (stem, 0)


def next_version_path(out_dir: Path, stem: str, ext: str, digits: int = 3) -> Path:
    base, current = split_version(stem)
    taken = set()
    if out_dir.is_dir():
        for p in out_dir.iterdir():
            b, n = split_version(p.stem)
            if b == base and n:
                taken.add(n)
    n = max([current, *taken]) + 1
    return out_dir / f"{base}_v{n:0{digits}d}{ext}"


def output_path(
    src: Path,
    out_dir: Path | None,
    naming: Naming = Naming.version,
    *,
    suffix: str = "_cm",
    ext: str | None = None,
) -> Path:
    out_dir = Path(out_dir) if out_dir else src.parent
    ext = ext or src.suffix
    if naming is Naming.overwrite:
        return out_dir / f"{src.stem}{ext}"
    if naming is Naming.suffix:
        return out_dir / f"{src.stem}{suffix}{ext}"
    return next_version_path(out_dir, src.stem, ext)
