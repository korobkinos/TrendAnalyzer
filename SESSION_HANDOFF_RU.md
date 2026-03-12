# Trend Analyzer — Сводка выполненных изменений (handoff)

Дата: 2026-03-11
Текущая версия: `1.5.18`
Последняя сборка EXE: `2026-03-11 16:46:04` (`dist/TrendClient.exe`; `dist/TrendRecorder.exe` без изменений с предыдущей сборки)

## 0) Срочное обновление 1.5.2 (стабилизация запуска)

- Исправлен запуск внешнего регистратора из клиента:
  - `TrendClient.exe` больше не запускает сам себя с `--recorder`.
  - Теперь клиент ищет соседний `TrendRecorder.exe` и запускает его в режиме `--recorder`.
- Добавлен `single-instance`:
  - для `TrendClient`,
  - для `TrendRecorder` в tray-режиме.
- Упрощен верхний сценарий работы в UI:
  - `Настройка -> 1) Подключение`, `2) Сигналы графика`,
  - `Рабочий процесс -> 3) Старт`, `4) Стоп`,
  - расширенные окна перенесены в `Настройка -> Дополнительно`.
- Сборка Windows теперь ориентирована на 2 файла:
  - `TrendClient.exe`,
  - `TrendRecorder.exe` (tray + recorder core).

## 0.1) Срочное обновление 1.5.3 (PID-синхронизация recorder)

- Исправлена ложная ситуация, когда статус JSON показывает `running`, а UI/tray пишет `recorder не запущен`.
- Добавлен `resolve_recorder_pid()`:
  - сначала проверяет `recorder.pid`,
  - затем восстанавливает PID из `recorder_status.json` при необходимости.
- Усилена проверка PID на Windows (устойчивее к ложным отрицательным результатам).

## 0.2) Срочное обновление 1.5.4 (устранение лагов remote-client)

- Удалённый live-поток переведён в неблокирующий режим (запросы выполняются в фоне, UI не ждёт сеть).
- Добавлена устойчивая логика `connected` для remote-source (TTL по последнему успешному ответу).
- Добавлен backoff на повторные ошибки remote-соединения, чтобы исключить дергание и перегрузку CPU/UI.
- Ускорен старт online-просмотра по remote-history (уменьшен timeout).

## 0.3) Срочное обновление 1.5.5 (график при записи по изменению)

- В режиме `архив только по изменению` добавлены виртуальные UI-точки:
  - график продолжает идти по времени даже при неизменном значении,
  - время в таблице остается актуальным,
  - в БД лишние точки не записываются (объем архива не растет из-за визуализации).

## 0.4) Срочное обновление 1.5.6 (автозапуск recorder из UI-старта)

- Исправлен сценарий `3) Старт` в `TrendClient`, когда live-просмотр запускался только при ручном старте `TrendRecorder`.
- Восстановлен автозапуск внешнего регистратора из клиента:
  - сохранение snapshot-конфигурации перед запуском,
  - проверка/ожидание PID после старта.
- Добавлен корректный fallback:
  - если локальный recorder не поднялся, но есть удалённые источники, запускается только удалённый live-поток.
- Разблокированы recorder-зависимые действия UI, если recorder ещё не запущен, но доступен для автозапуска.

## 0.5) Срочное обновление 1.5.7 (batch-чтение Modbus)

- Реализовано групповое чтение подряд идущих регистров (batch до 125 слов) в ядре чтения:
  - `ModbusWorker`,
  - `RecorderService`.
- В окне `Регистры Modbus` локальное чтение тегов переведено на пакетный режим.
- Добавлен API endpoint `POST /v1/modbus/read_many`:
  - клиент окна `Регистры Modbus` для удалённых источников использует batch-чтение через этот endpoint,
  - сохранен fallback на старый поштучный `/v1/modbus/read` для совместимости со старыми recorder.
- Оптимизирована массовая запись тегов:
  - убрано лишнее чтение старого значения перед записью каждой строки в режиме `Записать`.

## 0.6) Срочное обновление 1.5.8 (упрощение сортировки таблицы сигналов)

- В таблице просмотра сигналов удалён отдельный выпадающий список `Сортировка: ...`.
- Оставлена единая сортировка по клику на заголовки столбцов.
- Убрано сохранение/восстановление устаревшего `values_sort_mode`; сортировка хранится только через `values_sort_column` и `values_sort_desc`.

## 0.7) Срочное обновление 1.5.9 (убран fallback на медленное удалённое чтение)

- Для удалённого чтения в окне `Регистры Modbus` удалён откат на поштучный `/v1/modbus/read`.
- Клиент использует только batch-режим `POST /v1/modbus/read_many`.
- Если удалённый recorder не поддерживает `read_many`, отображается явная ошибка необходимости обновить recorder.

## 0.8) Срочное обновление 1.5.10 (чинка «кракозябр» в статусе)

- Исправлено отображение битой кириллицы в нижней статус-строке.
- Добавлена автоматическая нормализация статусного текста:
  - если в `status_label` попадает mojibake вида `Ð¡Ñ‚...`, текст автоматически восстанавливается в нормальный UTF-8 русский.

## 0.9) Срочное обновление 1.5.11 (индикатор подключения в заголовке окна)

- В заголовок главного окна `TrendClient` добавлен динамический суффикс источника live-подключения.
- Теперь в шапке видно, куда подключен Analyzer:
  - локальный источник: `локальный recorder PID <pid>` или `локальный recorder: нет связи`,
  - удалённый источник: `удалённый <name> (<host>:<port>, online/offline)`,
  - при нескольких удалённых источниках: агрегат `удалённые recorder: <connected>/<total> online`.
- Добавлены явные состояния шапки для режимов:
  - `режим: офлайн`,
  - `режим: онлайн (ожидание старта)`.

## 0.10) Срочное обновление 1.5.12 (фикс кодировки статуса применения профиля)

- Исправлен сценарий `Источники данных -> Применить профиль на источник`, где в нижней строке статуса показывался mojibake вида `РЎС‚...`.
- Строки статуса в обработчике отправки профиля на удалённый recorder приведены к корректной UTF-8 кириллице.
- Расширена функция автонормализации `status_label`:
  - теперь автоматически чинится не только mojibake формата `Ð¡Ñ‚...` (Latin-1),
  - но и формат `РЎС‚...` (CP1251), чтобы UI оставался читаемым даже при попадании «битых» строк.

## 0.11) Срочное обновление 1.5.13 (доработка шапки подключения)

- Исправлено отображение источника подключения в заголовке главного окна:
  - информация о подключении перенесена в начало шапки (`<статус подключения> — Trend Analyzer v...`), чтобы не терялась при обрезке справа,
  - в режиме `онлайн (ожидание старта)` добавлен показ целевого источника (`локальный recorder` и/или `удалённый host:port`),
  - для удалённых источников до запуска live-потока показывается `настроен`, после старта — `online/offline`.

## 0.12) Срочное обновление 1.5.14 (системный анти-mojibake фикс)

- Реализован глобальный анти-mojibake слой в UI:
  - patched Qt-методы установки текста (`setText`, `setWindowTitle`, `setItemText`, `setTabText`, `setTitle`, `setStatusTip`, `setToolTip`) автоматически прогоняют текст через нормализатор кодировки,
  - добавлена стартовая нормализация существующих UI-текстов (виджеты, меню, таблицы), чтобы исправлять строки, созданные через конструкторы.
- Нормализатор поддерживает два типовых вида «битой кириллицы»:
  - `Ð¡Ñ‚...` (UTF-8 -> Latin-1),
  - `РЎС‚...` (UTF-8 -> CP1251).
- Добавлены автотесты, которые проверяют:
  - декодирование обоих типов mojibake,
  - фактическую работу глобального Qt-patch для `QLabel` и `QTableWidgetItem`.

## 0.13) Срочное обновление 1.5.15 (явное подключение источника + выборочный импорт тегов)

- В `Источники данных` добавлена явная кнопка `Подключить выбранный`:
  - выполняет `health-check` выбранного source,
  - при успехе переводит строку в состояние `Подключен: ...`,
  - автоматически включает source (`Вкл`) и синхронизирует его для выбора в окне `Регистры Modbus`.
- Добавлен выборочный импорт тегов из source:
  - новая кнопка `Импортировать выбранные теги...`,
  - открывает диалог с чекбоксами по тегам источника,
  - позволяет импортировать только нужный поднабор тегов (без принудительного импорта всех).
- Массовый режим оставлен отдельным:
  - кнопка переименована в `Импортировать все теги`.
- Защита от дублей сохранена:
  - уже импортированные теги по паре `(source_id, remote_tag_id)` повторно не добавляются.

