# Redmatch 2 - RU Localization

> Неофициальный проект, основанный на личном видении автора. Не связан с
> разработчиками игры и не является официальным.

## Установка и использование

1. Скачайте репозиторий (или распакуйте архив) в отдельную папку.
2. Убедитесь, что рядом лежат `script.py` и `LocalizationRemake.csv`.
3. Закройте игру, если она запущена (файл может быть занят).
4. Запустите скрипт, передав путь к `localization.csv`:

   ```powershell
   py script.py "C:\path\to\localization.csv"
   ```

   Если команда `py` не работает, используйте:

   ```powershell
   python script.py "C:\path\to\localization.csv"
   ```

5. Файл обычно находится по пути:

   ```text
   C:\Program Files (x86)\Steam\steamapps\common\Redmatch 2\Redmatch 2_Data\StreamingAssets\localization.csv
   ```

6. Если игра установлена в другом месте, найдите файл
   `Redmatch 2_Data\StreamingAssets\localization.csv`.

   Быстрый способ открыть папку игры: Steam > Библиотека > ПКМ по Redmatch 2 >
   Управление > Просмотреть локальные файлы.

Скрипт обновляет только колонку `russian`, используя данные из
`LocalizationRemake.csv`.

> **Важно:** скрипт перезаписывает `localization.csv`. Рядом создаётся резервная
> копия: `localization.bak_YYYYMMDD_HHMMSS.csv`.

## Откат

Чтобы вернуть исходный перевод:

1. Найдите файл резервной копии `localization.bak_*.csv`.
2. Переименуйте текущий `localization.csv` (например, в `localization.csv.broken`).
3. Переименуйте резервную копию в `localization.csv`.
