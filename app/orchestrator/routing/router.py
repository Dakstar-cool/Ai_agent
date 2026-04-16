class TaskRouter:
    def route(self, message: str) -> str:
        lowered = message.lower()
        if any(word in lowered for word in ["архитект", "design", "blueprint"]):
            return "architecture"
        if any(word in lowered for word in ["bug", "fix", "refactor", "code", "код"]):
            return "coding"
        if any(word in lowered for word in ["research", "исслед", "сравни", "найди"]):
            return "research"
        return "general"
