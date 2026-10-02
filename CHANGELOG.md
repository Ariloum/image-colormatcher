# Changelog

Versions follow [Semantic Versioning](https://semver.org/). The version lives in `colormatcher/__init__.py`.

## 0.1.0 — first public release

**Core**
* Photoshop-calibrated filters: Brightness/Contrast (incl. legacy), Color Balance (shadows / midtones /
  highlights, preserve luminosity), Hue/Saturation, Opacity — mean ΔE ≈ 0.03 vs Photoshop 25.6.
* Adjustment stack baked into a 3D LUT (33³ preview, 65³ export), JIT-compiled LUT kernel (Numba).
* Eyedropper profile (neutrals per tonal range, tile voting, Lab) and auto-balance solver
  (Levenberg–Marquardt over the real sliders, L1 sparsity, limits, integer results).

**Workspaces**
* Folders with subfolders, files, clipboard paste; several references with one active (per location);
  anchors (eyedropper pairs); presets; export with the same names or versioned names; pack a workspace with all
  photos; autosave and reopen the last workspace.

**Masks**
* Label masks painted with a soft brush (7 colors + "ignore" / "protect"), palette presets.
* Per-label adjustment layers and a "Background" layer, soft-mask compositing in preview, thumbnails and export.
* Auto-balance per label: the area's color distribution is matched to the same label on the reference.

**GUI**
* PySide6 interface: reference strip always visible, single / group editing, before/after split, hold-to-peek
  (before / reference), 17 themes, UI scale and fonts, English and Russian with live switching, settings in tabs,
  rebindable shortcuts.

**CLI**
* `colormatch`: filters, apply, presets, analyze / compare / match, workspaces (`ws …`), export, pack;
  `--version`.

**Windows build**
* `ColorMatcher-<version>-win64.zip`: unpack and run `ColorMatcher.exe` — embedded Python included.
