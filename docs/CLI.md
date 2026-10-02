# CLI `colormatch` — спецификация

> Этап 0 (2026-09-29), черновик. Тикет: T-0021.
> Запуск: `colormatch.bat <команда> …` (обёртка над `python-embed\python.exe -m colormatcher.cli`).
> Общие ключи: `--json` (машиночитаемый вывод), `--jobs N` (процессы), `--quiet`, `--verbose`.
>
> **Реализовано (этап 1):** `filters list [--json]`, `apply` (`--preset`, `--set`, `--opacity`,
> `--out`, `--naming`, `--suffix`, `--quality`, `--lut-size`, `--force`, `--json`),
> `preset save` (`--set`, `--opacity`, `--name`, `--groups`), `preset show` (`--json`, `--all`).
> **Реализовано (этап 2):** `analyze` (`--preset`, `--json`), `compare` (`--ref`, `--json`),
> `match` (см. ниже; кроме `--scenes` — это этап 3 с воркспейсами).
> Остальное — спецификация на этап 3.
>
> Значения `--set`: число или `true/false/yes/no/on/off`. Неизвестный ключ — ошибка.
> `apply` не перезаписывает исходник без `--force`.

## Анализ

```
colormatch analyze IMG... [--proxy 512] [--json]
    Профиль («пипетка») каждого фото: касты по тонам, яркости, хрома.
colormatch compare REF... --targets IMG...  [--json]
    ΔE2000 между профилями по диапазонам.
```

## Авто-матчинг

```
colormatch match --ref REF... [--ref-weight 1.0,0.5] [--ref-mode mean|median] [--ref-preset P]
                 (IMG... | --dir DIR [--recursive])            # [--scenes] — этап 3
                 [--mode balance|balance+sat|tone+balance+sat|full]   # по умолчанию balance+sat
                 [--touch cb.midtones.cr,hs.saturation,...] [--limit bc.brightness=20 --limit cb=40]
                 [--sparsity 0.08] [--regularize 0.15] [--strength 100] [--base-preset P]
                 [--offset-L 0 --offset-a 0 --offset-b 0 --offset-chroma 1]
                 [--anchors anchors.json]
                 [--out-presets DIR] [--out DIR --naming overwrite|version|suffix --suffix _cm --quality 95]
                 [--jobs N] [--json]
    Подбирает ползунки для каждого фото (референсы из списка целей исключаются).
    --out-presets — пресет на каждое фото; --out — сразу рендер в полном размере.
    Фото обрабатываются параллельно (--jobs, по умолчанию по числу ядер, до 8).
colormatch match --to-group-mean --dir DIR
    Без референса: свести группу к её медианному профилю.

Файл якорей (`--anchors`) — JSON-список пар пипеток, координаты — доли кадра:
[{"label": "кожа", "weight": 1,
  "ref":    {"image": "ref.jpg",  "rect": [0.42, 0.30, 0.05, 0.06]},
  "target": {"image": "shot.jpg", "rect": [0.55, 0.28, 0.05, 0.06]}}]
Пара применяется к целевому фото, чьё имя файла совпадает с target.image.
```

## Фильтры и пресеты

```
colormatch apply IMG... (--preset P.json | --set bc.brightness=15 --set cb.shadows.cr=10 --set hs.sat=-5 ...)
                 [--opacity 80] --out DIR [--naming ...]
colormatch preset extract IMG --from-workspace WS --out P.json
colormatch preset show P.json
colormatch preset copy --from IMG --to IMG... --workspace WS [--groups bc,cb,hs,opacity]
colormatch filters list            # реестр фильтров и параметров со диапазонами
```

## Воркспейсы — реализовано (этап 3)

Фото указываются по id, пути или имени файла (если имя однозначно).

```
colormatch ws new WS [--name N] [--force]
colormatch ws add WS [IMG...] [--dir DIR ...] [--no-recursive] [--no-scenes] [--scene NAME]
    # папка: каждая подпапка — сцена («папка/подпапка»); повторные файлы пропускаются
colormatch ws remove WS [--photo P ...] [--scene NAME ...]      # файлы на диске не трогаются
colormatch ws show WS [--json]                                  # сцены, фото, ★ референсы, правки, пропавшие
colormatch ws ref WS [--add P...] [--remove P...] [--clear] [--active P]
                 [--offset-L --offset-a --offset-b --offset-chroma]      # смещение — активному
    # референсы проекта без усреднения; файл не из воркспейса добавляется в сцену "refs"
colormatch ws set WS (--photo P... | --scene NAME... | --all)
                 [--set cb.midtones.cr=10 ...] [--relative] [--preset P [--groups cb,hs]] [--reset]
    # ручная правка одного фото или группы; --relative — прибавить к текущим значениям
colormatch ws preset WS PHOTO --out P [--groups bc,cb,hs,opacity] [--name N]
colormatch ws match WS [--scene NAME...] [--photo P...] [--ref P] [--mode ...] [--touch ...]
                 [--limit k=v] [--sparsity] [--strength] [--to-group-mean] [--remember] [--jobs N] [--json]
    # каждое фото — к своему референсу (запомненному, иначе активному); --ref — всем выбранным
    # этот; фото запоминает референс; референсы не трогаются; остальные правки сохраняются
colormatch ws export WS [--out DIR] [--naming overwrite|suffix|version] [--suffix _cm]
                 [--format keep|jpg|png|tif] [--quality 95] [--bit-depth keep|8|16] [--flat]
                 [--scene NAME...] [--photo P...] [--force] [--jobs N] [--json]
    # по умолчанию подпапки-сцены в --out; без --out — рядом с исходниками
colormatch ws pack WS --out DIR [--name N] [--with-renders]
    # DIR/<имя>.cmws + DIR/photos/<сцена>/…; пути относительные — папку можно переносить
colormatch ws check WS [--relink DIR]                           # пропавшие файлы; поиск по имени
```

## Именование при экспорте

| `--naming` | Результат |
|---|---|
| `overwrite` | То же имя (в `--out`; перезапись оригинала — только при `--out` = исходной папке и `--force`) |
| `version` | `name_v001.jpg` → следующий свободный номер; если имя уже `_vNNN` — инкремент |
| `suffix` | `name_cm.jpg` (суффикс настраивается `--suffix`) |

## Коды выхода

`0` — успех, `1` — ошибка ввода, `2` — часть файлов не обработана (подробности в `--json`).
