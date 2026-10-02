# Сущности (модель данных)

> Живой документ: обновлять при каждом изменении моделей. Тикет: T-0030.

## Статус реализации

| Сущность | Статус | Где |
|---|---|---|
| ParamSpec, Filter, FilterRegistry | ✅ этап 1 | `colormatcher/core/filters/base.py` |
| Фильтры BrightnessContrast (`bc`), ColorBalance (`cb`), HueSaturation (`hs`) | ✅ этап 1 | `colormatcher/core/filters/*.py` |
| FilterState, AdjustmentStack | ✅ этап 1 | `colormatcher/models/stack.py` |
| Preset | ✅ этап 1 | `colormatcher/models/preset.py` |
| LoadedImage | ✅ этап 1 | `colormatcher/core/imageio.py` |
| Naming (overwrite/suffix/version) | ✅ этап 1 (часть T-0020) | `colormatcher/services/naming.py` |
| ColorProfile, BandStats, ProfileOffset, Region, AnchorPair, MatchSettings | ✅ этап 2 | `colormatcher/models/profile.py` |
| PixelSample (выборка пипетки), Anchor (якорь решателя), MatchResult | ✅ этап 2 | `core/analysis.py`, `core/matcher.py` |
| Workspace, Scene, PhotoItem, RefItem, ExportSettings | ✅ этап 3 (референсы — схема v2, T-0050) | `colormatcher/models/workspace.py`; сервисы — `services/` |
| RegionLayer, MaskSpec | проект | этап 7 (T-0032/T-0033); рендер «стек + маска» уже есть — `core/pipeline.py` |

Всё ниже, помеченное «проект», — ещё не код.

## LoadedImage (этап 1)

`rgb` (H, W, 3) float32 [0,1]; `alpha` (H, W) или `None`; `bit_depth` 8/16; `exif`
(байты, ориентация сброшена в 1 — пиксели уже повёрнуты); `icc`; `format`; `path`.
8-бит читает Pillow, 16-бит PNG/TIFF — OpenCV (пути с кириллицей поддержаны).

Все сущности — pydantic v2, сериализуются в JSON с полем `schema_version`.

## Workspace — файл `*.cmws` (JSON) — этап 3

Методы: `abs_path/rel_path`, `rebase(new_root)` (пересчёт путей при «сохранить как»),
`all_photos`, `scene(name, create)`, `locate(id)`, `find(id|путь|имя)` (имя — если однозначно),
`add_photo` (дубликаты пропускаются), `remove_photos` (чистит и наборы референсов),
`remove_scene`, `missing`; референсы — `ref_ids`, `ref_item`, `add_ref`, `remove_ref`, `set_active`, `ref_for(photo)` (см. RefItem).
Сервисы: `services/workspace_io.py` (new/save/load/pack/relink; save — атомарно + `.cmws.bak`;
load — миграции по `schema_version`, схема новее поддерживаемой — ошибка), `services/importer.py`
(подпапки = сцены, «естественная» сортировка), `services/matching.py` (`match_workspace`),
`services/export.py` (`plan_export` / `export`).

| Поле | Тип | Описание |
|---|---|---|
| `schema_version` | int | Версия схемы для миграций |
| `name` | str | Имя воркспейса |
| (корень) | — | Не хранится: это папка файла `.cmws`. Пути фото — **относительно** неё (posix), если тот же диск, иначе абсолютные |
| `scenes` | list[Scene] | Сцены (обычно = подпапки) |
| `refs` | list[RefItem] | Все референсы проекта (обычно по одному на локацию) — без усреднения (T-0050) |
| `active_ref` | str \| null | Активный референс: его применяет авто-баланс выделенных |
| `settings` | MatchSettings | Режим матчинга, веса, что трогать |
| `export` | ExportSettings | Папка, формат, именование |

## Scene

| Поле | Тип | Описание |
|---|---|---|
| `id` | str | Уникальный id |
| `name` | str | Имя (относительный путь подпапки или своё) |
| `photos` | list[PhotoItem] | Фото сцены |

## PhotoItem

| Поле | Тип | Описание |
|---|---|---|
| `id` | str | Уникальный id |
| `path` | str | Путь к исходнику |
| `stack` | AdjustmentStack | Параметры коррекции |
| `anchors` | list[AnchorPair] | Пары пипеток этого фото (к своему референсу) |
| `ref_id` | str \| null | Референс, по которому фото подбиралось (T-0050) |
| `regions` | list[RegionLayer] | Локальные слои по участкам (продвинутый этап T-0032; поле зарезервировано, пока `[]`) |
| (маска) | файл | Маска-метки фото — не в JSON, а `<воркспейс>.masks/<id>.npz` (см. «Маска-метки») |
| `layers` | dict[int, AdjustmentStack] | Слои меток 0…6: правки под маской поверх общего `stack` (T-0061); 9 (`masks.BG_LAYER`) — «Фон»: всё вне меток (T-0082); `active_layers()` — непустые |
| `note` | str | Заметка |
| (кеш) | — | Кеш профиля/масок/глубины — позже, в GUI (в файле не хранится) |

