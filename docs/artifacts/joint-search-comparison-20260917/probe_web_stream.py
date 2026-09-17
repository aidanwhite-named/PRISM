"""Inspect public web-search stream envelopes; no private files or settings in output."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend'))
from app.providers.agy_cli import AgyCliProvider
from app.providers.agy_stream import AgyStreamParser
from app.providers.base import ExecutionRequest, AGY_WEB_SEARCH
from app.config import PATHS

original = AgyStreamParser.feed
envelopes = []
def feed(self, line):
    try:
        value = json.loads(line)
        step = value.get('step_update', {})
        if step.get('step_type') in ('tool', 'tool_call'):
            envelopes.append(step)
    except ValueError:
        pass
    return original(self, line)
AgyStreamParser.feed = feed

async def main():
    directory = PATHS.run_dir('public-web-stream-probe')
    directory.mkdir(parents=True, exist_ok=True)
    request = ExecutionRequest('public-web-stream-probe', directory,
        'Use web search. Return the first relevant patent title and URL immediately as JSON. No further research is needed in this diagnostic.',
        'Search: patent "child bone" "first limit"', model='gemini-3.8-flash-medium',
        timeout_seconds=40, tool_policy=AGY_WEB_SEARCH)
    async def emit(kind, data): pass
    outcome = await AgyCliProvider().execute(request, emit)
    (directory / 'envelopes.json').write_text(json.dumps(envelopes, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'result': outcome.result_text, 'timeout': outcome.timed_out,
                     'envelopes': [{'keys': list(e), 'tool_info': e.get('tool_info')} for e in envelopes]}, ensure_ascii=False))

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
