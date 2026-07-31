# Project State

Обновлено: 2026-07-31

## Текущий milestone

`M3 — безопасный coding workflow` реализован; следующий milestone — `M4`.
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

## Незакрытый release gate M0

- выполнить smoke-test обычного ответа, `read_file`, `search_project` и malformed
  tool call с реальным LM Studio;
- после успешного smoke создать release tag `v0.2.0`.

На 2026-07-31 smoke заблокирован внешним состоянием: LM Studio process отсутствует,
а на `127.0.0.1:1234` нет listener. Это не блокирует M1, но блокирует release tag.

## Следующий milestone

`M4 — policy engine и autonomy modes`:

1. типизированный `RunPolicy` для safe/supervised/autonomous;
2. hard deny перед любыми task grants и requested mode;
3. TTL, path globs, allowed tools, write/command limits и network flag;
4. audit events и post-action hashes для выполненных мутаций;
5. policy-тесты, доказывающие, что autonomous не обходит hard deny.

## Известные gaps

- policy engine, desktop, hub, outbox и handoff ещё не реализованы;
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
