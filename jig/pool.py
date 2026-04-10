"""Agent pool — manages agent instance lifecycle."""

from pathlib import Path

from jig.models import AgentInstance, AgentStatus
from jig.persistence import (
    list_agent_instances,
    load_agent_type,
    save_agent_instance,
)


class AgentPool:
    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path

    def spawn(self, role: str) -> AgentInstance:
        """Create a new agent instance from a type. Returns the new instance."""
        load_agent_type(self._project_path, role)

        existing = list_agent_instances(self._project_path, agent_type=role)
        next_num = len(existing) + 1
        instance_id = f"{role}-{next_num}"

        instance = AgentInstance(id=instance_id, agent_type=role)
        save_agent_instance(self._project_path, instance)
        return instance

    def find_idle(self, role: str) -> AgentInstance | None:
        """Find an idle agent instance with the given role. Returns None if none available."""
        instances = list_agent_instances(self._project_path, agent_type=role)
        for instance in instances:
            if instance.status == AgentStatus.IDLE:
                return instance
        return None

    def acquire(self, role: str) -> AgentInstance:
        """Get an idle agent for a role, or spawn a new one. Sets status to ACTIVE."""
        instance = self.find_idle(role)
        if instance is None:
            instance = self.spawn(role)
        instance.status = AgentStatus.ACTIVE
        save_agent_instance(self._project_path, instance)
        return instance

    def release(self, instance: AgentInstance) -> None:
        """Release an agent back to the pool as dormant."""
        instance.status = AgentStatus.DORMANT
        instance.current_task_id = None
        save_agent_instance(self._project_path, instance)

    def update(self, instance: AgentInstance) -> None:
        """Persist the current state of an agent instance."""
        save_agent_instance(self._project_path, instance)