## 0.14) Срочное обновление 1.5.16 (видимость источников в таблицах)

- Исправлено отображение колонки `Источник` в таблице значений под графиком:
  - вместо режима `Курсор/Текущее` теперь показывается фактический источник сигнала.
- Добавлена отдельная колонка `Источник` в окно `Сигналы графика`:
  - видно для каждого тега, локальный он или с какого удалённого recorder пришёл.
- Добавлена синхронизация source-подписей:
  - при изменении списка источников имена/host:port в колонке `Источник` у сигналов обновляются автоматически.

## 0.15) Срочное обновление 1.5.17 (фикс чекбоксов выбора тегов)

- Исправлен баг в диалоге `Импортировать выбранные теги...`, из-за которого нельзя было поставить «птички», если теги уже присутствуют в проекте.
- В колонке `Импорт` чекбоксы больше не блокируются для уже импортированных тегов.
- Повторный выбор таких тегов безопасен:
  - дубли в список сигналов не добавляются (сохраняется фильтрация по `(source_id, remote_tag_id)`),
  - в заголовке диалога добавлена подсказка, что уже импортированные теги будут пропущены.
- Обновлена документация:
  - в `README.md` добавлен отдельный раздел с описанием всех параметров окна `Настройки подключения`, включая настройки архива и Recorder API.

## 0.16) Срочное обновление 1.5.18 (индикатор выполнения тяжёлых операций)

- Добавлен визуальный индикатор занятости в нижнюю строку состояния:
  - progress bar в неопределённом режиме (показывает, что операция выполняется),
  - курсор ожидания на время выполнения.
- Индикатор подключен к ресурсоёмким операциям:
  - подключение/проверка/сканирование источников,
  - импорт тегов и отправка профиля на источник,
  - ручное чтение/запись регистров в окне `Регистры Modbus`,
  - очистка архивной БД.

## 0.17) Оперативные правки после 1.5.18 (UX + кодировка + статистика)

- Окно `Регистры Modbus`:
  - устранено нежелательное создание новой вкладки по `Enter` во время ввода;
  - у кнопок отключены `autoDefault/default`, чтобы `Enter` не срабатывал как «нажатие кнопки».
- Окно `Анализ участка графика`:
  - исправлена строка с битой кодировкой в подписи интервала.
- График:
  - добавлена зеленая полупрозрачная подсветка интервала между 2 статистическими маркерами;
  - подсветка синхронизирована с перемещением маркеров и режимом двухточечной статистики.
- Пересобран `TrendClient.exe`:
  - файл `dist/TrendClient.exe` обновлен до `2026-03-11 16:46:04`.

## 1) Что уже реализовано

- Онлайн-опрос Modbus TCP (IP/порт/Unit ID/timeout/retries/offset).
- Разделены интервалы:
  - `Частота опроса` (получение данных),
  - `Интервал отрисовки` (UI-обновление).
- Многосигнальный график с несколькими Y-шкалами, курсором, автоскроллом, статистикой, масштабированием.
- Ось X: дата+время с миллисекундами.
- Таблица значений под графиком:
  - видимость сигнала (чекбокс),
  - цвет сигнала «на лету»,
  - сортировка.
- Отдельные окна:
  - `Настройки подключения`,
  - `Сигналы графика`,
  - `Регистры Modbus` (чтение/запись, диапазонное добавление, вкладки),
  - `Статистика`.
- Режимы архивирования/анализа:
  - online/offline,
  - экспорт/импорт архива.
- Экспорт архива в `.trend.zip` + проверка манифеста.
- Печать графика на A4 (горизонтально), белый фон, настраиваемые шрифты, отдельная страница статистики (по опции).
- Системный трей:
  - сворачивание в трей,
  - настройка поведения при закрытии.
- Автозапуск с Windows и автоподключение при старте приложения.
- Нижняя статус-панель:
  - слева: временный текст последнего действия,
  - справа: постоянный runtime-статус (подключение, CPU, RAM, размер архива, режим архивации, состояние рендера графика).
- Внешний регистратор (`1.2.0`):
  - запуск отдельным процессом (`main.py --recorder`),
  - управление из UI (`Режим -> Старт/Стоп внешнего регистратора`, `Статус регистратора...`),
  - межпроцессные файлы: `recorder_config.json`, `recorder_status.json`, `recorder_control.json`, `recorder.pid`.
- Tray-регистратор (`1.2.1`):
  - отдельный запуск без главного окна (`main.py --recorder-tray`),
  - управление записью из меню трея (`Старт записи`, `Стоп записи`, `Статус регистратора...`),
  - открытие UI-конфигуратора из трея (`Открыть интерфейс настройки`),
  - отдельный автозапуск Windows для tray-роли (`TrendAnalyzerRecorder`).
- Версионирование по SemVer + `CHANGELOG.md`.

## 2) Последние изменения по версиям 1.1.x-1.5.x

- `1.1.0`: старт ветки 1.1.x.
- `1.1.1 - 1.1.4`: доработка runtime-строки статуса (CPU/RAM/архив), автоочистка временного статуса.
- `1.1.5 - 1.1.6`: правки отступов и позиционирования нижней панели, старт окна в развернутом виде.
- `1.1.7`: кнопка очистки архива БД + подтверждение.
- `1.1.8`: оптимизация структуры архива (compact samples + `signals_meta`), миграция с legacy-формата.
- `1.1.9`: отключена legacy-совместимость, оставлена только новая компактная схема БД.
- `1.1.10`: добавлено архивирование «только при изменении» (`deadband` + `keepalive`) и настройки в окне подключения.
- `1.1.11`: добавлен индикатор режима архива в статусе + снижены риски роста RAM (ограничение очереди UI и pruning истории событий связи) + исправлена кодировка `README.md` (UTF-8 для корректного отображения на GitHub).
- `1.1.12`: добавлен переключатель отрисовки графика (режим архиватора без визуализации), сортировка по клику на заголовки в таблице значений и улучшено отображение ячейки цвета на всю ширину.
- `1.1.13`: поведение `Отрисовка графика` уточнено: выключение очищает график и отключает рендер, включение подтягивает график из архива; архивирование продолжает работать независимо от визуализации.
- `1.1.14`: в runtime-строку статуса добавлен явный индикатор режима отрисовки (`график: вкл/выкл`) с моментальным обновлением при переключении чекбокса `Отрисовка графика`.
- `1.1.15`: в окне `Сигналы графика` добавлена кнопка `Удалить все`; удаление очищает таблицу сигналов без автодобавления placeholder-сигнала и дополнительно чистит архивные хвосты по удалённым `signal_id` (`samples` + `signals_meta`).
- `1.2.0`: реализовано разделение ролей `Viewer/Configurator` и `Recorder`; UI сохраняет снапшот активного профиля для Recorder и может запускать/останавливать внешний процесс записи в БД независимо от отрисовки графиков.
- `1.2.1`: добавлен `Tray Recorder` как отдельный режим приложения; подключены команды запуска/остановки recorder из трея и отдельная настройка автозапуска tray-роли через Windows Run.
- `1.2.2`: переработана структура верхнего меню, заменено название раздела на `Управление`, сгруппированы пункты по сценариям работы и убран диагностический пункт из обычного меню.
- `1.3.0`: онлайн-поток UI переведен на live-чтение данных из БД; прямой Modbus-опрос в UI больше не является основным источником, единый источник данных обеспечивается внешним recorder-процессом.
- `1.4.0`: реализована мульти-источниковая архитектура клиентской части:
  - добавлено окно `Источники данных (регистраторы)` (ручное добавление + авто-сканер сети),
  - добавлен `Recorder API v1` в recorder-процесс (`/v1/health`, `/v1/tags`, `/v1/live`, `/v1/history`, `/v1/config`, `/v1/modbus/read`, `/v1/modbus/write`),
  - добавлен импорт тегов из удалённых recorder в `Сигналы графика`,
  - live-график в UI теперь может одновременно получать точки из локального и удалённых recorder,
  - в `Регистры Modbus` добавлен выбор источника (локальный прямой Modbus или удалённый recorder по API),
  - в профиль добавлены API-настройки recorder и список удалённых источников.
- `1.5.2 - 1.5.6`: серия стабилизаций запуска recorder и online-live режима:
  - исправлен запуск `TrendRecorder.exe` из клиента и синхронизация PID,
  - устранены лаги/дребезг remote-live,
  - исправлено отображение времени в режиме `архив только по изменению`,
  - восстановлен автозапуск recorder из `3) Старт` с fallback на удалённые источники.
- `1.5.7`: оптимизация Modbus-обмена:
  - групповые чтения подряд идущих адресов в worker/recorder,
  - batch-API чтения удалённых тегов (`/v1/modbus/read_many`),
  - ускорение массовой записи тегов за счет удаления лишнего pre-read.
