from pathlib import Path

import pytest

from jig.models import AgentInstance, AgentStatus, AgentTypeConfig
from jig.persistence import save_agent_type, load_agent_instance, list_agent_instances
from jig.pool import AgentPool


class TestAgentPool:
    @pytest.fixture
    def pool(self, tmp_jig_project: Path) -> AgentPool:
        save_agent_type(tmp_jig_project, AgentTypeConfig(
            role="dev", phase_prompt="Dev agent.", allowed_tools=["Read", "Edit"],
        ))
        save_agent_type(tmp_jig_project, AgentTypeConfig(
            role="test", phase_prompt="Test agent.", allowed_tools=["Read", "Bash"],
        ))
        return AgentPool(tmp_jig_project)

    def test_spawn_creates_instance(self, pool: AgentPool, tmp_jig_project: Path):
        instance = pool.spawn("dev")
        assert instance.id.startswith("dev-")
        assert instance.agent_type == "dev"
        assert instance.status == AgentStatus.IDLE
        loaded = load_agent_instance(tmp_jig_project, instance.id)
        assert loaded.id == instance.id

    def test_spawn_increments_ids(self, pool: AgentPool):
        i1 = pool.spawn("dev")
        i2 = pool.spawn("dev")
        assert i1.id == "dev-1"
        assert i2.id == "dev-2"

    def test_spawn_unknown_type_raises(self, pool: AgentPool):
        with pytest.raises(FileNotFoundError):
            pool.spawn("unknown")

    def test_find_idle(self, pool: AgentPool):
        pool.spawn("dev")
        instance = pool.find_idle("dev")
        assert instance is not None
        assert instance.agent_type == "dev"
        assert instance.status == AgentStatus.IDLE

    def test_find_idle_returns_none_when_all_busy(self, pool: AgentPool, tmp_jig_project: Path):
        instance = pool.spawn("dev")
        instance.status = AgentStatus.ACTIVE
        pool.update(instance)
        result = pool.find_idle("dev")
        assert result is None

    def test_find_idle_returns_none_when_no_instances(self, pool: AgentPool):
        result = pool.find_idle("dev")
        assert result is None

    def test_acquire_returns_idle_instance(self, pool: AgentPool):
        pool.spawn("dev")
        instance = pool.acquire("dev")
        assert instance.status == AgentStatus.ACTIVE

    def test_acquire_spawns_when_none_idle(self, pool: AgentPool):
        i1 = pool.spawn("dev")
        i1.status = AgentStatus.ACTIVE
        pool.update(i1)
        i2 = pool.acquire("dev")
        assert i2.id != i1.id
        assert i2.status == AgentStatus.ACTIVE

    def test_release(self, pool: AgentPool, tmp_jig_project: Path):
        instance = pool.acquire("dev")
        assert instance.status == AgentStatus.ACTIVE
        pool.release(instance)
        loaded = load_agent_instance(tmp_jig_project, instance.id)
        assert loaded.status == AgentStatus.DORMANT
        assert loaded.current_task_id is None

    def test_update_persists(self, pool: AgentPool, tmp_jig_project: Path):
        instance = pool.spawn("dev")
        instance.status = AgentStatus.ACTIVE
        instance.session_id = "sess-123"
        pool.update(instance)
        loaded = load_agent_instance(tmp_jig_project, instance.id)
        assert loaded.status == AgentStatus.ACTIVE
        assert loaded.session_id == "sess-123"
