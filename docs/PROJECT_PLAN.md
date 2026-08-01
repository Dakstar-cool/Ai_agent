# Генеральный план AI Agent

Статус: временная каноническая копия до создания `ai-agent-contracts`.

## Цель и границы

AI Agent — local-first платформа совместной работы людей и AI-агентов над
локальными репозиториями. Python worker выполняет LLM-запросы и инструменты на
компьютере пользователя. Удалённый self-hosted hub синхронизирует только
командные сущности и явно опубликованные артефакты. Недоступность hub не должна
останавливать локальные runs, approvals или coding workflow.

Зафиксированные решения:

- основной клиент: Tauri 2, React и TypeScript;
- worker: Python 3.12+, FastAPI, SQLite/WAL, `uv`, PyInstaller sidecar;
- локальные модели: LM Studio и Ollama по умолчанию;
- remote OpenAI-compatible providers и публикация кода — только explicit opt-in;
- coding выполняется в отдельной task branch и task worktree;
- default autonomy mode — `safe`;
- автоматические push, удаление, destructive git и доступ к секретам запрещены;
- hidden chain-of-thought не сохраняется и не передаётся;
- Windows, Linux и macOS входят в первый desktop release.

Репозитории:

1. `ai-agent-worker` — локальный runtime и tools (этот git history).
2. `ai-agent-desktop` — Tauri/React клиент и lifecycle sidecar.
3. `ai-agent-hub` — FastAPI/Postgres/OIDC collaboration service.
4. `ai-agent-contracts` — schemas, OpenAPI, ADR, roadmap и compatibility matrix.

## Архитектурные контракты

### Worker API

`POST /api/v1/chat` остаётся compatibility endpoint. Основной асинхронный API:

- `POST /api/v1/runs`;
- `GET /api/v1/runs/{run_id}`;
- `GET /api/v1/runs/{run_id}/events` (SSE);
- `POST /api/v1/runs/{run_id}/cancel`;
- `POST /api/v1/approvals/{approval_id}/decision`.

Tauri запускает worker на случайном loopback-порту и передаёт одноразовый
256-битный bootstrap token через stdin. Все локальные запросы требуют bearer
token. Provider credentials хранятся desktop-приложением в OS keychain или
Stronghold и передаются worker только в память.

Workspace задаётся внутренним `workspace_id`, а не произвольным путём клиента.
Worker хранит workspaces, sessions, runs, events, approvals и policy grants в
SQLite/WAL. На workspace/worktree действует один execution lock.

### Contracts

Канонические JSON Schema/OpenAPI описывают `Run`, `RunEvent`, `ToolCall`,
`ToolResult`, `MutationPreview`, `ApprovalDecision`, `VerificationReport`,
`Project`, `Task`, `Message`, `Artifact`, `HandoffPackage`, sync events и errors.
Каждый контракт содержит `schema_version`. Несовместимый major блокирует
подключение, minor вызывает предупреждение.

### Hub и offline sync

Hub использует FastAPI, async SQLAlchemy, Alembic, PostgreSQL, OIDC Authorization
Code + PKCE и отдельные отзываемые device credentials. REST обслуживает команды
и initial sync, WebSocket — realtime события. Артефакты лежат в S3-compatible
storage/MinIO, metadata и checksums — в Postgres. Hub никогда не запускает tools
над пользовательским репозиторием.

Desktop хранит cache, hub cursor и encrypted outbox. Исходящие события имеют UUID
idempotency key. Сообщения и артефакты append-only. `Task` использует optimistic
`version`; конфликт показывается пользователю без автоматической перезаписи.
Outbox отправляется по порядку с exponential backoff.

## Milestones

### M0 — worker foundation 0.2.0

- сохранить текущую пользовательскую работу в отдельной ветке;
- закрепить typed tool calling, read-only auto-execution, limits, approvals и
  безопасные errors;
- выполнить `uv sync --frozen`, pytest, compileall и ruff;
- smoke-test реального LM Studio: обычный ответ, `read_file`, `search_project`,
  malformed tool call;
- удалить старый параллельный scaffold моделей/contracts/simple;
- сохранить этот план и текущее состояние в `docs/`.

Готовность: worker воспроизводимо запускается, все автоматические проверки
зелёные, milestone закоммичен и описан.

### M1 — repositories и protocol foundation

- сохранить текущую историю как `ai-agent-worker`;
- создать `ai-agent-contracts`, `ai-agent-desktop`, `ai-agent-hub`;
- перенести roadmap, состояние и первые ADR/schemas в contracts;
- генерировать Python/TypeScript types и проверять protocol compatibility в CI;
- ввести независимый SemVer и `compatibility.yaml`;
- добавить в каждый repo `AGENTS.md` со ссылкой на версию общего плана.

Готовность: worker и минимальные desktop/hub собираются против одной версии
contracts.

### M2 — persistent worker и Run API

- перенести sessions и pending approvals в SQLite/WAL;
- реализовать состояния `queued`, `running`, `waiting_approval`, `verifying`,
  `completed`, `failed`, `cancelled`;
- сохранять LLM/tool/approval события в append-only `RunEvent`;
- добавить SSE, cancellation и восстановление после restart;
- сделать `/chat` синхронным адаптером над Run API;
- добавить workspace registry и execution lock;
- failed CodeVerifier должен менять итоговый status, reply и memory policy.

