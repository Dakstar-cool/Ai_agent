# Ai_agentV1

Локальный каркас AI-агента на FastAPI с отдельным orchestration layer, LM Studio как текущим LLM backend, опциональной локальной памятью и безопасным набором инструментов для работы с проектом.

Проект сейчас находится на стадии foundation: основной request pipeline уже собран, интерфейсы отделены от реализаций, а инструменты зарегистрированы через общий registry. Planner пока делает один LLM-шаг и еще не строит полноценный многошаговый tool-calling loop.

## Что уже есть

- FastAPI gateway с `/health` и `POST /api/v1/chat`.
- Опциональная авторизация через `X-API-Key` или `Authorization: Bearer ...`.
- Request ID, базовый rate limit, структурированные ошибки и логирование в консоль/файл.
- Orchestrator Core с router, context builder, planner, dispatcher, verifier, result synthesizer и session manager.
- LM Studio provider через OpenAI-compatible endpoint `/chat/completions`.
- Абстракция памяти `IMemoryService`, `NoOpMemoryService` по умолчанию и JSONL backend при включении.
- Фильтр чувствительных данных перед сохранением памяти.
- Tool registry и безопасные tools для файлов, поиска по проекту, read-only git и ограниченного запуска команд.
- Code verifier для coding-route при `metadata.verify_code=true`.
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
    context/builder.py            # история сессии + recalled memory + system prompt
    execution/tool_dispatcher.py  # выполнение tool steps через registry
    planning/planner.py           # активный planner, пока один LLM-шаг
    planning/simple.py            # экспериментальный scaffold, не активный runtime
    routing/router.py             # simple route: general/architecture/coding/research
    session/manager.py            # in-memory session state
    synthesis/result_synthesizer.py
    verification/
      verifier.py                 # проверка ответа модели на пустой результат
      code_verifier.py            # compileall, pytest, ruff при coding verification
  providers/
    llm/                          # ILLMProvider + LMStudioProvider
    memory/                       # IMemoryService, noop, json_file, policy, factory
  schemas/
    chat.py                       # ChatRequest, ChatResponse, ExecutionStep
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
tests/                            # pytest-набор по текущим модулям
```

`data/`, `logs/`, `.pytest_cache/`, `.ruff_cache/`, `.venv/`, `venv/` и `ai_agentv1.egg-info/` не являются основной архитектурой приложения. Это runtime/build/test артефакты или локальная среда.

## Базовый поток

```text
POST /api/v1/chat
  -> require_api_key()
  -> request middleware: request_id, rate limit, logging
  -> Orchestrator.handle()
  -> SessionManager.get_or_create()
  -> TaskRouter.route()
  -> ContextBuilder.build()
       - system prompt
       - последние сообщения сессии
       - recalled memory, если backend включен
  -> Planner.make_plan()
  -> LLMProvider.chat()
  -> Verifier.verify()
  -> optional CodeVerifier.verify(), если route=coding и metadata.verify_code=true
  -> memory save, если включена память и данные не чувствительные
  -> ResultSynthesizer.synthesize()
```

Важно: `ToolDispatcher` и tools уже подключены, но активный `Planner` пока не выбирает инструменты автоматически. Это следующий крупный шаг.

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
- фактически разрешенные command patterns сейчас ограничены `git status/diff/log`, `uv run pytest -q`, `uv run python -m compileall app tests` и `ruff check .`.

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
| `TOOL_WORKSPACE_ROOT` | `.` | Корень, внутри которого работают tools. |
| `TOOL_MAX_FILE_BYTES` | `200000` | Лимит чтения/записи файлов и поиска. |
| `TOOL_COMMAND_TIMEOUT_SECONDS` | `30` | Таймаут команд tools и code verifier. |
| `TOOL_MAX_OUTPUT_CHARS` | `20000` | Лимит stdout/stderr в tool result. |

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
ruff check .
```

`ruff check .` пропускается, если executable `ruff` не найден.

## Тесты

```powershell
uv run pytest -q
```

Дополнительно:

```powershell
uv run python -m compileall app tests
ruff check .
```

## Что планируется дальше

Ближайший порядок работ:

1. Расширить `Planner` до tool-calling loop: выбор tools, выполнение, передача результатов обратно в модель, stop conditions и защита от бесконечных циклов.
2. Описать tool schemas для модели и сделать единый формат tool step, чтобы LLM могла запрашивать `read_file`, `search_project`, `git_diff` и другие инструменты предсказуемо.
3. Собрать coding workflow: анализ запроса, чтение файлов, подготовка patch, запуск verifier, итоговый diff summary.
4. Сделать persistent session store вместо только in-memory history.
5. Улучшить memory backend: нормальный project/user scope, более точный retrieval, подготовка к embeddings/vector store или mem0 за тем же `IMemoryService`.
6. Добавить дополнительные LLM providers: Ollama, vLLM или общий OpenAI-compatible provider вместо жесткой привязки к LM Studio.
7. Добавить CLI-клиент рядом с API для локального использования без ручных HTTP-запросов.
8. Вынести GUI/desktop worker в отдельный adapter, не смешивая его с ядром orchestrator.
9. Усилить observability: метрики, structured tracing по шагам planner/tools/LLM и e2e-тесты на полный сценарий.

Текущий главный фокус - не добавлять новые возможности прямо в core, а нарастить их через интерфейсы: `ILLMProvider`, `IMemoryService`, `ITool` и orchestration pipeline.
