# Релизы (T-0087, T-0088)

* **Версия** — `colormatcher/__init__.py` (`__version__`, SemVer): заголовок окна, `colormatch --version`,
  имя архива. Что изменилось — `CHANGELOG.md`.
* **Сборка:** `python-embed\python.exe tools\make_release.py --check`
  1. публичные исходники -> `..\image-colormatcher-release\` (отдельный git-репозиторий; его `.git` и `dist\`
     не трогаются). Не попадают: тикеты, личные заметки, воркспейсы, демо-фото, `python-embed`, `tests/_out`,
     журнал решений и разборы. Личные замены и маркеры утечек — в локальном `tools/release_private.json`
     (не публикуется); найдена утечка — сборка останавливается;
  2. `dist\ColorMatcher-<версия>-win64\` + `.zip`: `ColorMatcher.exe`, bat-файлы, код, урезанный встроенный
     Python (без pip/pytest; из Qt — Core/Gui/Widgets и нужные плагины). ~400 МБ, архив ~150 МБ;
  3. `--check` — архив распаковывается во временную папку: `--version`, `apply` из CLI, окно GUI (offscreen).
* **Публикация** (GitHub CLI `gh`): в папке релиза — commit, тег `v<версия>`, push;
  `gh release create v<версия> dist\ColorMatcher-<версия>-win64.zip --notes-file <раздел CHANGELOG>`.