Готовность: restart не теряет runs, approvals и sessions; timeline полностью
восстанавливается по `run_id`.

### M3 — безопасный coding workflow

Поток: task → task worktree → inspect → mutation proposal → deterministic
preview → approval/policy → stale-state check → atomic write → verify → diff/report.

- branch `agent/<task-id>` и worktree создаются от выбранного committed SHA;
- stash/reset пользовательского workspace запрещены;
- `MutationPreview` содержит operation, relative path, unified diff, old/new
  SHA-256, size, `preview_hash` и expiry;
- approval содержит `approval_id`, decision и `preview_hash`;
- перед записью повторно проверяется old hash, иначе `stale_preview` без записи;
- safe mode требует approval на каждую мутацию;
- первая версия не делает grouped multi-file transaction;
- verification profile: compileall, pytest, ruff через `uv`;
- итог содержит diff stat, paths, verification report и local commit SHA;
- разрешены только безопасные git tools для branch/worktree/local commit.

Готовность: E2E coding prompt создаёт проверенный diff только в task worktree.

### M4 — policy engine и autonomy

`RunPolicy`: mode, TTL, path globs, allowed tools, write/command limits и network
flag. Порядок: hard deny → organization/project policy → task grant → requested
mode.

- `safe`: auto только read-only;
- `supervised`: scoped grant на task, paths и tools;
- `autonomous`: scoped writes, allow-listed tests и local commits в worktree;
- push/delete требуют отдельного approval, destructive git отсутствует;
- protected paths, secrets, workspace escape и неразрешённая сеть запрещены;
- каждая мутация пишет audit event и post-action hash.

Готовность: policy tests доказывают, что режимы не обходят hard deny.

### M5 — cross-platform desktop MVP

Tauri 2 + React + TypeScript + Vite: onboarding, workspace selector, streaming run
timeline, tool results, diff approval, autonomy settings, providers, history и
diagnostics. Нужны sidecar crash recovery, secure credentials, отсутствие
скрытого терминала, CI и подписанные packages для Windows/Linux/macOS.

Готовность: один local coding workflow работает во всех трёх сборках.

### M6 — collaboration hub MVP

Docker Compose: hub, Postgres и MinIO; внешний OIDC и optional Keycloak profile.
Реализовать projects, memberships, tasks, discussions, assignment человеку или
registered agent, явную публикацию summary/diff/report/handoff и immutable audit.
Репозиторий, memory и скрытый execution context автоматически не загружаются.

Готовность: два пользователя проходят offline/reconnect сценарий без дублей.

### M7 — handoff людей и агентов

`HandoffPackage` включает task state/messages, decision summaries, безопасно
обрезанный tool trace, base SHA/branch/manifest, diff, verification, artifacts,
выбранные snapshots, provider/model/template versions и approval audit без
capability IDs. Исключаются CoT, secrets, protected/unselected files и полная
local memory. Перед export показываются package composition и redaction report.
При import заново проверяются repo/base SHA; trace считается недоверенным.

Готовность: handoff между двумя устройствами обнаруживает base SHA mismatch до
любых изменений.

### M8 — providers, memory и hardening

- общий OpenAI-compatible provider, LM Studio preset, Ollama adapter и capability
  discovery; fallback только по явной policy;
- разделить session history, run trace и persistent knowledge;
- локальная SQLite FTS5 memory с scope, provenance, TTL, export/delete;
- embeddings остаются за `IMemoryService` и добавляются только по необходимости;
- structured JSON logs, IDs, hub OTel/metrics, opt-in local telemetry;
- dependency scanning, SBOM, signing, backup/restore, rate limits и quotas;
- security suites: injection, symlink/TOCTOU, redaction, stale approvals,
  OIDC/RBAC, malicious handoff и replayed sync events.

## Release gates

Каждый репозиторий проходит format, lint, typecheck, tests, security scan и build.
Breaking schema change требует нового contracts major и обновления compatibility
matrix. Worker и desktop не публикуются при несовместимом protocol major.

Тестовые слои:

- worker: contracts, policies, stores, fake-provider E2E, approval replay/expiry,
  worktree isolation, cancellation/deadline/restart и safe error redaction;
- desktop: Vitest, Playwright, Tauri integration, packaging и offline/outbox;
- hub: PostgreSQL/MinIO integration, OIDC/RBAC/idempotency/conflicts, WebSocket
  recovery, checksums/limits/redaction и chaos tests.

## Правила сохранения контекста

После M1 канонические файлы находятся в `ai-agent-contracts`:

- `docs/PROJECT_PLAN.md` — этот roadmap;
- `docs/PROJECT_STATE.md` — завершённое, следующее, blockers и gaps;
- `docs/adr/` — неизменяемые архитектурные решения;
- `roadmap.yaml` — machine-readable milestones;
- `compatibility.yaml` — совместимые версии компонентов;
- `schemas/` и OpenAPI documents.

Каждый feature PR указывает milestone и acceptance criterion. Архитектурное
изменение требует ADR. Завершение milestone обновляет state, roadmap и matrix.
Исторические планы помечаются superseded, но не переписываются. В проектную
документацию не попадают secrets, capability IDs, пользовательские локальные
пути или hidden reasoning.