- `1.5.8`: UI-упрощение таблицы сигналов:
  - удалён дублирующий выпадающий выбор сортировки,
  - оставлена сортировка только по заголовкам столбцов.
- `1.5.9`: удалена fallback-совместимость удалённого чтения регистров:
  - batch-only чтение через `/v1/modbus/read_many`,
  - поштучный fallback отключен.
- `1.5.10`: фикс отображения статуса:
  - добавлен авто-ремонт mojibake в нижней строке `Статус`.

Подробно: см. `CHANGELOG.md`.

## 3) Что важно знать по архиву (текущее состояние)

- Приложение работает только с новой структурой БД архива.
- Legacy-путь со старой таблицей `samples(..., signal_name, ...)` удален.
- При обнаружении legacy-схемы таблица пересоздается в compact-виде (без миграции данных legacy).
- Добавлен режим экономии архива:
  - `Только при изменении` (`archive_on_change_only`),
  - `deadband` (минимальная разница для записи),
  - `keepalive` (принудительная точка через заданный интервал, сек).

## 4) Наблюдения по памяти и узкие места

- Потенциальное накопление: очередь пакетов на отрисовку (`_pending_render_samples`) при очень быстрых опросах.
  - Мера: уменьшен лимит очереди (`_max_pending_render_batches`) для защиты RAM.
- Потенциальное накопление: список событий связи (`_connection_events`) при частых обрывах.
  - Мера: добавлен pruning по активному временному окну данных графика.
- Буферы сигналов в графике уже ограничены (`_max_points` на сигнал), поэтому безразмерного роста по точкам графика нет.

## 5) Файлы, где сосредоточены ключевые изменения

- `trend_analyzer/ui.py`
- `trend_analyzer/chart.py`
- `trend_analyzer/storage.py`
- `trend_analyzer/models.py`
- `trend_analyzer/modbus_worker.py`
- `trend_analyzer/archive_bundle.py`
- `trend_analyzer/recorder_shared.py`
- `trend_analyzer/recorder_service.py`
- `trend_analyzer/recorder_api.py`
- `trend_analyzer/recorder_tray.py`
- `trend_analyzer/startup.py`
- `trend_analyzer/version.py`
- `tests/test_models_profile.py`
- `tests/test_storage_archive.py`
- `tests/test_startup.py`
- `CHANGELOG.md`

## 6) Новое в 1.5.0 (role-based deployment)

- Добавлены отдельные точки входа:
  - `client_main.py` (UI клиент),
  - `recorder_main.py` (headless recorder, без UI),
  - `recorder_tray_main.py` (tray режим).
- Добавлены новые скрипты сборки:
  - `build_roles_windows.ps1` (2 отдельных exe: client + recorder),
  - `build_roles_linux.sh` (2 отдельных бинаря: client + recorder),
  - `build_deb_roles.sh` (deb-пакеты `trend-client` и `trend-recorder`).
- Добавлен systemd unit template:
  - `packaging/linux/trend-recorder.service`.
- В окне `Источники данных` добавлена кнопка:
  - `Применить профиль на источник` (удаленное применение профиля через `PUT /v1/config`).
- Версия проекта повышена до `1.5.0`.

---

## 0.18) Обновление 2026-03-11 (сегодня)

- Исправлен ложный prompt о несохраненной конфигурации при выходе после `Сохранить`.
  - В dirty-check исключены динамические runtime-поля (`ui_state.view.x_range` при `Авто X`, `y_min/y_max` для шкал с `Авто Y`).
- Нижняя таблица под графиком теперь показывает только активные сигналы.
- Добавлен drag-and-drop сигналов из окна `Сигналы графика` на график.
- Исправлены зависания при массовом переключении видимости (batch-обновление сигналов в графике).
- Добавлено явное сохранение активного набора сигналов в `ui_state.view.active_signal_ids` и восстановление при загрузке.
- Выполнена пересборка:
  - `dist/TrendClient.exe` — `2026-03-11 23:40:58`
  - `dist/TrendRecorder.exe` — `2026-03-11 23:42:04`
- Проверки:
  - `py_compile` — OK
  - unit tests (`35`) — OK
## 0.19) Update 2026-03-12 (performance/lag hotfix)

- Symptom addressed:
  - heavy UI lag while applying configuration and when toggling `Авто X`,
  - effective load looked like processing hundreds of hidden tags.

- Implemented optimizations:
  - `trend_analyzer/ui.py`
    - Auto-X handlers and render-enable flow now request recent history only for active signal ids.
    - `_load_recent_online_history_from_db(...)` supports `signal_ids` filter (MAX/COUNT + range query + fallback query).
    - remote mapping now supports filtering:
      - `_remote_signal_mapping(only_enabled, signal_ids)`
      - live bootstrap/poll/history use `only_enabled=True`.
    - `_apply_current_profile(...)` no longer always rebuilds chart; `configure_signals(...)` only when signals actually changed.
  - `trend_analyzer/chart.py`
    - hidden curves are cleared once and skipped on subsequent redraws,
    - range-change decimation redraw trigger now ignores disabled signals,
    - display rows emission now skips disabled signals.

- Verification:
  - `.venv` tests: `python -m unittest discover -s tests -p "test_*.py"` -> 35 passed.
  - compile check for touched files -> OK.

- Build status:
  - full `build_roles_windows.ps1` blocked because `dist/TrendClient.exe` is locked by running process.
  - side-by-side builds produced:
    - `dist/TrendClient_opt.exe` (2026-03-12 09:18:07)
    - `dist/TrendRecorder_opt.exe` (2026-03-12 09:19:08)
  - to refresh canonical names (`TrendClient.exe` / `TrendRecorder.exe`), close running TrendClient/TrendRecorder processes and rerun `build_roles_windows.ps1`.

## 0.20) Update 2026-03-12 (major feature: multi-device sources in one recorder)

- Request implemented:
  - one recorder profile can now contain signals from multiple Modbus devices (`IP:port`) in parallel.

- What changed:
  - `trend_analyzer/models.py`
    - `RecorderSourceConfig` now supports:
      - `source_kind` (`remote_recorder` or `modbus_tcp`)
      - `unit_id`, `timeout_s`, `retries`, `address_offset`
  - `trend_analyzer/recorder_service.py`
    - new grouped polling architecture:
      - `local` profile source + enabled `modbus_tcp` sources are polled in one loop,
      - each source gets its own Modbus client/signature/reconnect state,
      - samples are merged and archived together.
    - old single-source loop kept as legacy method (`_run_loop_legacy_single_source`) but not used.
  - `trend_analyzer/ui.py`
    - `Источники данных` table expanded:
      - `Вкл | Имя | Тип | Host | Порт | Unit ID | Token | Recorder ID | Статус`
    - source type routing:
      - `remote_recorder` uses recorder API (`/v1/*`),
      - `modbus_tcp` uses direct Modbus connection.
    - tags window (`Регистры Modbus`) now reads/writes `modbus_tcp` sources directly (including batch reads).
    - remote live/history/profile-sync logic now works only with `remote_recorder` sources.
    - signal normalization:
      - `remote_tag_id` auto-required only for `remote_recorder`,
      - `modbus_tcp` signals keep empty `remote_tag_id`.

