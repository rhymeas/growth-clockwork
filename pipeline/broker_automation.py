"""Single-lane local broker worker for internal work and explicit publisher tasks."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading

from pipeline.marketing_worker import run_marketing_task
from pipeline.mavery_qa_worker import run_mavery_qa_task
from pipeline.publisher_worker import run_publisher_task
from pipeline.research_worker import run_research_task
from pipeline.task_broker import BrokerError, TaskBroker


class BrokerAutomation:
    def __init__(self, *, workspace: Path, database: Path, permissions: Path,
                 executable: Path | None, runner=None, runners=None,
                 enabled_agents=None) -> None:
        self.workspace = Path(workspace)
        self.database = Path(database)
        self.permissions = Path(permissions)
        self.executable = Path(executable) if executable is not None else None
        if runners is not None and runner is not None:
            raise ValueError('Select runner or runners, not both')
        self.runners = runners or ({
            'research': runner, 'marketing': runner,
            'mavery-qa': runner,
        } if runner is not None else {
            'research': run_research_task,
            'marketing': run_marketing_task,
            'mavery-qa': run_mavery_qa_task,
            'publisher': run_publisher_task,
        })
        self.enabled_agents = set(self.runners) if enabled_agents is None else set(enabled_agents)
        if not self.enabled_agents.issubset(self.runners):
            raise ValueError('Enabled agent lacks a runner')
        self._executor = ThreadPoolExecutor(max_workers=1,
                                            thread_name_prefix='growth-pipeline')
        self._futures: dict[tuple[str, str], Future] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def start(self, *, project: str, task_id: str, profile_path: Path) -> bool:
        key = (project, task_id)
        with self._lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return False
            broker = TaskBroker(self.database, self.permissions)
            try:
                task = broker.get(project, task_id)
                agent = task['agent_id']
                if (agent not in self.enabled_agents or task['status'] != 'queued'
                        or (agent != 'publisher' and self.executable is None)):
                    return False
                broker.permissions(agent)
            finally:
                broker.close()
            self._futures[key] = self._executor.submit(
                self._execute, project, task_id, Path(profile_path))
            return True

    def _execute(self, project: str, task_id: str, profile_path: Path) -> None:
        broker = TaskBroker(self.database, self.permissions)
        result = None
        agent = None
        try:
            task = broker.get(project, task_id)
            agent = task['agent_id']
            runner = self.runners[agent]
            result = runner(
                broker, project=project, task_id=task_id,
                workspace=self.workspace, profile_path=profile_path,
                executable=self.executable)
        except Exception:
            # Preflight failures happen before run_task claims. Make failure visible.
            try:
                task = broker.get(project, task_id)
                if agent is not None and task['status'] == 'queued':
                    claim = broker.claim(project, task_id, agent=agent,
                                         lease_seconds=30)
                    broker.fail(project, task_id, agent=agent,
                                token=claim['lease_token'], version=claim['version'])
            except BrokerError:
                pass
        finally:
            broker.close()
        if (isinstance(result, dict) and result.get('next_status') == 'queued'
                and result.get('next_agent') in self.runners
                and isinstance(result.get('next_task_id'), str)):
            self.start(project=project, task_id=result['next_task_id'],
                       profile_path=profile_path)
