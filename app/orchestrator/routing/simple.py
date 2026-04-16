from app.orchestrator.models import TaskType

class SimpleTaskRouter:
    def route(self, user_message: str) -> TaskType:
        text = user_message.lower()

        if any(word in text for word in ["bug", "fix", "refactor", "код", "файл"]):
            return TaskType.CODING
        if any(word in text for word in ["архитектура", "roadmap", "design"]):
            return TaskType.ARCHITECTURE
        if any(word in text for word in ["research", "исслед", "сравни"]):
            return TaskType.RESEARCH
        return TaskType.CHAT