## AdjustmentStack

| Поле | Тип | Описание |
|---|---|---|
| `filters` | list[FilterState] | Упорядоченные фильтры; `{"type": "color_balance", "enabled": true, "params": {...}}`. Порядок по умолчанию: `brightness_contrast` → `color_balance` → `hue_saturation` |
| `opacity` | float 0–100 | Сила всего стека (смешение с оригиналом) |

Методы: `get/set(full_key)` (`cb.shadows.cr`, `opacity`; значения зажимаются в диапазон),
`update(dict)`, `flat()`, `groups()`, `copy_groups_from(other, groups)` (частичная вставка),
`is_identity()`. `FilterState.params` при валидации нормализуется реестром (дефолты + clamp).
Отсутствующий фильтр вставляется на своё место по `DEFAULT_ORDER`.

## RegionLayer (продвинутый этап, T-0032 — проект)

| Поле | Тип | Описание |
|---|---|---|
| `id`, `name` | str | Например «Небо» |
| `mask` | MaskSpec | Описание маски (не пиксели): список источников + операции + растушёвка |
| `stack` | AdjustmentStack | Свои фильтры региона |
| `opacity` | float 0–100 | Сила слоя |
| `match_class` | str \| null | Класс для авто-матча по регионам (`sky`, `location`, …) |

**MaskSpec** — `sources: [{kind: linear\|radial\|brush\|luma_range\|color_range\|semantic\|sam\|depth_range, params, op: add\|subtract\|intersect}]`,
`invert`, `feather`, `edge_aware` (guided filter). Растр маски — в кеше, пересчитывается по спецификации.

## Filter / ParamSpec / FilterRegistry

* `Filter` — класс фильтра: `type`, `params: list[ParamSpec]`, `to_lut_fn(params)`.
* `ParamSpec` — `key`, `label`, `min`, `max`, `default`, `step`, `group`, `gradient` (для UI).
* `FilterRegistry` — реестр по `type`; GUI и CLI строят интерфейс по нему.

Параметры фильтров — см. [ALGORITHM.md §1](ALGORITHM.md).

## Preset — файл `*.cmpreset.json`

| Поле | Тип | Описание |
|---|---|---|
| `name` | str | Имя |
| `stack` | AdjustmentStack | Снимок параметров |
| `groups` | list[str] | Какие группы параметров содержит (`bc`, `cb`, `hs`, `opacity`, …) — для частичной вставки |
| `source` | str \| null | С какого фото снят |
| `schema_version` | int | 1 |

Методы: `from_stack`, `apply_to(stack, groups=None)`, `save/load` (суффикс
`.cmpreset.json` дописывается сам), `to_clipboard_text/from_clipboard_text` (JSON).

## RefItem — референс (схема v2, T-0050)

| Поле | Тип | Описание |
|---|---|---|
| `photo_id` | str | Фото-эталон |
| `offset` | ProfileOffset | Ручное смещение цели («хочу темнее / теплее», T-0017) |

Логика: референсов может быть много (локации раскадровки), активен ровно один.
«Авто-баланс выделенных» применяет активный и запоминает его в `PhotoItem.ref_id`;
«сцена/весь проект» — каждое фото к своему запомненному референсу (новые — к активному).
Методы Workspace: `ref_ids`, `ref_item`, `add_ref(id, activate)`, `remove_ref`, `set_active`,
`ref_for(photo)`. Миграция v1 → v2 (общий набор и наборы сцен → список, активный — первый) —
`services/workspace_io._v1_to_v2`. Усреднение нескольких референсов осталось только в CLI
`colormatch match --ref A --ref B` (для скриптов).

## ColorProfile («пипетка») — этап 2

| Поле | Тип | Описание |
|---|---|---|
| `bands` | dict[shadows\|midtones\|highlights → BandStats] | `L`, `a`, `b` почти нейтральных пикселей диапазона, `mass` — доля их веса |
| `L_p05`, `L_p50`, `L_p95` | float | Тон: перцентили L* |
| `C_p50`, `C_p90` | float | Хрома Lab (справочно) |
| `S_p50`, `S_p90` | float 0–1 | Насыщенность Lab C/√(C²+L²) (L* > 12) — по ней решатель сравнивает насыщенность |
| `neutral_share` | float 0–1 | Доля почти нейтральных пикселей |
| `confidence` | float 0–1 | Надёжность оценки каста |

