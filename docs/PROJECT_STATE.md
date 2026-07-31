# Project State

Обновлено: 2026-07-31

## Текущий milestone

`M0 — worker foundation 0.2.0` реализован в ветке
`codex/worker-foundation-m0`; release tag ожидает ручной LM Studio smoke.

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

## Незакрытый release gate M0

- выполнить smoke-test обычного ответа, `read_file`, `search_project` и malformed
  tool call с реальным LM Studio;
- после успешного smoke создать release tag `v0.2.0`.

На 2026-07-31 smoke заблокирован внешним состоянием: LM Studio process отсутствует,
а на `127.0.0.1:1234` нет listener. Это не блокирует M1, но блокирует release tag.

## Следующий milestone

`M1 — repositories и protocol foundation`:

1. создать contracts repo и закрепить первую protocol version;
2. добавить JSON schemas, OpenAPI, ADR, roadmap и compatibility matrix;
3. создать минимальные worker/desktop/hub consumers одной contracts version;
4. добавить generated Python/TypeScript types и compatibility CI.

## Известные gaps

- sessions и approvals пока process-local;
- отсутствуют Run API, persistent event log, SSE и cancellation;
- клиент ещё задаёт `project_path`; workspace registry отсутствует;
- approval подтверждает сохранённый tool call, но ещё не использует
  deterministic mutation preview, `preview_hash` и stale-state check;
- failed CodeVerifier фиксируется в steps, но пока не меняет финальный run status;
- coding выполняется без обязательного task worktree;
- policy engine, desktop, hub, outbox и handoff ещё не реализованы;
- реальный LM Studio smoke зависит от локально запущенного сервера и модели.

## Правило продолжения

Новый исполнитель сначала читает этот файл, затем текущий milestone в
`PROJECT_PLAN.md`. После создания `ai-agent-contracts` локальная копия становится
указателем на закреплённую contracts version.
