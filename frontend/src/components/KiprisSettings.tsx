import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { AppSettings, CredentialCheck } from '../lib/types';

export default function KiprisSettings({ settings, onChange }: {
  settings: AppSettings; onChange: (settings: AppSettings) => void;
}) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [check, setCheck] = useState<CredentialCheck | null>(null);
  const [total, setTotal] = useState('');
  const quota = settings.kipris_quota;
  const enabled = settings.values.kipris_integration_enabled === true;
  const saved = settings.secrets_set?.kipris_api_key === true;
  useEffect(() => { setTotal(''); }, [quota?.month]);

  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await action(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function saveKey(value: string) {
    onChange(await api.updateSettings({ kipris_api_key: value }));
    setKey(''); setCheck(null); setMessage(value ? 'API 키를 저장했습니다.' : 'API 키를 삭제했습니다.');
  }
  return <section className="card settings-kipris" aria-label="키프리스 설정 및 사용량">
    <h2>키프리스 국내 특허 검색</h2>
    <p className="hint">KIPRIS Plus의 국내 특허·실용신안을 기존 검색과 함께 검색합니다. 검색 결과의 서지·초록과 청구항·전문 검증은 구분됩니다.</p>
    <label className="checkbox-row"><input type="checkbox" checked={enabled} disabled={busy}
      onChange={e => { const value = e.target.checked; void act(async () => {
        onChange(await api.updateSettings({ kipris_integration_enabled: value })); setCheck(null);
      }); }} /> 키프리스 연동 사용</label>
    <div className="field" style={{ marginTop: 14 }}>
      <label htmlFor="kipris-api-key">KIPRIS Plus API 키 (accessKey) <span className={`pill ${saved ? 'ok' : 'neutral'}`}>{saved ? '저장됨' : '미설정'}</span></label>
      <input id="kipris-api-key" type="password" autoComplete="new-password" value={key} disabled={busy}
        placeholder={saved ? '새 키로 변경할 때만 입력하세요' : 'KIPRIS Plus에서 발급한 API 키'}
        onChange={e => setKey(e.target.value)} />
      <div className="btn-row" style={{ marginTop: 8 }}>
        <button className="btn small" disabled={busy || !key.trim()} onClick={() => void act(() => saveKey(key.trim()))}>키 저장</button>
        <button className="btn small" disabled={busy || !saved} onClick={() => void act(() => saveKey(''))}>키 삭제</button>
        <button className="btn small" disabled={busy || !enabled || !saved || !!quota?.blocked || !!key.trim()}
          onClick={() => void act(async () => {
            setCheck(null);
            try { setCheck(await api.checkKipris()); }
            finally { onChange(await api.settings()); }
          })}>연결 테스트 (1회 사용)</button>
      </div>
      <p className="hint">「특허·실용 공개·등록공보」 서비스 신청 및 승인이 필요합니다. 키는 이 PC의 설정 DB에 저장되며 화면 응답에는 포함되지 않습니다.</p>
    </div>
    {check && <p role="status" className={`notice ${check.ok ? 'ok' : 'danger'}`}>{check.detail}</p>}
    <h3>키프리스 월별 사용량</h3>
    <p className="hint">월 1,000회 · 매월 1일 00:00 한국 시간에 초기화됩니다. 데이터 용량이나 결과 건수가 아닌 API 요청 횟수를 셉니다.</p>
    {quota ? <>
      <progress aria-label="키프리스 월 사용량" value={Math.min(quota.used, quota.limit)} max={quota.limit} style={{ width: '100%' }} />
      <div className="table-scroll"><table><tbody>
        <tr><th>집계 월 (한국 시간)</th><td>{quota.month}</td></tr>
        <tr><th>이번 달 사용 / 한도</th><td>{quota.used.toLocaleString()} / {quota.limit.toLocaleString()}회</td></tr>
        <tr><th>남은 횟수</th><td>{quota.remaining.toLocaleString()}회</td></tr>
        <tr><th>PRISM 호출 / 외부 사용 반영</th><td>{quota.requests.toLocaleString()} / {quota.external_requests.toLocaleString()}회</td></tr>
        <tr><th>다음 초기화</th><td>{new Date(quota.reset_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} (한국 시간)</td></tr>
      </tbody></table></div>
      {quota.warning && <p role="status" className={`notice ${quota.blocked ? 'danger' : 'warn'}`}>
        {quota.blocked ? '월간 한도에 도달하여 키프리스 호출이 중단되었습니다. 다른 검색원은 계속 사용할 수 있습니다.' : '월간 한도의 80% 이상 사용했습니다.'}
      </p>}
      <p className="hint">PRISM의 로컬 집계입니다. 실패·연결 테스트도 보수적으로 1회 집계하며, 캐시 재사용은 차감하지 않습니다. 다른 앱의 사용량은 자동으로 알 수 없으므로 포털에서 확인한 이번 달 전체 사용 횟수를 아래에 반영하세요.</p>
      <label htmlFor="kipris-total-used">포털에서 확인한 이번 달 전체 사용 횟수</label>
      <div className="btn-row">
        <input id="kipris-total-used" type="number" min={quota.requests} max={1000000} step={1} value={total}
          placeholder={String(quota.used)} disabled={busy} onChange={e => setTotal(e.target.value)} />
        <button className="btn small" disabled={busy || total === '' || !Number.isInteger(Number(total)) || Number(total) < quota.requests || Number(total) > 1000000}
          onClick={() => void act(async () => {
            onChange(await api.updateKiprisUsage(quota.month, Number(total))); setTotal(''); setMessage('외부 사용 횟수를 반영했습니다.');
          })}>사용량 반영</button>
      </div>
      <details style={{ marginTop: 12 }}><summary>최근 월별 사용 기록</summary>
        {quota.history.length ? <table><thead><tr><th>월</th><th>PRISM 호출</th><th>외부 사용</th></tr></thead>
          <tbody>{quota.history.map(row => <tr key={row.month}><td>{row.month}</td><td>{row.requests}회</td><td>{row.external_requests}회</td></tr>)}</tbody>
        </table> : <p className="hint">아직 사용 기록이 없습니다.</p>}
      </details>
    </> : <p className="hint">사용량을 불러오지 못했습니다. 새로고침해 주세요.</p>}
    <button className="btn small" style={{ marginTop: 12 }} disabled={busy}
      onClick={() => void act(async () => { onChange(await api.settings()); })}>키프리스 사용량 새로고침</button>
    {message && <p role="status" className="notice ok">{message}</p>}
    {error && <p role="alert" className="notice danger">{error}</p>}
  </section>;
}
