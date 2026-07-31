# Project State

Обновлено: 2026-07-31

## Текущий milestone

`M2 — persistent worker и Run API` реализован; следующий milestone — `M3`.
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

## Незакрытый release gate M0

- выполнить smoke-test обычного ответа, `read_file`, `search_project` и malformed
  tool call с реальным LM Studio;
- после успешного smoke создать release tag `v0.2.0`.

На 2026-07-31 smoke заблокирован внешним состоянием: LM Studio process отсутствует,
а на `127.0.0.1:1234` нет listener. Это не блокирует M1, но блокирует release tag.

## Следующий milestone

`M3 — безопасный coding workflow`:

1. task branch/worktree от выбранного committed SHA;
2. deterministic `MutationPreview` с old/new hashes и unified diff;
3. approval по `preview_hash` и stale-state check перед atomic write;
4. verification report, final diff stat и optional local commit;
5. безопасные git tools без push/reset/clean/delete.

## Известные gaps

- approval подтверждает сохранённый tool call и interim approval hash, но ещё не использует
  deterministic mutation preview, `preview_hash` и stale-state check;
- coding выполняется без обязательного task worktree;
- policy engine, desktop, hub, outbox и handoff ещё не реализованы;
- SQLite operations пока синхронные и рассчитаны на local single-worker workload;
- fine-grained LLM/tool events записываются после завершения orchestrator response,
  поэтому token streaming появится вместе с desktop integration;
- реальный LM Studio smoke зависит от локально запущенного сервера и модели.

## Правило продолжения

Новый исполнитель сначала читает этот файл, затем текущий milestone в
`PROJECT_PLAN.md`. После создания `ai-agent-contracts` локальная копия становится
указателем на закреплённую contracts version.
