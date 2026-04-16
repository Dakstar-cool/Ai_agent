# Local AI Agent Foundation v0

Это стартовый каркас проекта под архитектуру оркестратора.

Что уже заложено:
- FastAPI gateway
- Orchestrator Core
- Session Manager
- Task Router
- Context Builder
- Planner
- Tool Dispatcher
- Verifier
- Result Synthesizer
- LLM Provider abstraction
- LM Studio provider
- Memory abstraction + NoOp implementation
- Optional local JSON memory backend
- Tools registry + 3 базовых tools

## Базовый поток

```text
POST /api/v1/chat
  -> Orchestrator.handle()
  -> SessionManager.get_or_create()
  -> TaskRouter.route()
  -> ContextBuilder.build()
  -> Planner.make_plan()
  -> ToolDispatcher.execute()
  -> LLMProvider.chat()
  -> Verifier.verify()
  -> ResultSynthesizer.synthesize()
```

## Запуск

```bash
uv run python -m uvicorn app.main:app --reload
uv run python -c "import sys; print(sys.executable)"
uv sync
```


## Почему так

Главная идея - зависеть от интерфейсов, а не от конкретных реализаций.

Это значит:
- LM Studio потом можно заменить на Ollama или vLLM
- память можно расширить до mem0 без переписывания ядра
- GUI worker можно подключить как отдельный модуль
- тестировать оркестратор можно на заглушках