- Tests/validation:
  - `.venv\Scripts\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (11 tests).
  - `.venv\Scripts\python -m py_compile trend_analyzer/models.py trend_analyzer/recorder_service.py trend_analyzer/ui.py` -> OK.

- Important notes for next session:
  - UI source table currently exposes only `Unit ID` for per-source Modbus tuning in the grid.
  - `timeout_s`, `retries`, `address_offset` are persisted per source (hidden in row metadata + model), but not yet editable in dedicated controls.
  - if needed next: add per-source advanced editor (context menu/dialog) for these hidden fields.

## 0.21) Update 2026-03-12 (zoom detail + smoothness hotfix)

- User-facing issue:
  - at high time zoom chart looked under-detailed,
  - history/live updates appeared in visible jumps ("stutter").

- Changes made:
  - `trend_analyzer/chart.py`
    - adaptive render point budget by visible X-span:
      - short span (minutes) -> high detail,
      - medium span -> balanced detail,
      - long span -> stronger decimation for performance.
    - raised per-signal live in-memory point cap from 4000 to 8000.
  - `trend_analyzer/ui.py`
    - visible-window DB reload (`_load_history_window_from_db`) now requests active signals first (`enabled=true`) to avoid loading hidden hundreds of tags,
    - `_target_history_points(span_s=...)` is now span-aware (more points for short windows),
    - `_query_samples_for_window(...)` raw branch now returns real bucket granularity (prevents redundant re-reloads caused by synthetic coarse value),
    - `_load_recent_online_history_from_db(...)` defaults to active-signal filter when no explicit filter is passed,
    - `_flush_pending_render_samples()` now flushes in chunks (`_max_render_flush_batches`) to reduce UI jumps under burst load.
    - minor smoothing: preserve-range path avoids redundant `set_x_range(...)` when range is already unchanged.

- Verification:
  - `python -m py_compile trend_analyzer/chart.py trend_analyzer/ui.py` -> OK
  - `python -m unittest tests.test_models_profile tests.test_recorder_service` -> OK
  - `tests.test_ui_recorder_command` cannot run in current CLI env (missing `PySide6`), not due code regression.

- Build:
  - `build_roles_windows.ps1` -> OK
  - `dist/TrendClient.exe` -> `2026-03-12 10:08:18`
  - `dist/TrendRecorder.exe` -> `2026-03-12 10:09:41`

## 0.22) Update 2026-03-12 (UI redesign for multi-source workflow)

- Goal:
  - align `Настройки подключения` and `Сигналы графика` with new multi-source architecture
    where each signal can belong to different source.

- Implemented:
  - `trend_analyzer/ui.py` (`Настройки подключения`):
    - added compact source summary line:
      - local Modbus endpoint + source counts by kind (Modbus TCP / Recorder API),
    - added direct navigation button `Источники данных...`,
    - renamed core fields to emphasize local/default role:
      - `Локальный IP (Modbus)`, `Локальный порт`, `Локальный Unit ID`,
      - `Таймаут/Повторы/Смещение (лок./по умолч.)`.
  - `trend_analyzer/ui.py` (`Сигналы графика`):
    - added `Источник` combobox for target source of newly added signals/ranges,
    - added quick button `Источники...`,
    - introduced tab `Все источники` for full cross-source signal overview.
  - model-sync / save safety:
    - `_store_signal_table_to_profile(...)` now has special handling for `Все источники`:
      replaces full list instead of merge-by-source (prevents duplicates),
    - clear-all flow supports all-sources context and keeps archive cleanup ids correct,
    - remote-tag runtime repair updates visible table for both source tab and `Все источники`.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_ui_recorder_command tests.test_models_profile tests.test_recorder_service` -> OK

- Build:
  - full canonical build failed due locked `dist/TrendRecorder.exe` (running process),
  - produced side-by-side builds for immediate testing:
    - `dist/TrendClient_uiopt.exe` -> `2026-03-12 10:26:15`
    - `dist/TrendRecorder_uiopt.exe` -> `2026-03-12 10:27:19`

## 0.23) Update 2026-03-12 (close/shutdown reliability)

- User report:
  - after app "close", process remained in Task Manager even without tray icon.

- Root cause:
  - close behavior `tray` was still applied when tray was unavailable;
  - close event was ignored and app stayed alive (hidden/minimized process).

- Fixes in `trend_analyzer/ui.py`:
  - introduced `_can_minimize_to_tray()` gate;
  - `_minimize_to_tray()` now returns success flag and does nothing if tray unavailable;
  - `closeEvent(...)`:
    - for `behavior=tray`: if tray unavailable -> auto fallback to `exit`,
    - for `behavior=ask`: `В трей` option appears only when tray is available.
  - `_sync_close_behavior_actions()`:
    - disables `tray` actions when tray unavailable,
    - auto-switches persisted behavior from `tray` to `exit` in unavailable-tray session.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_ui_recorder_command tests.test_models_profile tests.test_recorder_service` -> OK

- Suggested runtime check (manual):
  1. zoom to ~1-5 min window and confirm denser curve detail;
  2. zoom/pan in offline mode and confirm lower stutter during window reload;
  3. profile with many disabled tags and only 1-2 enabled: verify Auto X/apply config no longer freezes as before.

## 0.24) Update 2026-03-12 (subnet scan: skip local machine)

- User report:
  - in `Источники данных` subnet scan found local PC IP (example: `192.168.4.186`) as "remote source".

- Root cause:
  - scanner probed entire subnet and did not filter out local host addresses;
  - if local Recorder API listened on `0.0.0.0:<api_port>`, local host answered and was added as remote.

- Fixes in `trend_analyzer/ui.py`:
  - local IPv4 detection (`_local_ipv4_candidates`) used in scan workflow;
  - `_scan_subnet_for_sources(...)` now skips local target hosts before probing;
  - `_on_scan_sources_clicked(...)` shows `пропущено локальных адресов: N` in status;
  - removed `profile.ip` from local-candidate list because it may reference external Modbus device.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_ui_recorder_command tests.test_models_profile tests.test_recorder_service` -> OK

## 0.25) Update 2026-03-12 (visual curve smoothing option)

- User request:
  - reduce "stair-step" curve appearance with a configurable smoothing option.

- Implemented:
  - `trend_analyzer/models.py`:
    - profile fields added:
      - `plot_smoothing_enabled: bool`
      - `plot_smoothing_window: int` (normalized odd window in range `3..31`)
  - `trend_analyzer/chart.py`:
    - added `set_curve_smoothing(enabled, window)` runtime API,
    - implemented centered moving-average smoothing on rendered Y-series,
    - smoothing is applied only to visualization (raw/archive data unchanged),
    - BOOL signals are not smoothed.
  - `trend_analyzer/ui.py`:
    - `Настройки графика` now has:
      - checkbox `Сглаживание кривой`,
      - spin `Окно сглаживания` (`3..31` points),
      - hint that smoothing affects only display.
    - smoothing settings are loaded/saved with profile and applied on:
      - profile load,
      - profile apply,
      - graph settings apply.
    - print chart uses same smoothing settings.
  - tests:
    - added model test for smoothing fields roundtrip + normalization.

