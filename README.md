# AI Agent Worker

Локальный каркас AI-агента на FastAPI с отдельным orchestration layer, LM Studio как текущим LLM backend, опциональной локальной памятью и безопасным набором инструментов для работы с проектом.

Worker `0.4.0` содержит безопасный tool-calling foundation, persistent Run API и
изолированный coding workflow в task worktree.
Один LLM-шаг Planner выполняется через ограниченный loop, автоматически запускающий
только read-only tools. Runs, events, sessions и approvals сохраняются в SQLite/WAL.

## Что уже есть

- FastAPI gateway с `/health`, compatibility `/api/v1/chat`, Run API, SSE и cancel.
- Опциональная авторизация через `X-API-Key` или `Authorization: Bearer ...`.
- Request ID, базовый rate limit, структурированные ошибки и логирование в консоль/файл.
- Orchestrator Core с router, context builder, planner, dispatcher, verifier, result synthesizer и persistent session manager.
- LM Studio provider через OpenAI-compatible endpoint `/chat/completions`.
- Абстракция памяти `IMemoryService`, `NoOpMemoryService` по умолчанию и JSONL backend при включении.
- Фильтр чувствительных данных перед сохранением памяти.
- Tool registry и безопасные tools для файлов, поиска по проекту, read-only git и ограниченного запуска команд.
- Типизированные `LLMResponse`, `ToolCall`, `ToolResult`, JSON Schema tools и ограниченный LLM-driven execution loop.
- Code verifier для coding-route при `metadata.verify_code=true`.
- Явный pin на `ai-agent-contracts` protocol `0.1.0` с checksum schema.
- Workspace registry: новый Run принимает `workspace_id`, а не клиентский raw path.
- State machine `queued/running/waiting_approval/verifying/completed/failed/cancelled`.
- Append-only `RunEvent`, восстановление timeline после restart и один execution lock
  на workspace.
- Task branch/worktree от committed SHA без stash/reset исходного workspace.
- Детерминированный `MutationPreview`, approval по `preview_hash` и stale-state check
  перед атомарной записью.
- Verification/diff report и опциональный локальный commit; push, clean, reset и
  удаление worktree не поддерживаются.
- Тесты для API, роутинга, инструментов, памяти, ошибок, настроек и верификации.

## Структура проекта

```text
app/
  main.py                         # создание FastAPI app, middleware, healthcheck
  api/
    routes/chat.py                # сборка orchestrator и POST /api/v1/chat
  config/
    settings.py                   # env-настройки через pydantic-settings
  errors.py                       # AppError и доменные ошибки
  orchestrator/
    core.py                       # основной pipeline обработки запроса
    approval/store.py             # одноразовые pending approvals с TTL
    context/builder.py            # история сессии + recalled memory + system prompt
    execution/tool_dispatcher.py  # выполнение tool steps через registry
    planning/planner.py           # активный planner, формирует LLM-шаг
    routing/router.py             # simple route: general/architecture/coding/research
    session/manager.py            # in-memory session state
    synthesis/result_synthesizer.py
    verification/
      verifier.py                 # проверка ответа модели на пустой результат
      code_verifier.py            # compileall, pytest, ruff при coding verification
  runs/
    models.py                     # Run state machine и persistent records
    service.py                    # background execution, recovery, cancel, approvals
  state/
    store.py                      # SQLite/WAL repositories и append-only events
    runtime.py                    # default workspace и lifecycle state store
  providers/
    llm/                          # ILLMProvider + LMStudioProvider
    memory/                       # IMemoryService, noop, json_file, policy, factory
  schemas/
    chat.py                       # ChatRequest, ChatResponse, ExecutionStep
    runs.py                       # Run/RunEvent/Workspace/Approval API contracts
  tools/
    base.py                       # ITool
    registry.py                   # ToolRegistry
    path_safety.py                # workspace policy, protected paths, safe scan
    files/                        # read_file, write_file
    git/                          # git_status, git_diff, git_log
    project/                      # scan_project, search_project
    terminal/                     # run_command без shell и с allow-list
  utils/
    logging.py
    request_context.py

docs/
  FOUNDATION_DECISIONS.md         # исторические архитектурные решения v0
  PROJECT_PLAN.md                 # генеральная дорожная карта до создания contracts repo
  PROJECT_STATE.md                # текущее состояние, следующий шаг и известные gaps
tests/                            # pytest-набор по текущим модулям
contracts.lock                    # protocol version/range и checksum source schema
```

