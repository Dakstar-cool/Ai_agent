from app.orchestrator.routing.router import TaskRouter


def test_router_architecture() -> None:
    router = TaskRouter()
    assert router.route("Нужен blueprint архитектуры") == "architecture"


def test_router_coding() -> None:
    router = TaskRouter()
    assert router.route("Исправь bug в коде") == "coding"