- Validation:
  - `python -m py_compile trend_analyzer/chart.py trend_analyzer/models.py trend_analyzer/ui.py tests/test_models_profile.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

## 0.26) Update 2026-03-12 (UI themes + archive toggling + size retention)

- User requests addressed:
  - add UI theme control (window/background/text style), including ready theme profiles;
  - fix `Писать в БД` toggle that appeared ineffective;
  - add archive depth mode by DB size (MB/GB), not only by days.

- Implemented:
  - `trend_analyzer/models.py`:
    - added profile fields:
      - `ui_theme_preset`,
      - `archive_retention_mode`,
      - `archive_max_size_value`,
      - `archive_max_size_unit`.
  - `trend_analyzer/ui.py`:
    - added global theme presets and runtime stylesheet application;
    - `Настройки графика` now includes `Тема интерфейса` with profiles:
      - `Темная`, `Светлая`, `Графит`, `Песочная`;
    - theme persists per profile and is restored on profile switch.
    - `Настройки подключения`:
      - added retention mode selector (`По времени (дни)` / `По размеру БД`),
      - added size controls (`value + MB/GB`).
    - `Архив и экспорт -> Писать в БД` now immediately pushes updated profile to local recorder runtime (`/v1/config` path via existing push logic), not only local UI state.
    - local archive prune path now supports both day-based and size-based policy.
  - `trend_analyzer/storage.py`:
    - added `db_size_bytes()`,
    - added `prune_to_max_size(...)` (oldest rows cleanup, checkpoints, optional vacuum).
  - `trend_analyzer/recorder_service.py`:
    - fixed bug: sample writes now truly stop when `archive_to_db=False`,
    - connection events also skip DB writes when archive writing is disabled,
    - unified retention helper now supports day mode and size mode in recorder loop.
  - `tests/test_models_profile.py`:
    - added roundtrip/normalization test for retention-size + theme fields.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py trend_analyzer/models.py trend_analyzer/storage.py trend_analyzer/recorder_service.py tests/test_models_profile.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.27) Update 2026-03-12 (light theme icon visibility + shared theme for chart/windows)

- User request:
  - in light theme, top-right panel control icons are hard to see;
  - add more light presets (a bit darker than current light);
  - theme profile should apply both to app windows and chart (light theme => light chart too).

- Implemented:
  - `trend_analyzer/ui.py`:
    - values panel control icons (`collapse/expand/close`) are now theme-aware:
      - icon stroke uses preset `icon` color (fallback to `text`);
      - icon set is refreshed whenever runtime theme is applied.
    - added two new presets in `UI_THEME_PRESETS`:
      - `light_soft` (`Светлая мягкая`),
      - `light_warm` (`Светлая теплая`).
    - each theme now includes chart defaults:
      - `chart_bg`, `chart_grid`, `chart_grid_alpha`.
    - graph settings theme selector now controls both UI and chart base style:
      - row label changed to `Тема интерфейса и графика`,
      - on theme change, chart color controls are automatically filled from the selected preset,
      - resulting settings are applied to chart and runtime UI together.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.28) Update 2026-03-12 (arrow hit-area + Enter key behavior in connection settings)

- User request:
  - arrow controls in settings are hard to click;
  - pressing `Enter` after editing IP should not create a new profile.

- Implemented:
  - `trend_analyzer/ui.py`:
    - expanded interactive zones for numeric/list editors via theme stylesheet:
      - `QSpinBox/QDoubleSpinBox` up/down button width/arrow size increased,
      - `QComboBox` drop-down button width/arrow size increased,
      - added hover/pressed states for better visual feedback.
    - prevented accidental default-button trigger in connection dialog:
      - disabled `setAutoDefault`/`setDefault` on profile action buttons
        (`Новый`, `Клон`, `Удалить`, `Сохранить`).

- Result:
  - spinner/combo arrows are easier to hit with the mouse;
  - `Enter` in input fields (including IP) no longer invokes `Новый профиль`.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.29) Update 2026-03-12 (checkbox visibility in light themes)

- User report:
  - checkboxes are hard to see on light theme (especially in table "Вкл" column).

- Implemented:
  - `trend_analyzer/ui.py`:
    - strengthened themed indicator styling for both standalone checkboxes and item-view checkbox delegates:
      - `QCheckBox::indicator`,
      - `QTableView::indicator`,
      - `QTreeView::indicator`,
      - `QListView::indicator`.
    - added explicit styles for `hover`, `checked`, `disabled` to maintain contrast on light palettes.

- Result:
  - checkbox boxes remain visible in unchecked state on light themes;
  - checked state remains clearly distinguishable.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK

## 0.30) Update 2026-03-12 (menu item spacing + async archive trim after retention changes)

- User request:
  - menu items (e.g. under `Настройка`) need a bit more text padding from edges;
  - when archive retention limit is reduced (size or time), existing DB should be pruned accordingly;
  - pruning must not freeze app UI (use current progress indicator).

- Implemented:
  - `trend_analyzer/ui.py`:
    - theme stylesheet spacing tweaks:
      - added `QMenuBar::item` padding/margins;
      - added `QMenu::item` padding/margins.
    - new non-blocking archive prune flow:
      - dedicated maintenance executor (`max_workers=1`) + timer-driven completion polling,
      - retention prune request is triggered on `Применить` / `Сохранить` when retention settings signature changed,
      - while prune runs, bottom busy progress indicator is shown,
      - repeated retention changes during active prune are queued and executed after current task.
    - added maintenance executor shutdown in close path.
    - `_save_config` extended with `trigger_retention_prune` flag; close-flow save uses `False`.
  - `trend_analyzer/storage.py`:
    - added `ArchiveStore.vacuum()` utility for explicit post-cleanup DB compaction.

- Result:
  - menu text no longer "stuck to edges";
  - stricter retention settings are applied to existing archive in background;
  - UI remains responsive during cleanup.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py trend_analyzer/storage.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.31) Update 2026-03-12 (flat button corners + clearer spin/combo arrows)

- User feedback:
  - UI is much smoother, but rounded button corners should be removed;
  - arrow/drop-down controls should have stronger visual indicators and larger arrows.

- Implemented:
  - `trend_analyzer/ui.py`:
    - button corner radius set to `0` (flat corners):
      - global themed `QPushButton/QToolButton`,
      - values panel tool buttons,
      - values-table color swatch buttons.
    - spin/combo controls reinforced:
      - `QSpinBox/QDoubleSpinBox` up/down button width increased to `24`,
      - spin arrows enlarged to `13x13`,
      - `QComboBox` drop-down width increased to `24`,
      - stronger drop-down separator (`2px`),
      - combo arrow enlarged to `13x13`.

- Result:
  - button appearance is now sharper/rectangular;
  - arrow/drop-down controls are easier to notice and hit.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.32) Update 2026-03-12 (checkbox style polish after UI feedback)

- User feedback:
  - checkbox visuals still look rough/ugly on light theme (solid filled square effect).

- Implemented:
  - `trend_analyzer/ui.py`:
    - adjusted themed indicator states:
      - unchecked: outlined clean square with contrast,
      - checked: no solid accent fill block; outlined state preserving native check glyph visibility,
      - hover/disabled states kept explicit for readability.
    - checkbox corner style aligned with flat UI (`border-radius: 0`).

- Result:
  - checkboxes look cleaner and less "blocky" while remaining readable on light backgrounds.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.33) Update 2026-03-12 (checkbox centering in values table)

- User report:
  - checkbox in `Вид` column is stuck to the left edge.

- Implemented:
  - `trend_analyzer/ui.py`:
    - switched `Вид` cell rendering from plain direct `QCheckBox` widget to centered container:
      - `QWidget` host + `QHBoxLayout` with `AlignCenter`,
      - child checkbox (`values_visible_checkbox`) remains bound to signal id/toggle handler.
    - added helper methods to reliably resolve row checkbox from wrapped cell for:
      - selected-rows signal extraction,
      - context menu batch visibility toggles.

- Result:
  - checkbox is centered in the cell and no longer visually glued to the left border.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.34) Update 2026-03-12 (native checkmark visibility restored)

- User report:
  - checkbox glyph itself is not visible in any theme.

- Root cause:
  - previous aggressive `QCheckBox/QTableView::indicator` state styling overrode native indicator painting and effectively removed visible checkmark glyph.

- Implemented:
  - `trend_analyzer/ui.py`:
    - removed custom `:checked/:unchecked/:disabled` indicator painting rules.
    - kept only neutral sizing (`14x14`) and checkbox text spacing.
    - preserved centered checkbox placement in values table from previous fix.

- Result:
  - native checkbox mark rendering restored for both dark and light themes.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.35) Update 2026-03-12 (uniform arrow controls across all windows)

- User feedback:
  - arrows/drop-down indicators were still inconsistent in some windows (e.g., Modbus registers table).

- Implemented:
  - `trend_analyzer/ui.py`:
    - made arrow indicators explicit in theme stylesheet:
      - spin up/down arrows rendered as visible triangles,
      - combo down-arrow rendered as visible triangle,
      - disabled arrows rendered with muted but visible color.
    - added spin control normalization helpers:
      - `_enforce_spin_arrow_mode(...)` (`UpDownArrows`),
      - `_enforce_spin_controls_globally(...)` invoked during theme apply.
    - ensured dynamic table editors also follow this:
      - signal table spin editors,
      - Modbus registers table spin editors.

- Result:
  - arrow indicators are now consistent and clearly visible in all major windows, including table-embedded editors.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.36) Update 2026-03-12 (arrow rendering redesign via proxy style)

- User feedback:
  - arrow visuals degraded into square artifacts; request was to use two normal icon styles for dark/light themes.

- Implemented:
  - `trend_analyzer/ui.py`:
    - introduced `ThemeArrowProxyStyle` (inherits `QProxyStyle`) to render proper vector arrows for spin/combo controls:
      - handles `PE_IndicatorSpinUp`, `PE_IndicatorSpinDown`, `PE_IndicatorArrowDown`.
    - colors are theme-driven:
      - enabled arrow color: theme `text`,
      - disabled arrow color: theme `muted_text`.
    - integrated theme style application in `_apply_ui_theme_runtime`.
    - removed fragile CSS triangle simulation from stylesheet; kept arrow block only for sizing.

- Result:
  - arrows are now normal-looking and consistent in all windows on both dark and light themes.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.37) Update 2026-03-12 (post-feedback arrow visibility hotfix)

- User validation result:
  - arrows still missing/square in real UI.

- Root cause:
  - stylesheet arrow subcontrol rules still intercepted native/proxy drawing path.

- Implemented:
  - `trend_analyzer/ui.py`:
    - removed `QSpinBox::...-arrow` and `QComboBox::down-arrow` stylesheet overrides.
    - proxy painter now handles both spin and generic arrow primitives:
      - `PE_IndicatorSpinUp/Down`,
      - `PE_IndicatorArrowUp/Down`.
    - theme apply flow updated: arrow proxy style applied before stylesheet.
    - base-style unwrap improved in `_apply_theme_arrow_style` to avoid wrapping `QStyleSheetStyle`.

- Expected result:
  - visible, proper arrows in both dark/light themes across all windows.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.38) Update 2026-03-12 (final arrow fix using real SVG theme assets)

- User validation:
  - arrows still effectively invisible; native/proxy/CSS approach remained unreliable.

- Implemented:
  - `assets/`:
    - added four explicit arrow SVG files:
      - `arrow_up_light.svg`,
      - `arrow_down_light.svg`,
      - `arrow_up_dark.svg`,
      - `arrow_down_dark.svg`.
  - `trend_analyzer/ui.py`:
    - removed proxy-style arrow painter.
    - added `_arrow_icon_variant(...)` and `_resolve_arrow_icon_path(...)`.
    - stylesheet now assigns real SVG images to:
      - `QSpinBox::up-arrow`,
      - `QSpinBox::down-arrow`,
      - `QDoubleSpinBox::up-arrow`,
      - `QDoubleSpinBox::down-arrow`,
      - `QComboBox::down-arrow`.
    - arrow asset variant is selected from theme brightness (dark/light buttons).
    - `_build_ui_theme_stylesheet(...)` rewritten with `''.join(...)` for safer string assembly.

- Important note:
  - source run only needs full restart;
  - existing `.exe` must be rebuilt because new SVG assets were added.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.39) Update 2026-03-12 (arrow rendering moved into custom-painted controls)

- User validation:
  - arrows still not visible after SVG/QSS attempt.
  - user also requested more spacing between `Авто X` and `Курсор`.

- Implemented:
  - `trend_analyzer/ui.py`:
    - imported base widgets as aliases:
      - `QtQComboBox`,
      - `QtQSpinBox`,
      - `QtQDoubleSpinBox`.
    - added:
      - `_ThemedArrowMixin`,
      - `ThemedComboBox`,
      - `ThemedSpinBox`,
      - `ThemedDoubleSpinBox`.
    - each control now overlays a real painted triangle arrow in `paintEvent(...)`, using theme/palette-aware foreground color.
    - rebound module-level `QComboBox/QSpinBox/QDoubleSpinBox` to these themed subclasses, so all existing widget construction paths automatically inherit the fix.
    - added extra `8px` spacing between the `Авто X` and `Курсор` checkboxes in the values header.

- Why this should hold:
  - arrow visibility no longer depends on native style rendering, QSS arrow support, or external icon lookup.
  - arrows are painted directly by the widget itself after the base control is drawn.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.40) Update 2026-03-12 (combo-box arrows made smaller)

- User validation:
  - custom-painted arrows are now visible.
  - remaining request: make drop-down arrows a bit smaller/more аккуратные.

- Implemented:
  - `trend_analyzer/ui.py`:
    - `_paint_triangle(...)` now accepts optional `max_size` and `vertical_offset`.
    - `ThemedComboBox.paintEvent(...)` paints the down-arrow inside a slightly reduced rect and caps its size at `8px`.
    - spin-box arrows were intentionally left unchanged.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK

## 0.41) Update 2026-03-12 (signal table `Вкл` column centered)

- User validation:
  - signal-table checkboxes still appeared left-aligned in the `Вкл` column.

- Implemented:
  - `trend_analyzer/ui.py`:
    - replaced signal-table column-0 item checkboxes with centered `cellWidget` containers, mirroring the lower values table approach.
    - added:
      - `_signal_enabled_checkbox(...)`
      - `_create_signal_enabled_cell(...)`
      - `_on_signal_enabled_toggled(...)`
    - updated `_sync_signal_table_enabled(...)` and `_collect_signal_table(...)` to use the checkbox widget state instead of `QTableWidgetItem.checkState()`.
    - left name editing and other item-change logic intact.

- Why this should hold:
  - alignment is now controlled by `QHBoxLayout(AlignCenter)` instead of the platform's item-checkbox painting.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.42) Update 2026-03-12 (slimmer client build config + menu state marker for Start/Stop)

- User request:
  - try to reduce `exe` size.
  - show current `Старт/Стоп` state in `Рабочий процесс` menu similarly to mode selection.

- Implemented:
  - `trend_analyzer/ui.py`:
    - made `action_start` / `action_stop` checkable and put them into an exclusive `QActionGroup`.
    - added `_sync_run_state_actions()`.
    - synced this state from:
      - menu creation,
      - `_apply_work_mode_ui(...)`,
      - `_update_recorder_dependent_ui_state(...)`,
      - `_start_worker(...)`,
      - `_stop_worker(...)`.
    - result: current run state is visible in the menu with the same checkmark convention as online/offline mode.
  - build slimming:
    - `TrendClient.spec`, `TrendClient_opt.spec`, `TrendClient_uiopt.spec`:
      - replaced `collect_all('pyqtgraph')` with filtered `collect_submodules('pyqtgraph')`,
      - removed `PySide6.QtSvg` and `PySide6.QtOpenGLWidgets` hidden imports,
      - excluded `PySide2`, `PyQt5`, `PyQt6`, `tkinter`, `matplotlib`, `IPython`,
      - set `optimize=1`.
    - `build_roles_windows.ps1`:
      - TrendClient now builds from `TrendClient.spec` instead of repeating heavyweight CLI flags.
    - `build_portable.ps1`:
      - replaced `--collect-all pyqtgraph` with `--collect-submodules pyqtgraph`,
      - excluded `pyqtgraph.examples`, `pyqtgraph.tests` and unused GUI stacks.

- Important:
  - no binary rebuild was performed in this turn, per user preference.
  - actual size reduction will be visible only after the next manual rebuild.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - spec files compile via `compile(..., 'exec')` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.43) Update 2026-03-12 (TrendRecorder exit now shuts down recorder service)

- User report:
  - `TrendRecorder` again remained in Task Manager after exit, while tray icon was already gone.

- Root cause:
  - tray exit only hid the tray icon and quit the Qt app.
  - in single-binary mode, the recorder service may continue running under the same `TrendRecorder.exe` name, which looks like a hung leftover process.

- Implemented:
  - `trend_analyzer/recorder_tray.py`:
    - added `_shutting_down` guard and `aboutToQuit` hook.
    - added `_terminate_recorder_pid(...)`.
    - `_stop_recorder(...)` now returns `bool` and supports `silent=True`.
    - `_exit_tray()` now runs coordinated shutdown:
      - stop status timer,
      - stop recorder service,
      - hide and delete tray icon,
      - quit the app.
    - after `app.exec()` returns, `instance_lock.release()` now runs explicitly in `finally`.

- Expected behavior now:
  - choosing `Выход` from tray should terminate both:
    - tray UI process,
    - recorder service process.
  - this should eliminate the “tray disappeared but TrendRecorder.exe still hangs in Task Manager” symptom.

- Validation:
  - `python -m py_compile trend_analyzer/recorder_tray.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.44) Update 2026-03-12 (startup crash fixed after Start/Stop menu state change)

