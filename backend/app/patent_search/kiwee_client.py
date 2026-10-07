"""Read-only Kiwee Solr transport; never reuse/export a program's credentials."""
from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .base import PatentSearchError

ENDPOINT = 'https://gateway.kiwee.or.kr/solr/select'
MAX_BYTES = 8 * 1024 * 1024


class KiweeError(PatentSearchError):
    def __init__(self, code, detail, http_status=None):
        super().__init__(detail)
        self.fault_code = code
        self.http_status = http_status


def validate_endpoint(value):
    value = str(value or '').strip()
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError('Kiwee 검색 서버 주소를 확인하세요.') from None
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or not url.path.endswith('/select')
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError('Kiwee 주소는 인증 정보·쿼리가 없는 HTTPS /select 주소여야 합니다.')
    return value


def validate_shards(value):
    value = str(value or '').strip()
    if value and not re.fullmatch(r'shd_[a-z0-9_]+(?:,shd_[a-z0-9_]+)*', value):
        raise ValueError('검색 범위는 shd_kr 또는 shd_kr,shd_us 형식으로 입력하세요.')
    return value


def validate_thumbprint(value):
    value = re.sub(r'\s+', '', str(value or '')).upper()
    if value and not re.fullmatch(r'[0-9A-F]{40}', value):
        raise ValueError('클라이언트 인증서 지문은 40자리 SHA-1 값이어야 합니다.')
    return value


def build_query(text, mode='keywords'):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('Kiwee 검색어는 1~2,000자로 입력하세요.')
    if mode == 'solr':
        if '{!' in text or any(ord(c) < 32 for c in text):
            raise ValueError('Solr 검색식에 지원하지 않는 구문이 있습니다.')
        return text.strip()
    if mode != 'keywords':
        raise ValueError('검색 방식은 keywords 또는 solr여야 합니다.')
    # Each word must occur in title, abstract or claims. No LLM or silent truncation.
    def escape(word):
        return word.replace('\\', '\\\\').replace('"', '\\"')
    return ' AND '.join('(' + ' OR '.join(f'{field}:"{escape(word)}"'
        for field in ('tl', 'ab', 'cl')) + ')' for word in text.split())


def _http_error(status):
    if status in (401, 403):
        return KiweeError('KIWEE.AUTH', 'Kiwee 서버가 인증 또는 검색 권한을 요구합니다. 클라이언트 인증서와 계정 권한을 확인하세요.', status)
    if 300 <= status < 400:
        return KiweeError('KIWEE.REDIRECT', '검색 서버가 다른 주소나 로그인 화면으로 이동을 요구했습니다. 검색 주소와 인증을 확인하세요.', status)
    if status == 404:
        return KiweeError('KIWEE.ENDPOINT', '검색 API 주소를 찾지 못했습니다. Kiwee 담당자에게 서버 주소를 확인하세요.', status)
    if status == 400:
        return KiweeError('KIWEE.QUERY', '서버가 검색식을 거절했습니다. 전송 검색식과 검색 필드·범위를 확인하세요.', status)
    return KiweeError('KIWEE.HTTP', f'Kiwee 검색 서버가 HTTP {status} 오류를 반환했습니다.', status)


def _tls_error():
    return KiweeError('KIWEE.TLS', 'Kiwee 서버 인증서를 신뢰할 수 없습니다. 기관의 루트 인증서를 Windows에 설치하거나 고급 설정에서 CA 인증서 파일을 지정하세요.')