Функции: `profile(rgb, stack)`, `combine(profiles, weights, mode)`, `apply_offset(profile, offset)`.
Позже (T-0016/T-0033): `semantic` (Lab классов), профили по планам глубины.

## ProfileOffset — этап 2

`L` (светлее +/темнее −), `a` (пурпурнее +/зеленее −), `b` (теплее +/холоднее −),
`chroma` (множитель насыщенности). Смещает целевой профиль набора референсов (T-0017).

## PixelSample — этап 2 (не сериализуется)

Зафиксированная выборка кадра для пипетки/решателя: `rgb` (n, 3), `w_band` (n, 3) —
вес пикселя в тенях/серединах/светах (нейтральность × тон × голос плитки), `w_valid`,
`neutral_share`, `confidence`, `shape`.

## Region / AnchorPair / Anchor — этап 2

* `Region` — `image` (путь), `rect` (x, y, ширина, высота) в долях кадра [0, 1].
* `AnchorPair` — `label`, `ref: Region`, `target: Region`, `weight`. Файл якорей для CLI —
  JSON-список AnchorPair; пара относится к целевому фото по имени файла `target.image`.
* `Anchor` (внутренний, решателя) — `target_px` (пиксели области цели), `ref_lab`
  (медиана Lab области референса после его стека), `weight`, `label`.
* Позже (GUI): точка + радиус, маска, метки «кожа/нейтраль/небо», автосвязь пар по метке.

## MatchSettings — этап 2

| Поле | По умолч. | Описание |
|---|---|---|
| `mode` | `balance+sat` | `balance` / `balance+sat` / `tone+balance+sat` / `full` |
| `touch` | — | Явный список ползунков (перекрывает `mode`) |
| `limits` | bc.brightness 40, bc.contrast 30, hs.saturation 35, hs.hue 20, cb 50 | Пределы решателя (ключ или группа) |
| `sparsity` / `sparse_rounds` | 0.08 / 2 | L1-штраф: меньше задействованных ползунков |
| `regularize` | 0.15 | L2-штраф величины ползунков |
| `band_weights` | (1, 1.2, 1) | Веса кастов теней/середин/светов |
| `tone_weight`, `sat_weight`, `anchor_weight` | 0.7, 1, 3 | Веса критериев |
| `sample_pixels` | 12000 | Размер выборки |
| `max_iter` | 25 | Итераций Левенберга–Марквардта |
| `strength` | 100 | Opacity результата |
| `reset` | true | Обнулять решаемые ползунки перед подбором (false — доводка от текущих) |

## MatchResult — этап 2

`stack` (итоговый стек), `params` (решённые ползунки), `before`/`after`/`target` (профили),
`metrics_before`/`metrics_after` (`cast_dE` — оттенок нейтралей, `dL50`, `dRange`, `dS50`),
`anchors_before`/`anchors_after` (ΔE якорей), `iterations`, `seconds`.

## ExportSettings

`out_dir`, `naming` (`overwrite`/`version`/`suffix`), `suffix`, `format`, `quality`,
`keep_metadata` (EXIF/ICC), `bit_depth`.

## AppSettings (GUI) — этап 4

Файл `%APPDATA%/colormatcher/settings.json` (или `$COLORMATCHER_HOME`): `theme`, `ui_scale`,
`font_family`, `font_size`, `slider_label_size`, `thumb_size`, `ref_thumb_size`, `viewer_bg`
(`gray18`/`black`/`white`/`checker`/`theme`), `preview_side`, `group_edit_relative`, `wheel_step`,
`autosave_minutes`, `reopen_last`, `mask_palette` (пресет палитры масок), `mask_alpha` (оверлей),
`brush_diameter`, `brush_spread` (+ `brush_hardness`, `brush_opacity` — на будущее), `language` (en/ru/…, T-0083),
`shortcuts` (id действия → клавиши, только переназначения; «» — без клавиши, T-0085), `recent`, `geometry`, `window_state`.

## Тема (GUI) — этап 4

JSON в `gui/themes/` или `%APPDATA%/colormatcher/themes/`: `name`, `dark`, токены `bg`, `panel`,
`surface`, `raised`, `border`, `text`, `text_dim`, `accent`, `accent_text`, `hover`, `selection`,
`danger`, `viewer`.

## AppState (GUI) — этап 4

