"""Сверка снимков из Photoshop с нашими фильтрами (T-0034).

Для каждого PNG из calibration/ps/ (или ps/_trial/ с --trial) находит задание в ps_jobs.jsx,
применяет НАШ фильтр с теми же значениями к hald_8.png и печатает:
  * бит — глубина файла из PS (ждём 16);
  * PS Δ — насколько PS вообще изменил картинку (средний ΔE76 к исходной HALD):
           ≈0 значит настройка не применилась — ошибка скрипта;
  * наш ΔE — среднее и 95-й перцентиль ΔE76 между PS и нашим фильтром.

Запуск: python-embed\\python.exe tools\\compare_ps.py [--trial]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colormatcher.core import imageio  # noqa: E402
from colormatcher.core.colorspace import delta_e76, srgb_to_lab  # noqa: E402
from colormatcher.core.pipeline import render_direct  # noqa: E402
from colormatcher.models import AdjustmentStack  # noqa: E402

CALIB = ROOT / "calibration"


def load_jobs() -> dict[str, dict]:
    text = (CALIB / "ps_jobs.jsx").read_text(encoding="utf-8-sig")
    data = re.search(r"var CALIB_JOBS = (\[.*\]);", text, re.S)[1]
    return {j["file"]: j for j in json.loads(data)}


def stack_for(job: dict) -> AdjustmentStack:
    s = AdjustmentStack()
    if job["kind"] == "cb":
        for rng in ("shadows", "midtones", "highlights"):
            for axis, v in zip(("cr", "mg", "yb"), job[rng]):
                s.set(f"cb.{rng}.{axis}", v)
        s.set("cb.preserve_luminosity", job["pl"])
    elif job["kind"] == "bc":
        s.update({"bc.brightness": job["b"], "bc.contrast": job["c"], "bc.legacy": job["legacy"]})
    elif job["kind"] == "hs":
        s.update({"hs.hue": job["h"], "hs.saturation": job["s"], "hs.lightness": job["l"]})
    s.opacity = job["opacity"]
    return s


def main() -> None:
    trial = "--trial" in sys.argv
    folder = CALIB / "ps" / ("_trial" if trial else "")
    jobs = load_jobs()
    hald = imageio.load_image(CALIB / "hald_8.png").rgb
    hald_lab = srgb_to_lab(hald)

    files = sorted(p for p in folder.glob("*.png") if p.name in jobs)
    if not files:
        print(f"нет снимков в {folder}")
        return
    print(f"{'файл':<58} {'бит':>3} {'PS Δ':>6} {'наш ΔE ср':>9} {'p95':>6}")
    means = []
    for path in files:
        ps = imageio.load_image(path)
        if ps.rgb.shape != hald.shape:
            print(f"{path.name:<58} размер {ps.rgb.shape} ≠ {hald.shape}")
            continue
        ps_lab = srgb_to_lab(ps.rgb)
        ours_lab = srgb_to_lab(render_direct(hald, stack_for(jobs[path.name])))
        ps_change = float(delta_e76(ps_lab, hald_lab).mean())
        de = delta_e76(ps_lab, ours_lab)
        means.append(float(de.mean()))
        flag = "  ← PS ничего не изменил?" if ps_change < 0.2 else ""
        print(f"{path.name:<58} {ps.bit_depth:>3} {ps_change:>6.2f} {de.mean():>9.2f} {np.percentile(de, 95):>6.2f}{flag}")
    print(f"\nфайлов: {len(means)}, средний ΔE по всем: {np.mean(means):.2f}")


if __name__ == "__main__":
    main()
