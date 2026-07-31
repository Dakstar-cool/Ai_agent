# Project State

Обновлено: 2026-07-31

## Текущий milestone

`M8 — providers, memory и platform hardening` реализован локально. Канонический
статус всего проекта хранится в `ai-agent-contracts/docs/PROJECT_STATE.md`.
Real LM Studio gate пройден 2026-08-01; release tag ожидает опубликованный CI.

## Завершено

- типизированы `LLMResponse`, `ToolCall` и `ToolResult`;
- `ITool` публикует JSON Schema и безопасный `read_only` признак;
- `ToolRegistry` генерирует OpenAI-compatible tool definitions;
- LM Studio provider поддерживает `tools`, `tool_choice`, `message.tool_calls` и
  локальный HTTP-клиент с `trust_env=False`;
- каждый provider request ограничен `LLM_MAX_OUTPUT_TOKENS`; реальный LM Studio smoke
  проверяет plain response, `read_file`, `search_project`, malformed call и `tool_call_id`;
- agent loop ограничен steps, tool calls, deadline и duplicate protection;
- автоматически выполняются только read-only tools;
- mutating tools создают одноразовый approval, привязанный к session и TTL;
- tool result возвращается модели с исходным `tool_call_id`;
- fake-provider E2E покрывает read tools, unknown tool, limits, malformed calls и
  approval flow;
- старый параллельный orchestrator scaffold удалён.
- `uv sync --frozen`, 65 pytest tests, compileall и ruff прошли 2026-07-31.
- M1: contracts/desktop/hub repositories созданы и закреплены на protocol `0.1.0`;
- M2: SQLite/WAL хранит workspaces, sessions, runs, events и approvals;
- добавлены Run API, SSE, cancel, approval decision и startup recovery;
- `/chat` переведён на синхронный adapter поверх persistent RunService;
- failed CodeVerifier теперь меняет reply, Run state и memory policy;
- полный M2 gate: 87 tests, compileall и ruff прошли 2026-07-31.
- M3: coding-задачи могут выполняться в отдельной ветке `agent/<task-id>` и worktree
  от выбранного committed SHA, не изменяя dirty исходный workspace;
- `write_file` формирует deterministic `MutationPreview` с unified diff и SHA-256,
  а approval связан с `preview_hash` и повторной проверкой исходного файла;
- добавлены stale-preview защита, атомарная запись, worktree report, verification и
  optional local commit выбранных путей;
- безопасные git tools поддерживают только создание task worktree и локальный commit;
  push/reset/clean/delete отсутствуют;
- fake-provider E2E покрывает путь coding prompt → preview → approval → write → verify
  и доказывает изоляцию исходного workspace.
- M4: `RunPolicy` поддерживает safe/supervised/autonomous, server-bound TTL,
  allow-list tools, path globs, write/command limits и network permission;
- policy precedence реализован как hard deny → organization/project → task grant →
  requested mode;
- protected/secrets paths, workspace escape и destructive git блокируются даже в
  autonomous mode и после обычного approval;
- mutation audit сохраняется в append-only RunEvent с policy decision и
  post-action SHA-256;
- policy unit/E2E доказывают scoped autonomous write без approval и невозможность
  обхода hard deny; полный M4 gate: 109 tests, compileall и ruff — зелёные.
- worker `0.6.0` закреплён на contracts protocol `0.3.0`;
- добавлен PyInstaller sidecar с 256-битным bootstrap token через stdin, случайным
  loopback-портом и обязательной bearer-аутентификацией всех local API requests;
- provider credentials передаются только в память, remote provider требует HTTPS,
  explicit opt-in и `RunPolicy.network_allowed=true`;
- добавлен безопасный pending-approval endpoint для desktop diff preview;
- persistent run history доступна через `GET /api/v1/runs` с workspace filter;
- Windows sidecar собран и smoke-проверен: protected health отвечает только с bearer;
- добавлен CI matrix для сборки и smoke-проверки sidecar на Windows x64, Linux x64 и
  macOS arm64; каждый artifact сопровождается SHA-256 checksum;
- полный M5 worker gate: 126 tests, compileall и ruff — зелёные.
- worker `0.7.0`: безопасный Handoff trace, bounded diff и проверка base SHA;
- worker `0.8.0`: общий OpenAI-compatible provider, LM Studio/Ollama presets,
  capability discovery и отсутствие неявного provider fallback;
- persistent knowledge вынесена в scoped SQLite/WAL/FTS5 с TTL, provenance,
  export/delete; сохраняются summaries/decisions, а не raw tool outputs;
- JSON logs содержат request/run/task IDs; release CI выполняет dependency scan,
  формирует CycloneDX SBOM и аттестует только tagged artifacts;
- полный M8 worker gate: 146 tests + 2 platform skips, compileall, ruff, build,
  SBOM/audit, Windows sidecar и real LM Studio smoke — зелёные.

## Release evidence M0

- smoke-test обычного ответа, `read_file`, `search_project` и malformed tool call
  с реальным LM Studio успешно выполнен на `liquid/lfm2.5-1.2b`;
- исторический tag `v0.2.0` не создаётся на коде `0.8.x`; следующий release tag
  создаётся только после опубликованного CI и актуальной compatibility matrix.

Воспроизводимый harness: `uv run python scripts/smoke_lmstudio.py --model <loaded-model-id>`.

## Следующий milestone

Release evidence и эксплуатационная проверка:

1. выполнить Ollama smoke после установки runtime и загрузки tool-capable модели;
2. запустить опубликованные Windows/Linux/macOS CI и signing/notarization;
3. выполнить PostgreSQL/MinIO reconnect и backup/restore gate в CI;
4. провести двухустройственный offline/handoff E2E.

## Известные gaps

- desktop M5–M7 реализован как функциональный slice, но native packaging/signing ещё
  не подтверждены на всех трёх ОС;
- organization/project policy boundaries поддержаны evaluator-ом, но их удалённое
  администрирование появится вместе с hub и OIDC/RBAC;
- Run API пока ожидает, что desktop сначала явно создаст task worktree, а затем запустит
  coding Run по его `worktree_workspace_id`; единый high-level create-task endpoint ещё не добавлен;
- SQLite operations пока синхронные и рассчитаны на local single-worker workload;
- fine-grained LLM/tool events записываются после завершения orchestrator response,
  поэтому token streaming появится вместе с desktop integration;
- Ollama runtime и модель локально пока не установлены; LM Studio smoke закрыт.

## Правило продолжения

Новый исполнитель сначала читает этот файл, затем текущий milestone в
`PROJECT_PLAN.md`. После создания `ai-agent-contracts` локальная копия становится
указателем на закреплённую contracts version.
