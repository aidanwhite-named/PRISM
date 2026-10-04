"""Offline before/after persistence measurement; no provider calls or user jobs."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.search_engine.autonomous import AutonomousSearch
from app.search_engine.autonomous_store import merge
from app.search_engine.report import render, manifest


async def measure(engine_type, directory, polls, candidates):
    directory.mkdir()
    merge(directory, [{'title': f'Source {i}', 'url': f'https://example.org/{i}',
                       'reason': 'A controls B based on C. ' * 30} for i in range(candidates)])
    # Simulate the real report construction on every emitted snapshot. DB and UI
    # latency are intentionally excluded from this local persistence measurement.
    emitted = []
    async def emit(snapshot):
        manifest(snapshot, claim='A controls B', provider='offline')
        render(snapshot)
        emitted.append(len(snapshot['candidates']))
    e = engine_type(claim='A controls B', directory=directory,
        inference=SimpleNamespace(usage=lambda: {}), values={}, emit=emit)
    started = time.perf_counter()
    for _ in range(polls):
        await e.publish()
    e.phase, e.stop_reason = 'complete', 'deadline'
    # Both engines must always persist the final state.
    await e.publish()
    seconds = time.perf_counter() - started
    saved = json.loads((directory / 'checkpoint.json').read_text(encoding='utf-8'))['snapshot']
    assert len(saved['findings']) == candidates and saved['stop_reason'] == 'deadline'
    return {'polls': polls, 'candidates': candidates, 'full_snapshot_updates': len(emitted),
            'local_seconds': round(seconds, 6),
            'final_candidates': len(saved['findings']), 'final_stop_reason': saved['stop_reason']}


async def main(args):
    spec = importlib.util.spec_from_file_location('app.search_engine._before', args.before)
    before = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = before
    spec.loader.exec_module(before)
    with tempfile.TemporaryDirectory(prefix='prism-persistence-benchmark-') as temporary:
        root = Path(temporary)
        previous = await measure(before.AutonomousSearch, root / 'before', args.polls, args.candidates)
        current = await measure(AutonomousSearch, root / 'after', args.polls, args.candidates)
    result = {'kind': 'offline_local_persistence', 'before': previous, 'after': current,
              'limitations': 'No LLM, network, DB commits or retrieval quality measured. '
                             'This is not an end-to-end search speedup.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', type=Path, required=True, help='Saved previous autonomous.py')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--polls', type=int, default=40)
    parser.add_argument('--candidates', type=int, default=130)
    asyncio.run(main(parser.parse_args()))