def _curl_transport(endpoint, body, values):
    """Schannel uses Windows trust and a selected non-exported private key."""
    curl = shutil.which('curl.exe')
    if not curl:
        raise KiweeError('KIWEE.CLIENT', 'Windows curl을 찾지 못했습니다.')
    with tempfile.TemporaryDirectory(prefix='prism-kiwee-') as directory:
        output = Path(directory) / 'response.json'
        args = [curl, '--disable', '--silent', '--show-error', '--proto', '=https',
                '--connect-timeout', '8', '--max-time', '25', '--max-filesize', str(MAX_BYTES),
                '--request', 'POST', '--header', 'Content-Type: application/x-www-form-urlencoded; charset=utf-8',
                '--header', 'Accept: application/json', '--data-binary', '@-',
                '--output', str(output), '--write-out', '%{http_code}']
        thumbprint = validate_thumbprint(values.get('kiwee_certificate_thumbprint'))
        if thumbprint:
            args += ['--cert', 'CurrentUser\\MY\\' + thumbprint]
        ca_file = str(values.get('kiwee_ca_file') or '').strip()
        if ca_file:
            args += ['--cacert', ca_file]
        args.append(endpoint)
        try:
            result = subprocess.run(args, input=body, capture_output=True, timeout=30,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise KiweeError('KIWEE.TIMEOUT', 'Kiwee 검색 서버의 응답 시간이 초과되었습니다.') from None
        except OSError:
            raise KiweeError('KIWEE.CLIENT', 'Kiwee 연결 도구를 실행하지 못했습니다.') from None
        if result.returncode:
            if result.returncode in (60, 77):
                raise _tls_error()
            if result.returncode in (35, 58):
                raise KiweeError('KIWEE.CERTIFICATE', 'TLS 연결 또는 클라이언트 인증서 사용에 실패했습니다. 인증서 지문·유효기간·개인 키 권한을 확인하세요.')
            if result.returncode == 28:
                raise KiweeError('KIWEE.TIMEOUT', 'Kiwee 검색 서버의 응답 시간이 초과되었습니다.')
            if result.returncode == 63:
                raise KiweeError('KIWEE.SIZE', '검색 응답이 8MB를 초과했습니다. 결과 수를 줄이세요.')
            raise KiweeError('KIWEE.NETWORK', 'Kiwee 서버에 연결하지 못했습니다. 기관망/VPN과 서버 주소를 확인하세요.')
        try:
            status = int(result.stdout.decode('ascii').strip())
        except ValueError:
            raise KiweeError('KIWEE.RESPONSE', '연결 도구의 HTTP 상태를 읽지 못했습니다.') from None
        if status != 200:
            raise _http_error(status)
        with output.open('rb') as handle:
            return status, handle.read(MAX_BYTES + 1)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def live_transport(endpoint, params, values):
    body = urlencode(params).encode('utf-8')
    ca_file = str(values.get('kiwee_ca_file') or '').strip()
    if ca_file and not Path(ca_file).is_file():
        raise KiweeError('KIWEE.CA_FILE', 'CA 인증서 파일을 찾지 못했습니다. 이 PC의 파일 경로를 확인하세요.')
    if os.name == 'nt':
        return _curl_transport(endpoint, body, values)
    if values.get('kiwee_certificate_thumbprint'):
        raise KiweeError('KIWEE.CERTIFICATE', 'Windows 인증서 지문 연결은 Windows에서 사용할 수 있습니다.')
    try:
        context = ssl.create_default_context(cafile=ca_file or None)
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
        request = urllib.request.Request(endpoint, data=body, headers={'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'})
        with opener.open(request, timeout=25) as response:
            return response.status, response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc.code) from None
    except (ssl.SSLError, urllib.error.URLError) as exc:
        if isinstance(exc, ssl.SSLError) or isinstance(getattr(exc, 'reason', None), ssl.SSLError):
            raise _tls_error() from None
        raise KiweeError('KIWEE.NETWORK', 'Kiwee 서버에 연결하지 못했습니다. 기관망/VPN과 서버 주소를 확인하세요.') from None
    except (OSError, ValueError):
        raise KiweeError('KIWEE.NETWORK', 'Kiwee 연결 또는 CA 인증서 설정을 확인하세요.') from None


def parse_response(data):
    if len(data) > MAX_BYTES:
        raise KiweeError('KIWEE.SIZE', '검색 응답이 8MB를 초과했습니다. 결과 수를 줄이세요.')
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise KiweeError('KIWEE.RESPONSE', '검색 결과 JSON을 받지 못했습니다. 로그인 화면이나 별도 API 형식일 수 있습니다.', 200) from None
    if not isinstance(payload, dict) or 'error' in payload:
        raise KiweeError('KIWEE.RESPONSE', 'Kiwee 서버가 검색 오류 또는 지원하지 않는 응답을 반환했습니다.', 200)
    header = payload.get('responseHeader', {})
    response = payload.get('response')
    if (not isinstance(header, dict) or header.get('status', 0) != 0
            or header.get('partialResults') not in (None, False)
            or not isinstance(response, dict) or not isinstance(response.get('docs'), list)
            or type(response.get('numFound')) is not int or response['numFound'] < 0
            or any(not isinstance(row, dict) for row in response['docs'])
            or response['numFound'] < len(response['docs'])):
        raise KiweeError('KIWEE.RESPONSE', '완전한 Solr 검색 응답을 확인하지 못했습니다. 결과 없음으로 처리하지 않았습니다.', 200)
    return response['docs'], response['numFound']