`data/`, `logs/`, `.pytest_cache/`, `.ruff_cache/`, `.venv/`, `venv/` и `ai_agentv1.egg-info/` не являются основной архитектурой приложения. Это runtime/build/test артефакты или локальная среда.

## Базовый поток

```text
POST /api/v1/chat (compatibility adapter)
  -> require_api_key()
  -> request middleware: request_id, rate limit, logging
  -> persistent RunService.create_run()
  -> Orchestrator.handle()
  -> SessionManager.get_or_create()
  -> TaskRouter.route()
  -> ContextBuilder.build()
       - system prompt
       - последние сообщения сессии
       - recalled memory, если backend включен
  -> Planner.make_plan()
  -> bounded agent loop
       - LLMProvider.chat(tools=..., tool_choice="auto")
       - read-only tool calls через ToolDispatcher
       - tool results обратно в LLM по tool_call_id
       - stop/max_steps/max_tool_calls/deadline/duplicate protection
  -> Verifier.verify()
  -> optional CodeVerifier.verify(), если route=coding и metadata.verify_code=true
  -> memory save, если включена память и данные не чувствительные
  -> ResultSynthesizer.synthesize()
  -> RunEvent timeline + terminal Run state
  -> synchronous ChatResponse adapter
```

Автоматически выполняются только tools с `read_only=true`. `write_file`, `run_command` и любые новые mutating tools по безопасному умолчанию сначала возвращают `approval_required` и выполняются только после отдельного подтверждённого запроса той же сессии.

## Run API

Новый клиент сначала регистрирует доверенный локальный workspace, после чего
использует только его server-generated `workspace_id`:

```text
POST /api/v1/workspaces
GET  /api/v1/workspaces
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events?after=0
POST /api/v1/runs/{run_id}/cancel
POST /api/v1/approvals/{approval_id}/decision
POST /api/v1/task-worktrees
GET  /api/v1/task-worktrees/{task_id}/report
POST /api/v1/task-worktrees/{task_id}/verify
POST /api/v1/task-worktrees/{task_id}/finalize
```

SSE events имеют монотонный `sequence`, поэтому timeline можно восстановить после
перезапуска или reconnect. Interrupted `running/verifying` run после restart
становится `failed` с безопасным `worker_restarted`; queued run возобновляется,
waiting approval сохраняется.

`POST /api/v1/chat` сохранён без изменения request shape. Он создаёт Run в default
workspace и ждёт terminal/waiting-approval state. Переданный `project_path` больше
не определяет область tools.

## Инструменты

| Tool | Назначение |
| --- | --- |
| `read_file` | Читает UTF-8 файл внутри workspace с лимитом размера. |
| `write_file` | Атомарно создает или перезаписывает UTF-8 файл внутри workspace. |
| `scan_project` | Возвращает список файлов, пропуская защищенные и служебные директории. |
| `search_project` | Ищет текст по проекту, пропуская бинарные и слишком большие файлы. |
| `run_command` | Запускает ограниченные команды без shell. |
| `git_status` | Read-only `git status`. |
| `git_diff` | Read-only `git diff`, опционально по конкретному пути. |
| `git_log` | Read-only `git log --oneline`. |

Защита инструментов строится в несколько слоев:

- все пути должны оставаться внутри `TOOL_WORKSPACE_ROOT`;
- закрыт доступ к `.env`, `.git`, `.venv`, `venv`, cache/build директориям и `node_modules`;
- `run_command` не использует shell и блокирует shell operators;
- git-инструменты read-only;
- mutating tools не выполняются автоматически и возвращают `approval_required`;
- фактически разрешенные command patterns сейчас ограничены `git status/diff/log`, `uv run pytest -q`, `uv run python -m compileall app tests` и `uv run ruff check .`.

## Запуск

Требования: Python 3.12+, `uv`, запущенный LM Studio server с OpenAI-compatible API.

```powershell
uv sync
Copy-Item .env.example .env
```

Проверьте в `.env`:

```env
LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
LMSTUDIO_MODEL=google/gemma-4-e4b
```

Старт API:

```powershell
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Проверка:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Пример chat-запроса:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/chat `
  -ContentType "application/json" `
  -Body '{"message":"Привет. Кратко опиши текущий проект."}'
```

Если задан `API_KEY`, добавьте заголовок:

```powershell
-Headers @{"X-API-Key"="your-api-key"}
```

## Основные настройки