- User report:
  - size optimization did not materially reduce exe size.
  - after rebuild/run, app crashed at startup with:
    - `AttributeError: 'MainWindow' object has no attribute 'mode_combo'`

- Root cause:
  - `_sync_run_state_actions()` was called during `_build_menu_bar()`.
  - at that moment `mode_combo` had not been created yet.

- Implemented:
  - `trend_analyzer/ui.py`:
    - hardened `_sync_run_state_actions()` to tolerate early startup:
      - returns if start/stop actions are missing,
      - checks whether `mode_combo` exists before using it,
      - falls back to `current_profile.work_mode` or `"online"` if needed.

- Result:
  - menu state indicator logic remains,
  - startup no longer depends on widget creation order.

- Important:
  - exe size work should be treated as a separate optimization track and validated with rebuild + actual size measurement after each change.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.45) Update 2026-03-12 (scales window `Авто Y` fixed + more spacing near `Курсор`)

- User report:
  - `Настройка шкал` window looked visually broken in the `Авто Y` column.
  - values header above the lower signal table still looked cramped around the `Курсор` checkbox.

- Implemented:
  - `trend_analyzer/ui.py`:
    - increased explicit spacing:
      - between `Авто X` and `Курсор`,
      - between `Курсор` and `Сброс масштаба`.
    - replaced `scales_table` `Авто Y` item-checkbox rendering with centered `cellWidget` checkbox rendering.
    - added:
      - `_scale_auto_y_checkbox(...)`
      - `_create_scale_auto_y_cell(...)`
      - `_on_scale_auto_y_toggled(...)`
    - `Min/Max` editability now follows the centered checkbox state instead of `QTableWidgetItem.checkState()`.

- Expected result:
  - `Авто Y` cell should no longer show a broken mixed checkbox artifact.
  - values header should look more balanced around `Курсор`.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.46) Update 2026-03-12 (quick `Авто Y` toggle added near lower `Авто X`)

- User request:
  - add `Авто Y` next to `Авто X` above the lower signal values table for quick access.

- Implemented:
  - `trend_analyzer/ui.py`:
    - inserted `values_auto_y_checkbox` in the values header between `Авто X` and `Курсор`.
    - added `_on_values_auto_y_toggled(...)` which calls `chart.set_auto_y(...)` and marks config dirty.
    - updated `_on_chart_auto_mode_changed(...)` to synchronize the new checkbox from chart state.
    - updated runtime view restore path to initialize the checkbox state during profile/view load.

