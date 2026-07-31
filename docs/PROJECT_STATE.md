# Project State

Обновлено: 2026-07-31

## Текущий milestone

`M5 — cross-platform desktop MVP` в работе; первый sidecar/UI slice реализован.
Release tag worker `v0.2.0` всё ещё ожидает ручной LM Studio smoke.

## Завершено

- типизированы `LLMResponse`, `ToolCall` и `ToolResult`;
- `ITool` публикует JSON Schema и безопасный `read_only` признак;
- `ToolRegistry` генерирует OpenAI-compatible tool definitions;
- LM Studio provider поддерживает `tools`, `tool_choice`, `message.tool_calls` и
  локальный HTTP-клиент с `trust_env=False`;
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
- полный M5 worker gate: 126 tests, compileall и ruff — зелёные.

## Незакрытый release gate M0

- выполнить smoke-test обычного ответа, `read_file`, `search_project` и malformed
  tool call с реальным LM Studio;
- после успешного smoke создать release tag `v0.2.0`.

На 2026-07-31 smoke заблокирован внешним состоянием: LM Studio process отсутствует,
а на `127.0.0.1:1234` нет listener. Это не блокирует M1, но блокирует release tag.

## Следующий milestone

Продолжение `M5 — cross-platform desktop MVP`:

1. проверить Rust/Tauri crate нативно на Windows/Linux/macOS CI;
2. подключить настоящие sidecar artifacts для трёх target triples;
3. добавить Tauri integration tests crash/restart и bootstrap auth;
4. подготовить unsigned packaging smoke, затем signing/notarization secrets;
5. закрыть protocol minor-version negotiation и high-level task/worktree UX.

## Известные gaps

- desktop M5 реализован как функциональный slice, но native packaging/signing ещё не
  подтверждены на всех трёх ОС; hub, outbox и handoff ещё не реализованы;
- organization/project policy boundaries поддержаны evaluator-ом, но их удалённое
  администрирование появится вместе с hub и OIDC/RBAC;
- Run API пока ожидает, что desktop сначала явно создаст task worktree, а затем запустит
  coding Run по его `worktree_workspace_id`; единый high-level create-task endpoint ещё не добавлен;
- SQLite operations пока синхронные и рассчитаны на local single-worker workload;
- fine-grained LLM/tool events записываются после завершения orchestrator response,
  поэтому token streaming появится вместе с desktop integration;
- реальный LM Studio smoke зависит от локально запущенного сервера и модели.

## Правило продолжения

Новый исполнитель сначала читает этот файл, затем текущий milestone в
`PROJECT_PLAN.md`. После создания `ai-agent-contracts` локальная копия становится
указателем на закреплённую contracts version.