`ws`, `path`, `selection`, `current`, `undo` (QUndoStack). Правки параметров — `edit_stacks(ids, fn(stack, id),
text, merge_key)` (снимки стеков; один сеанс перетаскивания — один шаг), изменения состава —
`mutate(text, fn(ws))` (снимок воркспейса). Сигналы: `workspace_changed`, `stacks_changed(ids)`,
`selection_changed`, `current_changed(id)`, `path_changed`, `masks_changed(id)`, `layer_changed(слой)`.
Слои меток: `active_layer` (None — общее, 0…6 — слой метки), `set_active_layer`, `target_stack(id, layer, create)`;
`edit_stacks(…, layer=…)` правит общий стек или слой (ключи снимков Undo — `id` / `id#слой`).
Маски: `push_mask_edit(text, id, box, before)` — мазок уже нанесён, в Undo хранится только
изменённый прямоугольник до/после; `remove_mask_slot(id, slot)`. Снимки `mutate` маски не содержат —
при восстановлении они переносятся (`Workspace.adopt_masks`).

## Маска-метки (T-0058, этап М1) — `models/masks.py`

Маска фото — массив весов **uint8 (H, W, 9)**: канал на метку, 0…255; в каждом пикселе сумма ≤ 255,
фон = 255 − сумма (под глобальной коррекцией). Размер сетки — пропорции кадра, длинная сторона
`MASK_SIDE = 1024` (`shape_for`); к кадру любого размера масштабируется (`resized`).

| Слот | Метка |
|---|---|
| 0…6 | Цвета палитры проекта (одинаковый слот на рефе и кадре = пара) |
| 7 (`IGNORE`) | «не учитывать» — вне статистики авто-баланса (М3) |
| 8 (`KEEP`) | «не трогать» — коррекция = 0 (М2) |

* **Кисть** `Brush(diameter, hardness=0, opacity=1, spread=0.2)`; профиль — `brush_profile(d, spread, hardness)` =
  (1 − d^p)² — всегда на весь диаметр (T-0075); радиус ядра ≥ 80% линеен по spread: 0.106 → 0.95 (T-0076, T-0078);
  расстояния — от центров пикселей маски (T-0077); hardness/opacity в GUI не используются; мазок — `Stroke(mask, slot, brush, erase)`: `add(p0, p1)`
  добавляет отрезок траектории как «капсулу» (расстояние до отрезка, край smoothstep), покрытие мазка копится
  максимумом (нажим ровный, перекрытия не копятся), смешивание — от снимка `before` на начало мазка:
  вес метки растёт к 255 как «нормальный» мазок PS, остальные метки пропорционально уступают (сумма ≤ 255);
  ластик уменьшает все метки. `stamp` / `stroke` — мазок из одной точки / одного отрезка. `coverage(mask)` — доля кадра на метку; `TINY_AREA = 0.2%` — порог ⚠.
* **Палитры** `PALETTES` (ключ → название, 7 цветов) + `SPECIAL_COLORS`; `palette_colors(preset)` — 9 цветов.
* **Хранение**: `Workspace.masks` (id → массив, приватно, вне JSON), `set_mask` / `touch_mask` (номер правки).
  `workspace_io.save` пишет `<имя>.masks/<id>.npz` (сжатый) — только изменённые с прошлой записи в эту
  папку, удаляет файлы убранных фото и пустых масок; `load` подхватывает; `pack` переносит;
  автосохранение (`write_json`) — в `<имя>.autosave.masks`. Схема JSON не изменилась (v2).

## Авто-баланс по меткам (М3) — `core/region_match.py`

* `MatchSettings.use_masks` (bool, по умолчанию true) — учитывать маски в авто-балансе.
* `region_sample(rgb, weights)` — пиксели области с весами; `region_stats(px, w)` — вектор
  `L10, L50, L90, a_dark, b_dark, a_light, b_light, C50`; `match_region(px, w, ref_stats, settings, base_layer)` →
  `RegionResult(stack, params, before, after)` (before/after — `dL`, `dAB`, `dC`).
* `matcher.lm_solve(evaluate, x, lo, hi, reg, max_iter)` — общий LM с границами (кадр и слои меток).
* Результат воркера авто-баланса: `layers` {метка: стек}, `regions` {метка: params, before, after, same_geometry}.

## Горячие клавиши — `gui/shortcuts.py` (T-0085)

`Registry` — действия главного окна со стабильными id (`file.open`, `match.selection`, `view.mode_masks`,
`view.peek_ref`, `masks.brush`…): `Binding(id, title, defaults, action, editable, current)`; `apply(overrides)`
ставит клавиши поверх умолчаний; `conflicts(current)` — одинаковые клавиши у разных id.

## Локализация — `gui/i18n.py` (T-0083)

`tr(строка, **подстановки)`, `N_(строка)` — пометка для констант, `load(code)`, `available()`, `current()`;
языки — JSON `{meta: {code, name}, strings: {исходная строка: перевод}}`. Подробно — docs/I18N.md.