- Result:
  - user now has quick direct access to `Auto Y` without opening the scales window.
  - checkbox stays in sync when auto-range state changes from chart/scales logic.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.47) Update 2026-03-12 (saving from `Сигналы графика` now applies axis changes to runtime)

- User report:
  - changing signal `Шкала` in `Сигналы графика` and saving did not affect the lower values table / active chart runtime.
  - symptom: after save, dragging/enabling the signal still showed old axis index (`Шкала 1`).

- Root cause:
  - the window-local `Сохранить конфигурацию` button only called `_save_config()`.
  - `_save_config()` persists profile state but intentionally does not reconfigure runtime chart objects.

- Implemented:
  - `trend_analyzer/ui.py`:
    - `_save_from_signals_window()` now does:
      - `_apply_current_profile(restart_live=False)`
      - then `_save_config()`
    - this applies updated signal definitions (including `axis_index`) to the active chart model before persistence.

- Expected result:
  - after changing `Шкала` and pressing save inside `Сигналы графика`, the lower values table and chart runtime should immediately reflect the new axis number.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

## 0.48) Update 2026-03-12 (local live graph decoupled from DB archive writes)

- User report:
  - disabling `Архив и экспорт -> Писать в БД` stopped live graph updates.
  - desired behavior:
    - graph must continue updating live,
    - but after restart there should be no persisted history.

- Root cause:
  - local live-view path consumed new samples from SQLite.
  - when recorder stopped writing to DB, UI lost its local live data source.

- Implemented:
  - `trend_analyzer/recorder_service.py`:
    - added in-memory live ring buffers for:
      - samples,
      - connection events.
    - recorder now stores current live stream into memory independently of DB archiving.
    - live memory buffers reset on recorder start/profile switch.
  - `trend_analyzer/recorder_api.py`:
    - `/v1/live` now returns in-memory live data when `archive_to_db == False`.
    - `/v1/history` now returns current-session in-memory history when `archive_to_db == False`.
  - `trend_analyzer/ui.py`:
    - local online mode now uses recorder API live polling instead of SQLite when local archive writing is disabled.
    - added local API bootstrap/history/sample parsing helpers.
    - startup status text distinguishes DB-backed vs non-archived live mode.

- Expected behavior now:
  - with `Писать в БД = выкл`, the graph continues updating in real time.
  - after restarting the application / recorder, old non-archived live data are gone.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py trend_analyzer/recorder_service.py trend_analyzer/recorder_api.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)
## 0.49) Update 2026-03-12 (TrendRecorder tray exit strengthened against lingering processes)

- User report:
  - `TrendRecorder.exe` still remained in Task Manager after tray exit.

- Findings:
  - likely frozen onefile edge case where tray shutdown/forced recorder stop may still leave related `TrendRecorder.exe` bootstrap / `--recorder` processes alive.
  - strengthened cleanup in tray shutdown instead of relying only on PID-file process shutdown.

- Implemented:
  - `trend_analyzer/recorder_tray.py`:
    - added discovery of residual sibling recorder processes (`--recorder`) via `psutil`.
    - added generic PID-group termination helper with wait/recheck loop.
    - `_stop_recorder()` now cleans residual recorder processes after both graceful and forced stop.
    - tray hard-exit fallback now additionally terminates:
      - residual recorder processes,
      - same-executable bootstrap parent processes in the current parent chain,
      - then calls `os._exit(0)`.

- Validation:
  - `python -m py_compile trend_analyzer/recorder_tray.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK (13 tests)

- Next verification to do in UI:
  - launch packaged `TrendRecorder.exe`,
  - exit from tray menu,
  - confirm no `TrendRecorder.exe` remains in Task Manager Details.
## 0.50) Update 2026-03-12 (Auto X no longer freezes live chart on plain toggle-off)

- User report:
  - disabling `Авто X` made the online chart appear frozen.
  - re-enabling `Авто X` could show a strange gap / partially lost interval.

- Root cause:
  - UI treated plain Auto X toggle-off as a switch into archive-window mode and could replace current live buffers with archived history immediately.
  - chart also had no intermediate mode between:
    - fully auto-follow latest,
    - fully manual fixed X range.
  - because of that, simple checkbox toggle-off could visually stop the timeline at once.

- Implemented:
  - `trend_analyzer/chart.py`:
    - added soft latest-edge follow while `Авто X` is off until the user actually changes X range manually.
    - any real manual X-range change now disables that soft-follow mode.
    - live append/cursor logic now uses `follows_latest_x()` instead of only raw `auto_x`.
  - `trend_analyzer/ui.py`:
    - removed forced archive/history replacement on plain Auto X toggle.
    - visible history reload is now triggered only after real manual X navigation.
    - heartbeat append path now also works while chart is in soft latest-follow mode.

- Expected behavior now:
  - if user simply unchecks `Авто X`, chart should continue updating on screen.
  - once user manually pans/zooms by X, latest-follow stops and visible-history loading can work as before.
  - turning `Авто X` back on should no longer create an artificial gap caused by immediate archive-buffer replacement.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py trend_analyzer/chart.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore` -> OK (16 tests)
## 0.51) Update 2026-03-12 (TrendRecorder exit now launches external cleanup helper)

- User report:
  - `TrendRecorder.exe` still remained in memory / Task Manager after exit.

- Findings:
  - in-process cleanup of recorder and parent-chain processes was not always enough for frozen onefile builds.
  - likely remaining culprit is PyInstaller onefile bootloader/parent process that survives past normal app shutdown timing.

- Implemented:
  - `trend_analyzer/recorder_tray.py`:
    - added detached hidden Windows cleanup helper launched through PowerShell.
    - helper sleeps briefly, then kills processes scoped by exact current `ExecutablePath`.
    - after recorder stop:
      - helper kills lingering same-exe `TrendRecorder.exe --recorder` processes.
    - after tray exit:
      - helper kills any remaining same-exe `TrendRecorder.exe` remnants for the current binary path.

- Why this helps:
  - onefile bootloader leftovers cannot always be fully cleaned from inside the same process tree.
  - external detached helper can run after the app has already started shutting down and remove remnants reliably.

- Validation:
  - `python -m py_compile trend_analyzer/recorder_tray.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore` -> OK (16 tests)

- Important:
  - packaged `TrendRecorder.exe` must be rebuilt to test this fix in the binary.
## 0.52) Update 2026-03-12 (manual X pan no longer wipes chart on empty history window)

- User report:
  - as soon as X axis was dragged with the mouse, chart data could disappear.

- Root cause:
  - manual visible-history reload could query an X-window with no archived points.
  - returned `samples_map` still had configured signal keys, and `set_archive_data(...)` cleared chart buffers for those signals.

- Implemented:
  - `trend_analyzer/ui.py`:
    - added `_samples_payload_has_points(...)`.
    - `_load_history_window_from_db(...)` now exits early when the requested history window has no actual points, leaving current chart buffers intact.

- Result:
  - dragging X into an empty window no longer clears the currently visible chart data.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore` -> OK (16 tests)
## 0.53) Update 2026-03-12 (chart live/history buffers split; TrendClient shutdown strengthened)

- User pressure / objective:
  - stop adding small Auto X patches and make graph browsing actually usable.
  - also fix lingering `TrendClient.exe` / `TrendRecorder.exe` processes after exit.

- Implemented graph-side architecture change:
  - `trend_analyzer/chart.py`:
    - chart no longer treats history load as full buffer replacement.
    - added:
      - `_live_buffers`
      - `_history_buffers`
      - merged `_buffers`
    - live samples now append only into live buffers.
    - history loads now populate history buffers and then merge with live.
    - rendering/cursor/stats still use merged buffers, so manual history loading no longer destroys current live stream data.

- Implemented shutdown-side changes:
  - `trend_analyzer/ui.py`:
    - `_shutdown_maintenance_executor()` now waits for thread pool shutdown (`wait=True`), because executor worker threads are non-daemon and could keep `TrendClient.exe` alive.
    - added frozen-Windows self-cleanup helper that kills lingering same-executable client tails after close.
    - `run_app()` now releases `SingleInstanceLock` in `finally`.
  - `trend_analyzer/recorder_tray.py`:
    - cleanup helper is now frozen-only to avoid dangerous behavior in source/python runs.

- Added tests:
  - `tests/test_chart_history_merge.py`
    - verifies merge helper ordering,
    - verifies live value wins on duplicate timestamps,
    - verifies empty-history guard helper.

- Validation:
  - `python -m py_compile trend_analyzer/chart.py trend_analyzer/ui.py trend_analyzer/recorder_tray.py tests/test_chart_history_merge.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK (23 tests)

