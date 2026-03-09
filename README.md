# Trend Analyzer v1.1.11

Приложение для онлайн/офлайн анализа технологических сигналов с Modbus TCP, архивом и печатью графиков.

## Основные возможности

- Опрос Modbus TCP в отдельном потоке.
- Несколько сигналов на одном графике.
- Несколько шкал Y с привязкой сигналов.
- Курсор и анализ участка графика.
- Архив в SQLite, экспорт/импорт архива.
- Режим экономии архива:
  - запись всех точек,
  - запись только при изменении (`deadband`, `keepalive`).
- Профили настроек, автозагрузка профиля.
- Автозапуск с Windows и автоподключение.
- Сборка portable EXE (one-file).

## Быстрый запуск (из исходников)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Где хранятся данные

- Конфигурация: `~/.trend_analyzer/config.json`
- Архив SQLite: `~/.trend_analyzer/archive.db`

Для portable EXE (`dist\TrendAnalyzer.exe`):

- `data\config.json` рядом с `exe`
- `data\archive.db` рядом с `exe`

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

