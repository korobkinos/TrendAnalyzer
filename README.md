# Trend Analyzer v1.3.0

Приложение для онлайн/офлайн анализа технологических сигналов с Modbus TCP, архивом и печатью графиков.

Начиная с версии `1.2.0` поддерживается разделение на две роли:
- `Viewer/Configurator` (основной UI: графики, теги, анализ, настройка),
- `Recorder` (внешний фоновый регистратор, пишет в БД независимо от UI).

С версии `1.2.1` добавлен отдельный `Tray Recorder`:
- работает в системном трее без главного окна,
- может автоматически запускать/останавливать фоновый Recorder,
- позволяет открыть UI-конфигуратор по требованию.

С версии `1.3.0` онлайн-режим работает по схеме единого источника:
- `Recorder` опрашивает Modbus и пишет в БД,
- UI в онлайн-режиме читает live-хвост данных из БД для графика и таблиц.

## Основные возможности

- Опрос Modbus TCP.
- Несколько сигналов на одном графике.
- Несколько шкал Y с привязкой сигналов.
- Курсор и анализ участка графика.
- Архив в SQLite, экспорт/импорт архива.
- Режим экономии архива:
  - запись всех точек,
  - запись только при изменении (`deadband`, `keepalive`).
- Профили настроек, автозагрузка профиля.
- Автозапуск с Windows и автоподключение.
- Внешний регистратор:
  - запуск/останов из UI,
  - отдельный статус регистратора,
  - запись продолжается без зависимости от окна графиков.
- Сборка portable EXE (one-file).

## Быстрый запуск (из исходников)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Viewer/Configurator:

```bash
python main.py
```

Recorder (фоновый режим):

```bash
python main.py --recorder
```

Tray Recorder (иконка в трее):

```bash
python main.py --recorder-tray
```

В tray-режиме:
- запись запускается автоматически при старте tray-приложения,
- доступны действия `Старт записи`, `Стоп записи`, `Статус регистратора...`,
- пункт `Открыть интерфейс настройки` поднимает обычный UI-конфигуратор.

## Где хранятся данные

- Конфигурация UI/профилей: `~/.trend_analyzer/config.json`
- Архив SQLite: `~/.trend_analyzer/archive.db`
- Конфиг внешнего регистратора: `~/.trend_analyzer/recorder_config.json`
- Статус внешнего регистратора: `~/.trend_analyzer/recorder_status.json`
- Команда управления регистратором: `~/.trend_analyzer/recorder_control.json`
- PID внешнего регистратора: `~/.trend_analyzer/recorder.pid`

Для portable EXE (`dist\TrendAnalyzer.exe`):

- `data\config.json` рядом с `exe`
- `data\archive.db` рядом с `exe`
- `data\recorder_config.json` рядом с `exe`
- `data\recorder_status.json` рядом с `exe`
- `data\recorder_control.json` рядом с `exe`

## Сборка portable EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

Результат:

- `dist\TrendAnalyzer.exe`

## Версионирование

Используется SemVer: `MAJOR.MINOR.PATCH`.

- `PATCH` (`1.1.x`) — исправления и небольшие улучшения.
- `MINOR` (`1.x.0`) — новые возможности без ломки совместимости.
- `MAJOR` (`x.0.0`) — крупные изменения с возможной несовместимостью.

История изменений:

- см. `CHANGELOG.md`
- см. `SESSION_HANDOFF_RU.md`
