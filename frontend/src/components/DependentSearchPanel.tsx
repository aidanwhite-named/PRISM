import type { DependentSearchInput } from "../lib/types";

interface Props {
  value: DependentSearchInput;
  onChange: (value: DependentSearchInput) => void;
  onPrepare: () => void;
  disabled: boolean;
  preparing: boolean;
  canPrepare: boolean;
  error?: string;
}

export default function DependentSearchPanel({ value, onChange, onPrepare, disabled, preparing, canPrepare, error }: Props) {
  return <section className="input-panel dependent-search-panel" aria-label="종속항 검색 대상 확인">
    <label className="field">
      <strong>따로 검색할 종속항</strong>
      <span className="hint">청구항 번호와 전문을 입력하세요. 여러 종속항도 함께 넣을 수 있습니다.</span>
      <textarea aria-label="따로 검색할 종속항" value={value.dependentText} disabled={disabled}
        placeholder="예: 청구항 2. 제1항에 있어서, 상기 객체의 이동속도에 따라 경고 임계값을 변경하는 장치."
        onChange={e => onChange({ ...value, dependentText: e.target.value, featureText: "", explanation: "", warnings: [] })} />
    </label>
    <div className="btn-row">
      <button type="button" className="btn" disabled={disabled || !canPrepare || !value.dependentText.trim()} onClick={onPrepare}>
        {preparing ? "추가 특징 정리 중…" : "AI로 검색 대상 정리"}
      </button>
      <span className="hint">검색 전에 확인하고 싶을 때 사용하세요. 이 단계를 건너뛰어도 검색할 수 있습니다.</span>
    </div>
    {preparing && <p role="status">설정된 검색 모델이 추가 특징을 정리하고 있습니다. 완료되면 아래 문장 칸에 표시됩니다. 문헌 검색은 실행하지 않습니다.</p>}
    {error && <div className="notice danger" role="alert">{error}</div>}
    <label className="field">
      <strong>실제 검색 대상 문장 (선택)</strong>
      <span className="hint">비워 두면 AI가 종속항 원문에서 추가 특징을 파악해 검색합니다. 작성하면 이 문장을 검색 기준으로 사용합니다.</span>
      <textarea aria-label="실제 검색 대상 문장" value={value.featureText} disabled={disabled}
        placeholder="예: 객체의 이동속도에 따라 위험 경고의 임계값을 변경하는 기술."
        onChange={e => onChange({ ...value, featureText: e.target.value })} />
    </label>
    {value.explanation && <p className="faint">AI 정리 설명: {value.explanation}</p>}
    {!!value.warnings.length && <div className="notice warn"><strong>검색 전 확인할 사항</strong><ul>{value.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>}
    <p className="hint">선행항은 의미와 관련성을 이해하는 참고자료입니다. 검색어·분야 확장·재검색은 AI가 판단하며,
      결과에는 추가 특징의 대응 근거와 적용 맥락의 차이를 설명합니다.</p>
  </section>;
}
