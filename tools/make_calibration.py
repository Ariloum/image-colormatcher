"""Генератор набора для калибровки фильтров под Photoshop (T-0034).

Создаёт в calibration/:
  * hald_8.png   — HALD-картинка уровня 8 (512×512, 16 бит): все цвета сетки 64³.
                   Порядок пикселей: индекс i = r + g·64 + b·64², x = i % 512, y = i // 512.
  * list.csv     — список настроек, которые нужно применить в Photoshop, и имя выходного файла.
  * ps_jobs.jsx  — тот же список как данные для скрипта Photoshop tools/ps_calibrate.jsx.

Запуск: python-embed\\python.exe tools\\make_calibration.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from colormatcher.core import imageio

ROOT = Path(__file__).resolve().parent.parent / "calibration"
LEVEL = 8
N = LEVEL * LEVEL          # 64 уровня на канал
SIDE = LEVEL ** 3          # 512 px


def hald_image() -> np.ndarray:
    i = np.arange(SIDE * SIDE)
    r, g, b = i % N, (i // N) % N, i // (N * N)
    rgb = np.stack([r, g, b], axis=-1).astype(np.float32) / (N - 1)
    return rgb.reshape(SIDE, SIDE, 3)


def _v(x: int) -> str:
    return f"{x:+d}" if x else "0"


def settings() -> list[dict]:
    rows: list[dict] = []

    def add(group: str, params: dict, prio: int, note: str = "", name: str = "") -> None:
        if not name:
            name = group + "__" + "__".join(
                f"{k}-{v}" if isinstance(v, str) else f"{k}{_v(v)}" for k, v in params.items()
            )
        rows.append({"file": name + ".png", "group": group, "params": params, "prio": prio, "note": note})

    # Color Balance: каждый из 9 ползунков отдельно, Preserve Luminosity ВКЛ
    for rng in ("shadows", "midtones", "highlights"):
        for axis in ("cr", "mg", "yb"):
            for v in (-100, -50, 50, 100):
                add("cb", {f"{rng}.{axis}": v, "pl": "on"}, 1 if abs(v) == 100 else 2)
    # Preserve Luminosity ВЫКЛ — чтобы отделить сам сдвиг от восстановления яркости
    for rng in ("shadows", "midtones", "highlights"):
        for v in (-100, 100):
            add("cb", {f"{rng}.cr": v, "pl": "off"}, 1)
        add("cb", {f"{rng}.yb": 100, "pl": "off"}, 2)
    # Сочетания — проверка, что эффекты складываются
    add("cb", {"shadows.cr": 60, "midtones.mg": 30, "highlights.yb": -40, "pl": "on"}, 1, "сочетание")
    add("cb", {"shadows.yb": -50, "midtones.cr": -30, "highlights.mg": 45, "pl": "off"}, 2, "сочетание")

    # Brightness/Contrast (НЕ Legacy)
    for v in (-150, -100, -50, -25, 25, 50, 100, 150):
        add("bc", {"b": v, "c": 0}, 1 if abs(v) in (50, 150) else 2)
    for v in (-50, -25, 25, 50, 75, 100):
        add("bc", {"b": 0, "c": v}, 1 if v in (-50, 50, 100) else 2)
    for b, c in ((50, 50), (-50, 50), (100, -30)):
        add("bc", {"b": b, "c": c}, 1 if (b, c) == (50, 50) else 2, "сочетание")
    # Legacy
    for b, c in ((100, 0), (-100, 0), (0, 50), (0, -50)):
        add("bcl", {"b": b, "c": c}, 2, "Use Legacy ВКЛ")

    # Hue/Saturation (Master, Colorize ВЫКЛ)
    for v in (-90, -30, 30, 90, 180):
        add("hs", {"h": v, "s": 0, "l": 0}, 1 if v == 90 else 2)
    for v in (-100, -75, -50, -25, 25, 50, 75, 100):
        add("hs", {"h": 0, "s": v, "l": 0}, 1 if abs(v) in (50, 100) else 2)
    for v in (-100, -75, -50, -25, 25, 50, 75, 100):
        add("hs", {"h": 0, "s": 0, "l": v}, 1 if abs(v) == 50 else 2)
    for h, s, l in ((30, 40, 0), (0, 50, -30)):
        add("hs", {"h": h, "s": s, "l": l}, 2, "сочетание")

    # Opacity: одно контрольное — корректирующий слой с непрозрачностью 50%
    add("op", {"shadows.cr": 60, "midtones.mg": 30, "highlights.yb": -40, "pl": "on", "opacity": 50}, 1,
        "как первое сочетание CB, но непрозрачность слоя 50%", name="op__cb-combo__50")
    return rows


# Контрольные файлы пробного прогона скрипта Photoshop (по одному на каждый вид слоя)
TRIAL = {"cb__shadows.cr+100__pl-on.png", "cb__shadows.cr+100__pl-off.png",
         "bc__b+50__c0.png", "hs__h0__s+50__l0.png", "op__cb-combo__50.png"}


def ps_job(row: dict) -> dict:
    """Строка списка -> задание для tools/ps_calibrate.jsx (структурированные значения)."""
    p, g = row["params"], row["group"]
    job = {"file": row["file"], "prio": row["prio"], "trial": row["file"] in TRIAL,
           "opacity": int(p.get("opacity", 100))}
    if g in ("cb", "op"):
        job["kind"] = "cb"
        for rng in ("shadows", "midtones", "highlights"):
            job[rng] = [int(p.get(f"{rng}.{a}", 0)) for a in ("cr", "mg", "yb")]
        job["pl"] = p.get("pl", "on") == "on"
    elif g in ("bc", "bcl"):
        job.update(kind="bc", b=int(p["b"]), c=int(p["c"]), legacy=g == "bcl")
    elif g == "hs":
        job.update(kind="hs", h=int(p["h"]), s=int(p["s"]), l=int(p["l"]))
    else:
        raise ValueError(f"неизвестная группа {g}")
    return job


def main() -> None:
    ROOT.mkdir(exist_ok=True)
    imageio.save_image(ROOT / "hald_8.png", hald_image(), bit_depth=16)
    with open(ROOT / "list.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["priority", "file", "group", "params", "note"])
        rows = settings()
        for row in rows:
            params = "; ".join(f"{k}={v}" for k, v in row["params"].items())
            w.writerow([row["prio"], row["file"], row["group"], params, row["note"]])
    jobs = [ps_job(r) for r in rows]
    assert sum(j["trial"] for j in jobs) == len(TRIAL), "контрольный файл не найден в списке"
    (ROOT / "ps_jobs.jsx").write_text(
        "// Сгенерировано tools/make_calibration.py — не править руками.\n"
        f"var CALIB_JOBS = {json.dumps(jobs, ensure_ascii=True, indent=1)};\nCALIB_JOBS;\n",
        encoding="utf-8-sig",  # BOM: ExtendScript без него может читать как ANSI
    )
    p1 = sum(r["prio"] == 1 for r in rows)
    print(f"{len(rows)} настроек (приоритет 1: {p1}) → {ROOT}")


if __name__ == "__main__":
    main()
