# Image ColorMatcher 🌙

Color auto-balancer for photo series: matches a group of photos to a reference (one per location) so they look
like one scene — same color balance, tone and saturation. Adjustments are the familiar Photoshop ones, calibrated
to match Photoshop numerically: **Brightness/Contrast, Color Balance** (shadows / midtones / highlights ×
cyan-red, magenta-green, yellow-blue), **Hue/Saturation, Opacity**.

* **GUI** (PySide6): references always visible, manual and group editing, live preview, before/after,
  label masks with per-area layers, presets, workspaces, 17 themes, English / Russian interface,
  rebindable shortcuts.
* **Auto-balance:** a neutral-color eyedropper per tonal range + a solver over the real sliders; masks get their own
  layers matched to the same area of the reference.
* **CLI** `colormatch`: the same functionality for scripts and batch processing.

## Download and run (Windows)

1. Download `ColorMatcher-<version>-win64.zip` from [Releases](../../releases).
2. Unpack anywhere (no installation, no system Python needed).
3. Run **`ColorMatcher.exe`**. Command line: `colormatch.bat --help`.

Settings are stored in `%APPDATA%\colormatcher`. Language: Settings → General → Language.

## Run from source

Requires Python 3.12 (64-bit).

```bat
python -m pip install -r requirements.txt
python -m colormatcher.gui            :: GUI
python -m colormatcher.cli --help     :: CLI
python -m pytest                      :: tests
```

The Windows build uses an embedded Python in `python-embed\` next to the program (`ColorMatcher.exe`,
`colormatch.bat` and `colormatch-gui.bat` start it); `tools\make_release.py` builds the source release and
the zip archive.

### CLI examples

```bat
colormatch filters list
colormatch apply photo.jpg -s bc.contrast=20 -s cb.midtones.yb=-25 -s hs.saturation=10 -o out
colormatch preset save warm.cmpreset.json -s cb.highlights.yb=-15 -s cb.midtones.cr=8
colormatch apply a.jpg b.jpg c.jpg -p warm.cmpreset.json --opacity 70 -o out --naming version
colormatch ws new shoot.cmws && colormatch ws add shoot.cmws --dir D:\photos
```

## Documentation (Russian)

* [Stack and frameworks](docs/STACK.md) · [Architecture](docs/ARCHITECTURE.md)
* [Algorithms: filters, eyedropper, auto-balance](docs/ALGORITHM.md) · [Photoshop calibration](docs/CALIBRATION.md)
* [Masks and layers](docs/MASKS-DESIGN.md) · [Do we need neural networks](docs/ML-ANALYSIS.md)
* [UI design and shortcuts](docs/UI-DESIGN.md) · [Localization](docs/I18N.md) · [CLI](docs/CLI.md)
* [Data model](docs/ENTITIES.md) · [Roadmap](docs/ROADMAP.md) · [Changelog](CHANGELOG.md)

---

## Кратко по-русски

Авто-балансер цветов серии фото по референсам: фото выглядят как снятые в одной сцене. Фильтры совместимы с
Photoshop (Brightness/Contrast, Color Balance, Hue/Saturation, Opacity), авто-баланс по «пипетке» нейтралей,
маски-метки с отдельными слоями, GUI и CLI `colormatch`.

**Запуск на Windows:** скачайте `ColorMatcher-<версия>-win64.zip` из Releases, распакуйте в любую папку и
запустите `ColorMatcher.exe` — установка не нужна. Язык интерфейса: Настройки → Основные → Язык.
Документация — в папке `docs/` (на русском).
