import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { AppSettings, KiweeTrialResult } from '../lib/types';

function config(settings: AppSettings) {
  const v = settings.values;
  return {
    kiwee_integration_enabled: v.kiwee_integration_enabled === true,
    kiwee_endpoint: v.kiwee_endpoint ?? 'https://gateway.kiwee.or.kr/solr/select',
    kiwee_shards: v.kiwee_shards ?? 'shd_kr',
    kiwee_ca_file: v.kiwee_ca_file ?? '',
    kiwee_certificate_thumbprint: v.kiwee_certificate_thumbprint ?? '',
  };
}

export default function KiweeSettings({ settings, onChange }: {
  settings: AppSettings; onChange: (settings: AppSettings) => void;
}) {
  const [draft, setDraft] = useState(() => config(settings));
  const [query, setQuery] = useState('반도체');
  const [mode, setMode] = useState<'keywords' | 'solr'>('keywords');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [result, setResult] = useState<KiweeTrialResult | null>(null);
  const saved = JSON.stringify(config(settings));
  useEffect(() => { setDraft(JSON.parse(saved)); setResult(null); }, [saved]);
  const dirty = JSON.stringify(draft) !== saved;
  const ready = draft.kiwee_integration_enabled && !dirty && !!query.trim() && !busy;
  function edit(key: keyof typeof draft, value: string | boolean) {
    setDraft(old => ({ ...old, [key]: value })); setResult(null); setMessage('');
  }
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await action(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function search(page = 1) {
    setResult(null);
    await act(async () => { setResult(await api.searchKiwee(query.trim(), mode, page)); });
  }
  return <section className="card settings-kiwee" aria-label="Kiwee 검색 시험 연동">
    <h2>Kiwee 검색 <span className="pill warn">시험 연동</span></h2>
    <p className="hint">Kiwee를 실행하지 않고 검색 서버에 직접 요청합니다. 먼저 짧은 검색어로 연결과 반환 문헌을 확인하세요. 연동을 켜면 유사문헌 검색의 AI도 이 검색원을 사용할 수 있습니다.</p>
    <label className="checkbox-row"><input type="checkbox" checked={draft.kiwee_integration_enabled} disabled={busy}
      onChange={e => edit('kiwee_integration_enabled', e.target.checked)} /> Kiwee 연동 사용</label>
    <div className="field" style={{ marginTop: 14 }}>
      <label htmlFor="kiwee-endpoint">검색 서버 주소</label>
      <input id="kiwee-endpoint" type="url" value={draft.kiwee_endpoint} disabled={busy}
        onChange={e => edit('kiwee_endpoint', e.target.value)} />
    </div>
    <div className="field">
      <label htmlFor="kiwee-shards">검색 범위</label>
      <select id="kiwee-shards" value={draft.kiwee_shards} disabled={busy} onChange={e => edit('kiwee_shards', e.target.value)}>
        <option value="shd_kr">한국</option>
        <option value="shd_us">미국</option>
        <option value="shd_ep">유럽</option>
        <option value="shd_wo">국제출원 (WO)</option>
        <option value="shd_kr,shd_us,shd_ep,shd_wo">한국·미국·유럽·WO</option>
        <option value="">서버 기본 범위</option>
        {!['shd_kr', 'shd_us', 'shd_ep', 'shd_wo', 'shd_kr,shd_us,shd_ep,shd_wo', ''].includes(draft.kiwee_shards)
          && <option value={draft.kiwee_shards}>{draft.kiwee_shards}</option>}
      </select>
    </div>
    <details style={{ marginBottom: 12 }}>
      <summary>인증서 고급 설정</summary>
      <p className="hint">기관망/VPN과 서버 이용 권한이 필요할 수 있습니다. Kiwee 로그인 정보는 자동으로 가져오지 않습니다.</p>
      <div className="field">
        <label htmlFor="kiwee-ca">기관 CA 인증서 파일 (선택)</label>
        <input id="kiwee-ca" value={draft.kiwee_ca_file} placeholder="C:\\인증서\\기관-루트.pem" disabled={busy}
          onChange={e => edit('kiwee_ca_file', e.target.value)} />
        <p className="hint">서버 인증서 신뢰 오류가 발생하면 기관에서 제공한 PEM 형식의 CA 파일을 지정하세요.</p>
      </div>
      <div className="field">
        <label htmlFor="kiwee-cert">클라이언트 인증서 지문 (선택 · Windows)</label>
        <input id="kiwee-cert" value={draft.kiwee_certificate_thumbprint} disabled={busy} placeholder="40자리 SHA-1 지문"
          onChange={e => edit('kiwee_certificate_thumbprint', e.target.value)} />
        <p className="hint">Windows의 현재 사용자 → 개인 인증서에 설치된 인증서의 지문을 입력하세요. 유효한 인증서와 개인 키가 필요합니다.</p>
      </div>
    </details>
    <button className="btn small" disabled={busy || !dirty} onClick={() => void act(async () => {
      onChange(await api.updateSettings(draft)); setMessage('Kiwee 설정을 저장했습니다.');
    })}>Kiwee 설정 저장</button>
    {dirty && <p className="hint">변경한 설정을 저장한 뒤 시험 검색을 실행하세요.</p>}
    <h3>시험 검색</h3>
    <div className="field">
      <label htmlFor="kiwee-query">검색어 또는 검색식</label>
      <textarea id="kiwee-query" rows={2} maxLength={2000} value={query} disabled={busy}
        onChange={e => { setQuery(e.target.value); setResult(null); }} />
    </div>
    <div className="field">
      <label htmlFor="kiwee-mode">검색 방식</label>
      <select id="kiwee-mode" value={mode} disabled={busy} onChange={e => {
        setMode(e.target.value as 'keywords' | 'solr'); setResult(null);
      }}>
        <option value="keywords">키워드 — 제목·초록·청구항에서 검색</option>
        <option value="solr">서버 검색식 직접 입력 (Solr)</option>
      </select>
      <p className="hint">키워드는 공백으로 나누며 모든 단어를 포함하도록 검색합니다. 시험 검색은 LLM을 호출하지 않습니다. 서버 검색식은 Kiwee 화면의 검색식과 문법이 다를 수 있습니다.</p>
    </div>
    <div className="btn-row">
      <button className="btn small" disabled={!ready} onClick={() => void search()}>{busy ? '처리 중…' : 'Kiwee 시험 검색'}</button>
      {result?.ok && result.next_page && <button className="btn small" disabled={!ready}
        onClick={() => void search(result.next_page!)}>다음 5건</button>}
    </div>
    {result && <div aria-live="polite" style={{ marginTop: 12 }}>
      <p role="status" className={`notice ${result.ok ? 'ok' : 'danger'}`}>{result.detail}
        {result.http_status != null && ` (HTTP ${result.http_status})`}</p>
      {result.records.map((record, i) => <article key={i} className="card" style={{ marginTop: 8 }}>
        <b>{record.fields.title || record.document_number || record.fields.kiwee_id || `결과 ${i + 1}`}</b>
        {record.document_number && <p>{record.document_number}</p>}
        {record.fields.publication_date && <p>공개일: {record.fields.publication_date}</p>}
        {record.fields.abstract && <p style={{ whiteSpace: 'pre-wrap' }}>{record.fields.abstract}</p>}
        <details><summary>문헌 정보</summary><pre className="result-raw">{JSON.stringify(record.fields, null, 2)}</pre></details>
      </article>)}
      {result.notes.map(note => <p className="hint" key={note}>{note}</p>)}
      <details><summary>연결 진단 정보</summary>
        <p>오류 코드: {result.error_code || '없음'}</p>
        <p>전송 검색식</p><pre className="result-raw">{result.solr_query}</pre>
        {result.returned_fields.length > 0 && <p className="break">반환 필드: {result.returned_fields.join(', ')}</p>}
      </details>
    </div>}
    {message && <p role="status" className="notice ok">{message}</p>}
    {error && <p role="alert" className="notice danger">{error}</p>}
  </section>;
}