| Переменная | По умолчанию | Что делает |
| --- | --- | --- |
| `APP_ENV` | `dev` | Метка окружения в `/health`. |
| `API_KEY` | пусто | Если задан, включает защиту `/api/v1/chat`. |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `120` | Лимит запросов в минуту на client host. |
| `LOG_LEVEL` | `INFO` | Уровень логирования. |
| `LOG_TO_FILE` | `true` | Писать логи в файл. |
| `LMSTUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` | OpenAI-compatible endpoint LM Studio. |
| `LMSTUDIO_MODEL` | `google/gemma-4-e4b` | Модель для запросов к LM Studio. |
| `ENABLE_MEMORY` | `false` | Включает сохранение и recall памяти. |
| `MEMORY_BACKEND` | `noop` | Сейчас поддерживаются `noop` и `json`. |
| `MEMORY_FILE_PATH` | `data/memory/interactions.jsonl` | JSONL-файл памяти. |
| `SESSION_MAX_SESSIONS` | `200` | Максимум in-memory сессий. |
| `SESSION_MAX_MESSAGES` | `50` | Максимум сообщений в истории сессии. |
| `STATE_DB_PATH` | OS app-data/state | SQLite/WAL с runs, events, sessions и approvals. |
| `TASK_WORKTREE_ROOT` | OS app-data/worktrees | Изолированные worktrees coding-задач. |
| `RUN_EVENT_POLL_INTERVAL_SECONDS` | `0.1` | Интервал polling для SSE и sync adapter. |
| `AGENT_MAX_STEPS` | `6` | Максимум LLM-turns в одном execution loop. |
| `AGENT_MAX_TOOL_CALLS` | `10` | Общий лимит запрошенных tool calls. |
| `AGENT_TIMEOUT_SECONDS` | `120` | Общий deadline одного execution loop. |
| `APPROVAL_TTL_SECONDS` | `300` | Срок действия ожидающего подтверждения mutating tool. |
| `APPROVAL_MAX_PENDING` | `200` | Максимум ожидающих подтверждений в памяти процесса. |
| `TOOL_WORKSPACE_ROOT` | `.` | Корень, внутри которого работают tools. |
| `TOOL_MAX_FILE_BYTES` | `200000` | Лимит чтения/записи файлов и поиска. |
| `TOOL_COMMAND_TIMEOUT_SECONDS` | `30` | Таймаут команд tools и code verifier. |
| `TOOL_MAX_OUTPUT_CHARS` | `20000` | Лимит stdout/stderr в tool result. |

## Подтверждение mutating tools

Когда модель запрашивает `write_file`, `run_command` или другой tool без `read_only=true`, соответствующий `ExecutionStep` получает статус `approval_required` и серверный `payload.approval_id`. Сам tool на этом этапе не выполняется.

Для подтверждения отправьте новый запрос в ту же сессию:

```json
{
  "message": "Подтверждаю ожидающее изменение",
  "session_id": "тот-же-session-id",
  "metadata": {
    "approve_tool_call_id": "approval-id-из-предыдущего-ответа"
  }
}
```

Сервер выполняет сохраненную копию исходного `ToolCall`: передать новые аргументы через metadata нельзя. Подтверждение одноразовое, привязано к сессии и ограничено по TTL. Чужой `session_id`, повторное или просроченное подтверждение отклоняются до выполнения tool.

## Память

По умолчанию память выключена:

```env
ENABLE_MEMORY=false
MEMORY_BACKEND=noop
```

Чтобы включить локальную JSONL-память:

```env
ENABLE_MEMORY=true
MEMORY_BACKEND=json
MEMORY_FILE_PATH=data/memory/interactions.jsonl
```

Перед сохранением проверяются сообщение пользователя, metadata и ответ модели. Если найдено что-то похожее на API keys, passwords, tokens, authorization headers или private keys, запись в память пропускается.

Recalled memory передается модели как недоверенный контекст: planner явно добавляет предупреждение не выполнять инструкции из памяти.

## Верификация

Обычная верификация сейчас проверяет, что модель вернула непустой ответ.

Для coding-route можно попросить дополнительную проверку проекта:

```json
{
  "message": "Проверь кодовую часть проекта",
  "metadata": {
    "verify_code": true
  }
}
```

`CodeVerifier` выполняет:

```text
uv run python -m compileall app tests
uv run pytest -q
uv run ruff check .
```

## Тесты

```powershell
uv run pytest -q
```

Дополнительно:

```powershell
uv run python -m compileall app tests
uv run ruff check .
```

## Что планируется дальше

Актуальный порядок работ и критерии готовности хранятся в
[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md). Полная дорожная карта находится в
[`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md). После появления отдельного
`ai-agent-contracts` эти документы становятся каноническими в нём, а worker
закрепляет совместимую версию contracts.
