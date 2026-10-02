# Архитектура и выбор фреймворков

> Этап 0 (2026-09-29). Тикеты: T-0002 (фреймворки), T-0004 (план).

## 1. Принципы

1. **Ядро отдельно от GUI.** Вся обработка, анализ, авто-матчинг, пресеты и воркспейсы —
   в пакете `colormatcher.core` / `colormatcher.models` / `colormatcher.services`.
   GUI и CLI — два тонких «лица» над одним и тем же API. Всё, что умеет GUI, умеет CLI.
2. **Недеструктивность.** Оригиналы не трогаются. У каждого фото — стек параметров
   (`AdjustmentStack`), рендер выполняется только при превью/экспорте.
3. **Скорость через LUT.** Brightness/Contrast + Color Balance + Hue/Sat (+ будущие глобальные фильтры) — это
   поточечные цветовые преобразования. Стек «запекается» в 3D LUT (33³ или 65³),
   который применяется к картинке одной трилинейной интерполяцией. Превью на прокси
   (~1600px) — миллисекунды, пересчёт LUT при движении ползунка — единицы мс.
4. **Расширяемость фильтров.** Каждый фильтр — класс в реестре (`FilterRegistry`) с
   описанием параметров (имя, диапазон, дефолт, группа). GUI строит панель по схеме,
   CLI — ключи по схеме. Новый фильтр = один файл.
5. **Готовность к регионам.** Конвейер с самого начала рендерит «стек + маска»;
   глобальный стек — частный случай маски `≡ 1`. Это позволит позже (T-0032) красить
   участки/объекты отдельно и сшивать их мягкими масками без переделки ядра.
6. **Встроенный питон.** Всё работает на `python-embed` (3.12.8). `import site`
   выключен, но `Lib\site-packages` и корень проекта (`..`) уже в `python312._pth`.

## 2. Выбор фреймворков

| Задача | Выбор | Почему | Отклонённые варианты |
|---|---|---|---|
| GUI | **PySide6 (Qt 6)** | Нативная скорость, аппаратный рендер (`QGraphicsView`/`QOpenGLWidget`), QSS-скины, High-DPI и масштаб UI из коробки, полноценный буфер обмена (изображения + списки файлов), drag&drop, док-панели. LGPL. | DearPyGui (быстрый, но бедный буфер обмена/DnD, тяжело стилизовать «красиво»), Flet/NiceGUI (веб-стек, медленнее на больших картинках), Tkinter (нет скинов уровня Qt), PyQt6 (GPL) |
| Матрицы/пиксели | **NumPy** | База для всего | — |
| Быстрые операции с изображениями | **OpenCV (`opencv-python-headless`)** | Ресайз, преобразования в Lab/HSV, LUT, быстрее Pillow в разы. Headless — чтобы не конфликтовал Qt из OpenCV с PySide6 | scikit-image (медленнее) |
| Чтение/запись файлов | **Pillow** (+ `pillow-heif` опционально) | EXIF, ICC-профили, JPEG-качество, 16-bit PNG/TIFF через `tifffile` при необходимости | imageio |
| Ускорение горячих циклов | **numba** (с этапа 1) | Параллельное JIT-ядро применения 3D LUT: в ~100 раз быстрее NumPy (замеры ниже). Без numba работает запасной путь на NumPy | Cython (сборка на Windows сложнее) |
| Модели данных | **pydantic v2** | Валидация JSON воркспейсов/пресетов, версии схем, быстрый (ядро на Rust) | dataclasses + ручная валидация |
| CLI | **Typer** + **rich** | Подкоманды, автодоки `--help`, прогресс-бары, `--json` вывод | argparse (многословно), click (Typer поверх него) |
| Параллелизм | `concurrent.futures.ProcessPoolExecutor` (CLI), `QThreadPool` (GUI) | Пакетная обработка групп без блокировки UI | — |
| Нейросети (опционально) | **onnxruntime-gpu** | Один рантайм для сегментации/детекции лиц, RTX 5090 есть; без PyTorch в зависимостях | PyTorch (тяжёлый, ~3 ГБ) |
| Тесты | **pytest** | — | — |

Установка (тикет T-0005):
```bat
python-embed\python.exe -m pip install -r requirements.txt
```
Проверено 2026-09-29: PySide6 6.11.2, NumPy 2.5.3, numba 0.67, OpenCV 5.0, Pillow 12.3,
pydantic 2.13, Typer 0.27 на Python 3.12.8 (embed).

### Замеры скорости (этап 1, CPU, кадр 3168×1344 из demo-photos)

