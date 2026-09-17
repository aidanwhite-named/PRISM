import asyncio

import pytest


@pytest.mark.asyncio
async def test_shutdown_cancels_queued_tasks(monkeypatch):
    from app.execution.runner import JobRunner
    runner = JobRunner()
    task = asyncio.create_task(asyncio.sleep(60))
    runner._tasks["queued"] = task
    calls = []

    async def cancel(job_id):
        calls.append(job_id)
        task.cancel()

    monkeypatch.setattr(runner, "cancel", cancel)
    await runner.shutdown()
    assert calls == ["queued"]
    assert task.cancelled()