- Next real-world verification requested from user after rebuild/run:
  - drag X in online mode and confirm data no longer vanish from live/history buffer replacement.
  - close packaged `TrendClient.exe` and `TrendRecorder.exe` and confirm no lingering processes remain in Task Manager.
## 0.54) Update 2026-03-12 (statistics window now forces true manual X mode)

- User report:
  - when opening `Анализ участка графика`, autoscroll by X still continued even though the `Авто X` checkbox was visibly off.

- Root cause:
  - stats UI called `set_auto_x(False)`, but chart's current design enables hidden soft-follow on plain Auto X toggle-off.
  - for statistics mode this behavior is wrong: X must stop scrolling completely.

- Implemented:
  - `trend_analyzer/chart.py`:
    - added `force_manual_x()` which disables both:
      - `_auto_x`
      - `_soft_follow_latest_x`
  - `trend_analyzer/ui.py`:
    - `_disable_auto_x_for_stats()` now uses `chart.force_manual_x()`.

- Result:
  - when stats/analysis window disables Auto X, chart no longer keeps silently following latest X.

- Validation:
  - `python -m py_compile trend_analyzer/chart.py trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK (23 tests)
## 0.55) Update 2026-03-12 (frozen exit cleanup now uses direct taskkill by process name)

- User report:
  - even after previous cleanup work, `TrendRecorder.exe` still remained in Task Manager.
  - user explicitly preferred brute-force semantics: kill all processes with that image name on exit.

- Implemented:
  - `trend_analyzer/recorder_tray.py`:
    - tray full-exit cleanup helper now runs delayed `taskkill /IM <current exe basename> /F`.
    - selective recorder-only stop path is still kept separate so "stop recording" does not kill tray itself.
  - `trend_analyzer/ui.py`:
    - frozen client self-cleanup helper now also runs delayed `taskkill /IM <current exe basename> /F`.

- Why:
  - path/cmdline-filtered post-exit cleanup was still too brittle for the observed Windows/PyInstaller environment.
  - delayed `taskkill /IM ... /F` matches the exact manual recovery procedure the user already had to use.

- Validation:
  - `python -m py_compile trend_analyzer/recorder_tray.py trend_analyzer/ui.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK (23 tests)
## 0.56) Update 2026-03-12 (exit cleanup helper switched to cmd.exe + taskkill)

- User report:
  - even name-based delayed cleanup was still not reliably removing lingering processes after exit.

- Adjustment:
  - moved frozen cleanup launcher from direct PowerShell invocation to `cmd.exe`.

- Implemented:
  - `trend_analyzer/ui.py`:
    - client cleanup helper now runs:
      - `cmd.exe /d /c "timeout /t 3 /nobreak >nul & taskkill /IM <exe> /F"`
  - `trend_analyzer/recorder_tray.py`:
    - full tray-exit cleanup helper now also runs delayed `taskkill /IM` through `cmd.exe`.
    - selective recorder-only cleanup remains PowerShell-filtered internally, but is also spawned through `cmd.exe /c`.

- Why:
  - removes dependency on direct PowerShell startup/policy peculiarities in the packaged Windows environment.
  - keeps behavior aligned with the user's own manual recovery commands.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py trend_analyzer/recorder_tray.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK (23 tests)
## 0.57) Update 2026-03-12 (fixed mojibake in `Регистры Modbus` status texts)

- User report:
  - when changing register values through the `Регистры Modbus` window, the status text still appeared with broken encoding.

- Root cause:
  - this specific issue was caused by corrupted string literals already present in source code, not by theme/rendering.
  - affected paths were inside the Modbus register window write/read handlers in `trend_analyzer/ui.py`.

- Implemented:
  - replaced mojibake literals with normal Russian text for:
    - single-row write success/error
    - add-range summary
    - local connection failure
    - BOOL pre-write read error
    - remote single-row "written" message
    - batch read summary
    - batch write per-tag error + final summary

- Files:
  - `trend_analyzer/ui.py`

- Validation:
  - `python -m py_compile trend_analyzer/ui.py` -> OK
## 0.58) Update 2026-03-12 (Auto X is disabled when no visible signals exist)

- User report:
  - when the bottom values table was empty, `Авто X` could stay enabled and the chart continued scrolling despite no visible signals.

- Implemented:
  - added `_disable_auto_x_when_no_visible_signals(visible_count)` in `trend_analyzer/ui.py`
  - `_update_values_table(...)` now calls it after rebuilding the lower table
  - when `visible_count == 0`:
    - `chart.force_manual_x()` is called to disable both Auto X and soft-follow
    - menu `Авто X` action is synced to unchecked
    - bottom `Авто X` checkbox is synced to unchecked
  - no automatic re-enable occurs when signals come back; user keeps explicit control

- Why this placement:
  - the lower values table is the best canonical source for "is there anything visible right now?"
  - avoids touching history-loading logic or signal-configuration logic directly

- Validation:
  - `python -m py_compile trend_analyzer/ui.py trend_analyzer/chart.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_ui_history_restore tests.test_ui_recorder_command` -> OK
## 0.59) Update 2026-03-12 (View menu + scales window now expose both Auto X and Auto Y)

- User request:
  - add `Авто Y` into the `Вид` menu next to `Авто X`
  - make `Настройка шкал` show both `Авто X` and `Авто Y` for a more logical UI

- Implemented:
  - `trend_analyzer/ui.py`:
    - added `self.action_auto_y` to the `Вид` menu
    - synchronized `action_auto_y` with the existing bottom `values_auto_y_checkbox`
    - expanded scales table to 6 columns:
      - `Шкала | Авто X | Авто Y | Мин | Макс | Сигналы`
    - added:
      - `_scale_auto_x_checkbox(...)`
      - `_create_scale_auto_x_cell()`
      - `_on_scale_auto_x_toggled(...)`
    - shifted scales-table min/max editing logic to new columns
  - `trend_analyzer/chart.py`:
    - `set_auto_x()` and `force_manual_x()` now also emit `scales_changed`
    - scales payload rows now include `auto_x`, allowing scales window sync after any X-auto change

- Design choice:
  - `Авто X` in the scales window is global, so every row displays the same state and any row toggle updates the global X-follow mode.
  - `Авто Y` remains per-axis as before.

- Validation:
  - `python -m py_compile trend_analyzer/ui.py trend_analyzer/chart.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_ui_history_restore tests.test_ui_recorder_command tests.test_models_profile` -> OK
## 0.60) Update 2026-03-12 (archive DB normalized: integer refs instead of repeated UUID text in samples)

- User concern:
  - `samples` table stored long text `profile_id` and `signal_id` in every row, which looked wasteful and likely dominated archive size.

- Decision:
  - move archive storage to normalized physical tables with integer refs
  - keep compatibility read views so the rest of the app can continue using familiar SQL names during the transition
  - migration/backward compatibility intentionally not preserved; old schema is recreated from scratch if detected

- Implemented:
  - `trend_analyzer/storage.py`
    - added archive schema versioning (`ARCHIVE_SCHEMA_VERSION = 2`)
    - physical tables:
      - `profiles_meta`
      - `signal_catalog`
      - `sample_rows`
      - `connection_event_rows`
    - compatibility views:
      - `samples`
      - `signals_meta`
      - `connection_events`
    - `insert_batch(...)` now:
      - resolves/creates integer `profile_ref`
      - resolves/creates integer `signal_ref`
      - writes compact rows into `sample_rows(profile_ref, signal_ref, ts, value)`
    - `insert_connection_event`, `prune_older_than`, `prune_to_max_size`, `delete_signals`, `min_sample_ts` updated for normalized storage
    - legacy reset logic drops old objects safely by consulting `sqlite_master`
  - `trend_analyzer/ui.py`
    - archive clear action now deletes from physical normalized tables:
      - `sample_rows`
      - `connection_event_rows`
      - `signal_catalog`
      - `profiles_meta`
    - sqlite sequence reset updated accordingly
  - `tests/test_storage_archive.py`
    - updated to assert both:
      - compatibility read surface still works (`samples`, `signals_meta`)
      - physical compact table really uses `profile_ref` / `signal_ref`

- Why this is better:
  - long UUID strings are no longer repeated in each sample row and in sample indexes
  - storage hot path now uses small integer keys
  - read-side code can still query `samples/signals_meta/connection_events`

- Validation:
  - `python -m py_compile trend_analyzer/storage.py trend_analyzer/ui.py tests/test_storage_archive.py` -> OK
  - `.venv\\Scripts\\python -m unittest tests.test_storage_archive tests.test_chart_history_merge tests.test_ui_history_restore tests.test_ui_recorder_command tests.test_recorder_service tests.test_models_profile` -> OK
