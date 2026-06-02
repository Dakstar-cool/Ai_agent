from app.orchestrator.models import ExecutionPlan, PlanStep, PlanStepType


class SimplePlanner:
    """Experimental scaffold planner; active runtime uses Planner."""

    def build_plan(self, user_message: str, context) -> ExecutionPlan:
        return ExecutionPlan(
            steps=[
                PlanStep(
                    step_type=PlanStepType.LLM_CHAT,
                    name="primary_llm_response",
                    payload={},
                )
            ]
        )
