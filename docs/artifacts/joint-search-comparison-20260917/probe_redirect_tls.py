"""One-off, user-authorized TLS comparison for the recorded public redirect URL only."""
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import PATHS
from app.search_engine.models import write_json

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    events = json.loads((PATHS.run_dir('public-web-stream-probe') / 'envelopes.json').read_text(encoding='utf-8'))
    url = next(e['tool_info']['parameters']['Url'] for e in events if e.get('tool_name') == 'read_url_content')
    assert url.startswith('https://vertexaisearch.cloud.google.com/grounding-api-redirect/')
    results = []
    for verify in (True, False):
        context = ssl.create_default_context() if verify else ssl._create_unverified_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
        row = {'certificate_verification': verify, 'automatic_redirects': False}
        try:
            response = opener.open(urllib.request.Request(url, headers={'User-Agent': 'PRISM-Diagnostic/1.0'}), timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        except Exception as exc:
            row['error'] = type(exc).__name__ + ': ' + str(exc)[:800]
            try:
                row['decoded_error'] = row['error'].encode('latin1').decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            results.append(row)
            continue
        with response:
            row.update(status=response.status, content_type=response.headers.get('Content-Type'),
                       location=response.headers.get('Location'), body_prefix=response.read(2048).decode('utf-8', errors='replace'))
        results.append(row)
    output = {'target_host': 'vertexaisearch.cloud.google.com', 'scope': 'recorded URL only; no persistent TLS settings changed', 'results': results}
    write_json(Path(__file__).with_name('redirect-tls-probe.json'), output)
    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