| Операция | Время |
|---|---|
| Запекание LUT 33³ (bc+cb+hs) | ~15 мс |
| Запекание LUT 65³ | ~160–190 мс |
| Применение LUT, прокси 1600px — NumPy | ~420 мс |
| Применение LUT, прокси 1600px — **numba** | **~3–4 мс** |
| Применение LUT, полный кадр 4.3 Мп — numba | ~13–15 мс |

Живое превью = запекание 33³ + применение ≈ 20 мс.

## 3. Структура проекта

```
image-colormatcher/
├─ colormatcher/
│  ├─ core/
│  │  ├─ imageio.py        # загрузка/сохранение, EXIF/ICC, 8/16 bit, прокси
│  │  ├─ colorspace.py     # sRGB<->linear<->Lab, HSL, luminance
│  │  ├─ filters/          # реестр фильтров
│  │  │  ├─ base.py        # Filter, ParamSpec, FilterRegistry
│  │  │  ├─ ps_tables.py   # таблицы совместимости с PS (ps_calibration.json, T-0034)
│  │  │  ├─ brightness_contrast.py
│  │  │  ├─ color_balance.py
│  │  │  ├─ hue_saturation.py
│  │  │  (opacity — не отдельный фильтр, а смешение в pipeline.py)
│  │  ├─ pipeline.py       # AdjustmentStack -> LUT -> рендер
│  │  ├─ lut.py            # запекание и применение 3D LUT
│  │  ├─ analysis.py       # «пипетка»: выборка, ColorProfile, усреднение референсов
│  │  └─ matcher.py        # авто-баланс: решатель параметров
│  ├─ models/
│  │  ├─ stack.py          # FilterState, AdjustmentStack
│  │  ├─ preset.py         # Preset
│  │  ├─ workspace.py      # Workspace, Scene, PhotoItem, RefItem, ExportSettings (схема v2)
│  │  └─ profile.py        # ColorProfile, ProfileOffset, AnchorPair, MatchSettings
│  ├─ services/
│  │  ├─ naming.py         # overwrite / suffix / version (_v001)
│  │  ├─ workspace_io.py   # new/save/load/pack/relink (.bak, миграции схемы)
│  │  ├─ importer.py       # папки с подпапками -> сцены
│  │  ├─ matching.py       # воркер авто-баланса, параллельный запуск, match_workspace
│  │  └─ export.py         # план экспорта (коллизии имён, версии), параллельный рендер
│  ├─ ml/                  # опционально: сегментация, лица (onnxruntime)
│  ├─ cli/                 # Typer-приложение `colormatch` (python -m colormatcher.cli)
│  │  ├─ main.py           # filters, apply, preset
│  │  ├─ match_cmds.py     # analyze, compare, match
│  │  ├─ ws_cmds.py        # ws …
│  │  └─ common.py         # разбор --set / --limit
│  └─ gui/                 # PySide6 (python -m colormatcher.gui)
│     ├─ app.py            # запуск: настройки → масштаб → тема → окно
│     ├─ main_window.py    # раскладка, превью, действия, DnD, буфер, файлы, якоря, автосохранение
│     ├─ state.py          # AppState: воркспейс, выделение, Undo/Redo
│     ├─ imagecache.py     # фоновая загрузка, прокси, миниатюры с правками
│     ├─ theme.py          # темы: JSON-палитры + QSS-шаблон, ThemeHolder
│     ├─ settings.py       # AppSettings (%APPDATA%/colormatcher/settings.json)
│     ├─ dialogs.py        # экспорт, настройки
│     ├─ tasks.py          # фоновые задачи
│     ├─ qtutil.py         # numpy↔QImage, Lab→цвет, фоны
│     ├─ widgets/          # viewer, slider (ParamRow), photolist (лента, референсы, дерево)
│     ├─ panels/           # adjustments, match_panel, presets_panel
│     └─ themes/           # 17 палитр *.json
├─ tests/
├─ docs/
├─ .cuteide/tickets/
├─ colormatch.bat          # CLI через python-embed
└─ colormatch-gui.bat      # GUI через python-embed
```

## 4. Поток данных

```
файлы ──importer──> Workspace(Scenes, PhotoItems)
                        │
          PhotoItem.stack (AdjustmentStack: [BrightContrast, ColorBalance, HueSat, ...], opacity)
                        │
   stats.profile(proxy(photo) after stack) ──> ColorProfile
                        │
   референс фото (запомненный или активный): его ColorProfile после его стека + смещение цели
                        │
   matcher.solve(target_proxy, ref_profile, anchors) ──> новые параметры stack
                        │
   pipeline.bake_lut(stack) ──> apply(LUT, full-res) ──> blend(opacity) ──> export
```

Подробности математики — [ALGORITHM.md](ALGORITHM.md). Сущности — [ENTITIES.md](ENTITIES.md).
