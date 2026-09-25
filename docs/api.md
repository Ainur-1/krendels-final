# API

Полная спецификация порождается из кода — [openapi.json](openapi.json), а у работающего сервиса она открыта на `/docs`. CI сверяет выгруженный файл с кодом (`scripts/export_openapi.py --check`), поэтому документ не может отстать незаметно. Здесь — то, чего спецификация не говорит: подлинность, права, ошибки и сценарии использования.

## Подлинность

| кто | как | где |
|---|---|---|
| пользователь | `Authorization: Bearer <токен>`; токен подписан HMAC-SHA256 и имеет срок действия | все эндпоинты, кроме `/api/health` |
| edge-источник | `X-Source-Id` и `X-Signature` — HMAC-SHA256 тела запроса ключом источника | `POST /api/events` |

В демонстрационном режиме токен выдаёт `POST /api/auth/demo-login {"user_id": "ctrl-01"}`; список пользователей — `GET /api/users`. В рабочем режиме оба эндпоинта отключены, токен выдаёт администратор командой `scripts/keys.py token`.

## Эндпоинты

| метод и путь | право | что делает |
|---|---|---|
| `GET /api/health` | — | живость и число записей журнала |
| `GET /api/me` | любой | текущий пользователь и его права |
| `POST /api/events` | `ingest` | приём одного события, списка или `{"events": [...]}`; ответ по каждому сообщению: `accepted`, `duplicate` или `rejected` с ошибками, предупреждения и признаки (`late`, `out_of_order`, `clock_skew`) |
| `GET /api/line` | `read` | участки, операции в работе, оборудование, пропуски источников, последние события |
| `GET /api/items[?status=]` | `read` | изделия со статусами |
| `GET /api/items/{id}` | `read` | история изделия: компоненты, операции с длительностями и их происхождением, проверки, несоответствия, хронология |
| `GET /api/nonconformances[?status=]` | `read` | карточки |
| `GET /api/nonconformances/{id}` | `read` | карточка: исходные сообщения как пришли, разбор системы, решения, материалы, допустимые действия текущего пользователя |
| `POST /api/nonconformances/{id}/decisions` | `decide` или `set_cause` | решение: `start_review`, `request_recheck`, `confirm`, `reject`, `close`, `reopen`, `confirm_cause` (+ `cause_category`); обоснование обязательно |
| `GET /api/metrics` | `read` | производственная аналитика и сводка приёма |
| `GET /api/quarantine` | `read` | отклонённые сообщения с ошибками |
| `GET /api/integrity` | `admin` | проверка цепочки, подписей и якоря |
| `GET /api/audit` | `admin` | журнал критических действий |
| `GET /api/keys`, `POST /api/keys/rotate` | `admin` | ключи и профили; выпуск нового ключа |
| `GET /api/integration`, `POST /api/integration/sync` | `read` / `integrate` | исходящая очередь, сопоставление идентификаторов, журнал обмена; синхронизация |
| `GET /api/export/ocel` | `export` | выгрузка истории в OCEL 2.0 |
| `GET /api/contracts`, `GET /api/contracts/{файл}` | `read` | схемы контракта событий |
| `GET /api/ops/metrics` | `read` | телеметрия сервиса: счётчики и распределения времени |
| `GET /api/auth/options`, `POST /api/auth/login` | — | что показать на экране входа; вход по паролю |
| `GET /api/lines`, `GET /api/lines/{id}` | `read` | линии и конфигурация графа |
| `POST /api/lines` | `line_manage` | новая линия из этапов по порядку |
| `PUT /api/lines/{id}/economics` | `line_manage` | параметры экономики линии |
| `GET /api/lines/{id}/live` | `read` | граф с состоянием этапов, изделия в работе, тревоги, состояние эмуляции |
| `GET /api/lines/{id}/stages[?item_type&shift&since&until]` | `read` | статистика всех этапов за срез |
| `GET /api/lines/{id}/stages/{node}` | `read` | этап: статистика, изделия, несоответствия, возникшие и обнаруженные здесь |
| `GET /api/lines/{id}/items[?item_type&status&stage&q]` | `read` | изделия линии с фильтрами по столбцам |
| `GET /api/lines/{id}/overview` | `read` | сводка для панелей ролей |
| `GET /api/lines/{id}/economics` | `read` | экономика линии |
| `GET /api/items/{id}/path` | `read` | маршрут изделия A → B → C с итогом на каждом этапе |
| `POST /api/lines/{id}/emulation`, `POST /api/lines/{id}/emulation/inject` | `emulate` | пуск и остановка эмуляции, дефект или отклонение станка в выбранный этап |
| `GET /api/flows/example`, `POST /api/flows` | `read` / `line_manage` | пример потока и загрузка своего ([flows.md](flows.md)) |
| `GET /api/admin/users` | `admin` | пользователи, роли, задан ли пароль |

## Ошибки

| код | когда |
|---|---|
| 400 | тело запроса — не JSON |
| 401 | нет токена, токен повреждён или истёк; подпись пакета не сходится |
| 403 | у роли нет права; отказ записан в журнал критических действий |
| 404 | нет изделия или карточки |
| 409 | решение недопустимо в текущем статусе карточки или без обоснования |
| 422 | неизвестный криптографический профиль |

Ошибки содержания событий — не код ответа: пакет принимается, а в ответе по каждому сообщению перечислены проблемы с путём до поля и стабильным кодом (`missing_field`, `unknown_enum_value`, `unknown_schema_version`, `unknown_event_type`, `conflicting_duplicate`, `invalid_value`). Так один плохой источник не блокирует пакет целиком, а отклонённое сообщение не теряется — оно в карантине.
