"""One analysis payload, application-owned citation numbering and report layout.

Preserve valid content and expose incomplete fields. Exact
source excerpts are resolved once, then reused by every referencing component.
"""
from __future__ import annotations

import html
import json
import re

from . import analysis_evidence, analysis_manifest, citation_mapping, report_sources, report_references
from .enums import AttachmentRole, DeliveryMode
from .reference_selection import INSTRUCTIONS as REFERENCE_SELECTION

MARKER = '# PRISM 구조화 보고서 V5'
INSTRUCTIONS = MARKER + '''
최종 출력은 아래 형태의 JSON 객체 하나다. Markdown 보고서, 표, 기존 기계 판독 블록을 중복 작성하지 않는다.
version은 5이며 모든 evidence에 sentence_ids가 필수다. 원문을 다시 적는 quote 필드는 사용하지 않는다.
분석 기준은 앞의 지침을 따르되 서식·번호·발췌 배치는 프로그램이 처리한다.
이 출력 양식은 판단 결과를 담는 틀이다. 예시의 점수·대응 여부·결론을 따라 쓰거나 양식을 맞추기 위해 분석 결과를 바꾸지 않는다.
documents는 주 인용문헌부터 나열하고 attachment는 실제 ATT-번호만 쓴다. 고정 매핑이 있으면 그 주 문헌을 유지한다.
문헌별 선행 검토와 주 문헌 선정이 제공되면 그 검토 이후 보고서를 작성한다. 구성 문언·기호·순서를 유지하고 선정된 primary_attachment를 첫 문헌으로 사용한다. 원문이 검토 표보다 우선한다.
evidence는 선택한 근거만 E1 등의 ID로 한 번씩 기록하고 components에서 재사용한다.
본문의 [원문 문장 ATT-01-P3-T2] 표식 뒤 원문을 읽고 sentence_ids에 그 번호를 그대로 선택한다. 문헌 번호나 페이지로 문장 번호를 새로 조합하지 않는다.
문장 번호는 탐색용 구간 U번호와 다르다. T번호만 사용하며 quote, source_id, page, start/end는 쓰지 않는다.
프로그램이 선택한 문장의 실제 원문과 위치를 그대로 가져온다. 대소문자·관사·문장부호·OCR 오류를 고쳐 다시 적지 않는다.
sentence_ids에는 같은 원문·페이지에서 연속된 번호만 순서대로 넣는다. 약어·OCR 때문에 문장이 잘렸으면 다음 번호도 함께 선택하여 필요한 문맥을 보존한다.
떨어진 문장이나 다른 페이지의 문장은 반드시 별도 evidence와 번역으로 등록한다. 중간 문장·코드·표·문단 번호를 생략하여 하나의 연속 발췌로 묶지 않는다.
translation은 선택한 번호의 원문 전체만 충실하게 번역한다. 선택하지 않은 문장의 내용을 보태거나 선택한 문장 일부를 생략하지 않는다.
페이지 끝에서 문장이나 열거 항목이 끊기면 다음 페이지의 근거를 별도로 선택하고 그 범위만 따로 번역한다. 다음 페이지의 내용을 앞 페이지 번역에 보충하지 않는다.
language는 ko 또는 foreign이다. foreign이면 선택한 전체 범위의 한국어 translation을 반드시 쓴다.
원문·위치를 확인하지 못하면 근거를 만들지 말고 evidence를 비우고 reasoning에 한계를 적는다.
각 구성은 claim, symbol, feature, similarity(0~100 정수 또는 판단 불가 시 null), basis(direct/inferred),
evidence(실제로 설명에 사용하는 근거 ID 목록), evidence_uses(근거 ID별 support/contrast 용도), reference_roles(미대응·확인 불가 문헌의 검토 결과), reasoning(한정별 대응 이유와 범위), difference(복수 문헌 대비 후 잔존 차이)를 한 번씩 기록한다.
feature는 사용자의 구성 문언을 한정 생략 없이 그대로 보존한다.
symbol은 비울 수 없다. 기호 없는 전제부는 (전제부), 다른 미표기 구성은 (분석 1) 등으로 표시하되 사용자의 (A), (B) 기호를 변경하지 않는다.
구성대비 evidence와 reasoning에는 주 문헌과 남은 한정을 실제로 보완하는 데 필요한 최소 인용발명만 사용한다. 문헌별로 실제 대응하는 한정과 근거 ID를 구분한다.
유사도만 인용발명 1 자체를 기준으로 평가한다. 다른 문헌의 대응 근거를 점수에 합산하지 않는다. 주 문헌의 부족한 한정은 reasoning의 대응 범위 설명에 포함한다.
difference는 복수 문헌으로 대비해도 대응되지 않는 부분만 한 문장으로 쓰며, 없으면 빈 문자열이다. 이 필드는 생략하지 않는다.
낮은 주 문헌 유사도만으로 difference를 강제로 채우지 않는다. 여러 문헌의 개별 기재를 합쳐 단일 문헌의 전체 개시 또는 문헌 간 연결 관계의 입증으로 취급하지 않는다.
resolution, supplements, derivation은 작성하지 않는다. 대응 근거와 설명은 모두 구성대비에 기록하고 차이점에는 발췌·번역·보완 설명을 반복하지 않는다.
모든 evidence 참조는 근거가 하나여도 ["E8"]처럼 배열로 쓴다. 같은 원문·위치의 근거는 하나의 ID로 재사용한다.
근거 재사용 시 각 구성의 evidence 배열에는 해당 ID를 다시 연결한다. 앞 구성에 출력됐다는 이유로 연결을 생략하지 않는다. 중복 출력만 프로그램이 제거한다. reasoning에서 사용한 모든 E번호는 현재 구성의 evidence에도 있어야 한다.
''' + REFERENCE_SELECTION + '''
각 구성은 필수 한정의 대응 여부를 판단하고 필요한 원문을 선택한 뒤 설명과 근거를 연결한다. evidence의 모든 ID에 evidence_uses를 {"E1":"support","E2":"contrast"}처럼 기록한다.
support는 해당 구성의 한정에 실제 대응하는 근거다. contrast는 문헌의 실제 내용을 보여 주어 처리 대상·동작·관계가 왜 다른지 설명하는 비교 근거이며 대응의 입증이나 양수 점수의 근거로 세지 않는다. 일반 배경은 두 용도 모두에서 제외한다.
support 문헌은 연결된 support 근거에서 프로그램이 산출하므로 reference_roles에 중복 선언하지 않는다. reference_roles에는 긍정 대응 근거가 없는 검토 문헌의 not_found 또는 unavailable만 기록한다.
contrast만 사용하는 문헌은 프로그램이 comparison 역할로 산출한다. 차이 설명용 인용 자체는 문헌 전체 미발견 선언이 아니므로 reference_roles를 억지로 작성하지 않는다. 원문 전체의 미발견 판단이나 확인 불가를 명시하는 경우에만 해당 검토 결과를 별도로 기록한다.
not_found는 제공된 검토 범위에서 해당 한정의 대응 근거를 찾지 못한 경우다. 문헌의 다른 처리 방식을 contrast 발췌로 설명할 수 있으며 발췌가 존재하는 사실과 무대응 판단은 모순이 아니다. unavailable은 자료를 읽거나 판단할 수 없는 경우이며 그 자료의 발췌를 연결하지 않는다.
주 문헌이 unavailable이면 similarity는 null이다. 다른 문헌의 미발견·확인 불가는 주 문헌 점수를 바꾸지 않는다. 부분 대응 문헌에서 일부 한정만 미발견인 경우 역할은 support이며 대응 범위를 reasoning에 쓴다.
주 문헌이 not_found이면 similarity는 0이다. 양수 점수는 현재 구성에 연결한 주 문헌 근거로만 부여한다. 다른 문헌의 대응을 주 문헌 점수에 섞지 않는다.
미발견은 제공·검토된 범위에 한정한다. 일부 발췌만 읽고 문헌 전체에 없다고 단정하지 않는다.
근거는 해당 한정을 가장 직접적으로 입증하는 최소 범위를 우선 선택한다. 추가 문장·단락·문헌은 별개의 필수 한정 또는 관계를 입증하거나 지시어·조건·부정을 해석하는 데 필요한 경우에만 포함한다.
각 추가 근거가 어떤 한정을 새로 입증하는지 reasoning에서 짧게 연결한다. 이미 선택한 발췌로 입증되는 한정에 개요·실시예·청구항을 거듭 붙이지 않는다. 주변 장치 설명, 일반 배경, 절 제목이나 도면 소개만 하는 문장은 제외한다. 근거 수를 채우기 위해 약한 대응을 추가하지 않는다.
reasoning에는 원문·번역을 다시 옮기지 말고 대응 관계만 짧게 쓴다. 발췌 참조는 {{E1}}로 적고 문헌 참조는 {{ATT-01}}처럼 실제 자료 번호를 쓴다.
외국어 근거는 필요한 선택 범위 전체의 충실한 번역을 기록한다. 프로그램은 번역 (위치; 원문 확인용 짧은 구절) 순으로 출력한다.
표시용 원문은 번역 전체와 같은 길이일 필요가 없다. 검증용 전체 원문은 보존하고 표시만 한 줄 길이로 축약한다. 생략부호는 표시용이며 원문 자체에 추가하지 않는다.
한국어 근거는 실제 대응 내용을 필요한 만큼 발췌하고 위치를 붙인다. 한 줄 제한을 적용하지 않는다.
프로그램이 동일 발췌의 중복 출력을 막고 앞의 구성·근거를 참조하므로 같은 발췌를 설명에 다시 작성하지 않는다.
차이점은 구성별 한 줄이며 미대응 한정·관계 또는 확인 불가 범위만 담는다. difference에 근거 ID, 번역, 발췌, 해소 판단, 보완 문헌 소개를 넣지 않는다.
summary.main_reason은 주 문헌 선정 이유, summary.relationships는 전체 유사점과 잔존 차이만 간결하게 쓴다. 법적 최종 결론을 쓰지 않는다.
근거 검증 실패·누락은 별도의 보고서 항목 점검으로 표시하며 모델의 점수를 대체하지 않는다. 모든 청구항 구성을 빠뜨리지 않는다.
문헌번호가 없으면 document_number를 빈 문자열로 둔다. 문헌 번호와 보고서 서식은 프로그램이 처리한다.
{
 "version":5,
 "documents":[{"attachment":"ATT-01","document_number":"","title":""},{"attachment":"ATT-02","document_number":"","title":""}],
 "evidence":[{"id":"E1","attachment":"ATT-01","sentence_ids":["ATT-01-P3-T2"],"language":"ko","translation":""},{"id":"E2","attachment":"ATT-02","sentence_ids":["ATT-02-P1-T1"],"language":"foreign","translation":"선택한 원문 범위의 충실한 번역"}],
 "components":[{"claim":"청구항 1","symbol":"(A)","feature":"청구항 구성 원문을 요약 없이 그대로","similarity":70,"basis":"direct","evidence":["E1","E2"],"evidence_uses":{"E1":"support","E2":"support"},"reference_roles":{},"reasoning":"청구항 구성 (A)의 첫 필수 한정은 {{ATT-01}}의 {{E1}}에 기재된 내용에 대응합니다. 청구항의 다른 필수 한정은 {{ATT-01}}에서 확인되지 않지만, {{ATT-02}}의 {{E2}}에 기재된 동작에 대응합니다.","difference":"두 문헌에서도 확인되지 않은 구체적인 연결 조건입니다."}],
 "summary":{"main_reason":"주 문헌 선정 이유","relationships":"전체 구성 관계 및 잔존 차이"}
}
'''


class ReportError(ValueError):
    pass


def text(value):
    return value.strip() if isinstance(value, str) else ''


def original_preview(value, limit=120):
    """A display-only, contiguous locator snippet; never the validation source."""
    value = re.sub(r'\s+', ' ', value).strip()
    if len(value) <= limit:
        return value
    prefix = value[:limit - 1]
    boundary = prefix.rfind(' ')
    if boundary > limit // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip() + '…'


def paragraph_markers(content):
    """Locate structural paragraph labels, not bracketed bibliography years.

    Patent labels begin a line and usually have zero padding. Unpadded labels
    need a consecutive numbering sequence; an isolated line-wrapped [2000]
    is insufficient. Ambiguous sources keep their verified PDF page location.
    This does not change the historical sentence IDs used for evidence.
    """
    candidates = list(re.finditer(
        r'(?m)^[ \t]*(?P<label>\[[ \t]*(?P<number>\d{4,6})[ \t]*\])(?=\s|$)', content))
    numbered = set()
    for first, second in zip(candidates, candidates[1:]):
        if int(second['number']) == int(first['number']) + 1:
            numbered.update((first.start(), second.start()))
    return [m for m in candidates if m['number'].startswith('0') or m.start() in numbered]


def no_remaining_difference(value):
    """Recognize only standalone no-gap declarations, never infer from prose.

    Keep the original text in the report/data. A qualifier, second sentence,
    negation or specific limitation must still undergo the conservative check.
    """
    value = re.sub(r'\s+', ' ', text(value)).removesuffix('.').strip()
    return value in {
        '없음', '없습니다', '차이 없음', '차이가 없습니다',
        '잔존 차이 없음', '잔존 차이가 없습니다', '남은 차이가 없습니다',
        '차이가 해소됩니다', '차이는 해소됩니다', '차이가 해소되었습니다',
        '실질적 기술 구성상의 차이는 해소됩니다',
    }


def prose(value, *, original=False):
    value = html.escape(text(value), quote=False)
    if not original:
        value = value.replace('“', '"').replace('”', '"')
    return re.sub(r'([\\`*_\[\]{}#|~$])', r'\\\1', value).replace('\n', ' ')


def decode(raw):
    raw = raw.strip()
    if raw.startswith('```') and raw.endswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)[:-3].strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ReportError('구조화된 분석 결과를 읽지 못했습니다. 원 응답은 보존했습니다.') from exc
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] not in (1, 2, 3, 4, 5):
        raise ReportError('지원하지 않는 보고서 형식입니다.')
    for key, limit in (('documents', 100), ('evidence', 600), ('components', 300)):
        rows = data.get(key)
        if not isinstance(rows, list) or len(rows) > limit or any(not isinstance(row, dict) for row in rows):
            raise ReportError(f'{key} 목록 형식 또는 개수 오류입니다.')
    if not data['documents'] or not data['components']:
        raise ReportError('문헌 또는 청구항 구성 목록이 비어 있습니다.')
    return data


def instructions(*, retrieved=False):
    return INSTRUCTIONS.replace('ATT-01-P3-T2', 'S001-T2').replace('ATT-02-P1-T1', 'S002-T1') if retrieved else INSTRUCTIONS


def source_catalog(aliases, attachments, bundle=None):
    """The exact same delivered sources for compilation and selection repair."""
    pool, sources, errors = {}, {}, []
    if bundle is not None:
        from .retrieval.evidence import source_pool
        pool = {row['id']: row for row in source_pool(bundle).sources}
        for source in pool.values():
            sources.setdefault(source['attachment'], {'pages': []})['pages'].append((source['pdf_page'], source['text']))
        rows = list(pool.values())
    else:
        delivered = [a for a in attachments if a.delivery_mode == DeliveryMode.INLINE_CONTEXT]
        sources, errors = analysis_evidence.source_pages(delivered, aliases)
        rows = [{'id': f'{alias}-P{page or 0}', 'attachment': alias, 'pdf_page': page, 'text': content}
                for alias, source in sources.items() for page, content in source['pages']]
    return pool, sources, errors, rows


def compile_report(raw, aliases, attachments, *, prior_mapping=None, bundle=None):
    data = decode(raw)
    multi_comparison = data['version'] >= 4
    issues = []
    allowed = {a.attachment_id for a in attachments if a.included and a.role != AttachmentRole.APPLICATION}
    previous = {row['attachment_sha256']: row for row in (prior_mapping or {}).get('items', [])}
    next_number = max((row['citation_number'] for row in previous.values()), default=0) + 1
    documents, mapped = {}, []
    for row in data['documents']:
        alias = text(row.get('attachment')).upper()
        if alias not in aliases or aliases[alias].attachment_id not in allowed:
            raise ReportError(f'실제 인용문헌에 없는 자료 번호입니다: {alias}')
        if alias in documents:
            raise ReportError(f'중복 문헌입니다: {alias}')
        old = previous.get(aliases[alias].sha256)
        number = old['citation_number'] if old else next_number
        if not old:
            next_number += 1
        document = {**row, 'attachment': alias, 'citation_number': number,
                    'document_number': old['document_number'] if old else text(row.get('document_number')) or '문헌번호 확인 불가'}
        documents[alias] = document
        mapped.append({key: document[key] for key in ('citation_number', 'attachment', 'document_number')})
    if previous and not any(row['citation_number'] == 1 for row in mapped):
        issues.append('고정 주 인용발명 1이 결과에 없습니다. 다른 문헌으로 대체하지 않았습니다.')
    try:
        mapping = citation_mapping.parse(citation_mapping._OPEN + json.dumps({'items': mapped}) + citation_mapping._CLOSE, aliases)
    except citation_mapping.MappingError as exc:
        raise ReportError(str(exc)) from exc

    pool, sources, source_errors, source_rows = source_catalog(aliases, attachments, bundle)
    issues.extend(source_errors)
    choices = {unit['id']: unit for source in source_rows
               for unit in report_sources.units(source['id'], source['attachment'], source['pdf_page'], source['text'])}
    sentence_choices = {unit['id']: unit for source in source_rows
                        for unit in report_sources.sentences(source['id'], source['attachment'], source['pdf_page'], source['text'])}
    evidence = {}
    for row in data['evidence']:
        eid, alias = text(row.get('id')), text(row.get('attachment')).upper()
        if not eid or eid in evidence:
            raise ReportError('근거 ID가 없거나 중복되었습니다.')
        item = {**row, 'attachment': alias, 'issues': [], 'verified': False}
        quote, page = text(row.get('quote')), row.get('page')
        if data['version'] >= 3:
            selected_ids = row.get('sentence_ids')
            if alias not in documents:
                item['issues'].append('근거의 인용문헌 연결을 확인하지 못했습니다.')
            elif any(field in row for field in ('quote', 'source_id', 'page', 'start', 'end')):
                item['issues'].append('문장 번호 방식에는 원문 재작성이나 별도 위치를 함께 지정할 수 없습니다.')
            elif (not isinstance(selected_ids, list) or not selected_ids or len(selected_ids) > 100
                  or any(not isinstance(sid, str) or sid not in sentence_choices for sid in selected_ids)):
                item['issues'].append('전달된 원문의 문장 번호를 확인하지 못했습니다.')
            else:
                selected = [sentence_choices[sid] for sid in selected_ids]
                first = selected[0]
                if any(s['attachment'] != alias for s in selected):
                    item['issues'].append('선택한 문장이 지정한 인용문헌에 속하지 않습니다.')
                elif any(s['source_id'] != first['source_id'] or s['order'] != first['order'] + i
                         for i, s in enumerate(selected)):
                    item['issues'].append('떨어진 문장·다른 페이지는 각각 별도 근거와 번역으로 선택해야 합니다.')
                else:
                    page = first['pdf_page']
                    # Reconstruct only the selected delivered source. No search,
                    # transcription repair, or model-written quote is involved.
                    content = (pool[first['source_id']]['text'] if pool else
                               next(content for p, content in sources[alias]['pages'] if p == page))
                    quote = content[first['start']:selected[-1]['end']].strip()
                    item.update(verified=True, resolution_method='source_sentence_ids',
                                source_start=first['start'], source_end=selected[-1]['end'])
        elif data['version'] == 2 and not quote:
            item['issues'].append('번역 대상 원문 quote가 누락되었습니다. 구간 전체로 대체하지 않습니다.')
        elif data['version'] == 2 and ('start' in row or 'end' in row):
            item['issues'].append('새 보고서는 문자 범위 대신 선택 번호와 번역 대상 원문 quote를 사용해야 합니다.')
        elif alias not in documents:
            item['issues'].append('근거의 인용문헌 연결을 확인하지 못했습니다.')
        elif row.get('source_id'):
            source_id = text(row['source_id'])
            choice = choices.get(source_id)
            source = pool.get(source_id)
            start, end = row.get('start'), row.get('end')
            if choice and choice['attachment'] == alias and 'start' not in row and 'end' not in row:
                # A supplied quote narrows the selected context. Never silently
                # replace an invalid quote with the whole (possibly long) unit.
                if 'quote' in row:
                    hit = analysis_evidence.anchor(quote, choice['text']) if quote else None
                    if hit:
                        quote, page = choice['text'][hit[0]:hit[1]], choice['pdf_page']
                        item['verified'] = True
                    else:
                        # A unit boundary is indexing metadata, not a sentence
                        # boundary. Accept only an exact, continuous quote which
                        # starts in the chosen unit of the same delivered page.
                        matches = []
                        for source_page, content in sources.get(alias, {}).get('pages', []):
                            if source_page != choice['pdf_page']:
                                continue
                            selected = analysis_evidence.anchor(choice['text'], content)
                            candidate = analysis_evidence.anchor(quote, content)
                            if (selected and candidate and selected[0] <= candidate[0] < selected[1]
                                    and analysis_evidence.compact(content).count(analysis_evidence.compact(quote)) == 1):
                                matches.append((content[candidate[0]:candidate[1]], source_page))
                        matches = list(dict.fromkeys(matches))
                        if len(matches) == 1:
                            quote, page = matches[0]
                            item.update(verified=True, resolution_method='continuous_same_page')
                        else:
                            # Treat the copied quote as the source of truth for
                            # location, but never broaden the delivered corpus or
                            # fuzzy-match/splice/expand the translated span.
                            exact_matches = set()
                            ambiguous = False
                            for source_page, content in sources.get(alias, {}).get('pages', []):
                                compact_quote = analysis_evidence.compact(quote)
                                count = analysis_evidence.compact(content).count(compact_quote) if compact_quote else 0
                                candidate = analysis_evidence.anchor(quote, content)
                                if count > 1:
                                    ambiguous = True
                                if candidate:
                                    exact_matches.add((source_page, content[candidate[0]:candidate[1]]))
                            if len(exact_matches) == 1 and not ambiguous:
                                page, quote = exact_matches.pop()
                                item.update(verified=True, requested_source_id=source_id,
                                            resolution_method='unique_exact_quote',
                                            location_note='선택 번호 오류를 같은 문헌의 전달 원문 내 유일한 일치 위치로 정정했습니다.')
                            else:
                                reason = ('여러 원문 위치에서 발췌가 일치하여 위치를 확정하지 못했습니다.' if ambiguous or len(exact_matches) > 1 else
                                          '모델 발췌에 생략부호가 포함되어 연속 원문과 일치하지 않습니다.'
                                          if re.search(r'\.{3}|…', quote) else
                                          '전달된 동일 문헌에서 발췌와 일치하는 연속 원문을 확인하지 못했습니다. 문구 변경·생략·중간 머리말 포함 여부를 확인해야 합니다.')
                                item['issues'].append(reason)
                else:  # Compatibility with previously generated ID-only payloads.
                    quote, page = choice['text'], choice['pdf_page']
                    item['verified'] = True
            elif (source and source['attachment'] == alias and type(start) is int and type(end) is int
                    and 0 <= start < end <= len(source['text'])):
                quote, page = source['text'][start:end], source['pdf_page']
                item['verified'] = True
            else:
                item['issues'].append('공유 원문 ID 또는 발췌 범위를 확인하지 못했습니다.')
        elif quote:
            if page is not None and (type(page) is not int or page < 1):
                item['issues'].append('페이지 형식이 잘못되었습니다.')
            else:
                for source_page, content in sources.get(alias, {}).get('pages', []):
                    if page is not None and source_page != page:
                        continue
                    hit = analysis_evidence.anchor(quote, content)
                    if hit:
                        quote, page = content[hit[0]:hit[1]], source_page
                        item['verified'] = True
                        break
                if not item['verified'] and page is not None:
                    # Correct only an unambiguous exact quote, never a fuzzy match.
                    hits = []
                    for source_page, content in sources.get(alias, {}).get('pages', []):
                        hit = analysis_evidence.anchor(quote, content)
                        if hit:
                            hits.append((source_page, content[hit[0]:hit[1]]))
                    hits = list(dict.fromkeys(hits))
                    if len(hits) == 1:
                        item['requested_page'] = page
                        page, quote = hits[0]
                        item['verified'] = True
                        item['issues'].append('페이지 오기를 전달된 원문의 유일한 일치 페이지로 정정했습니다.')
                    elif hits:
                        item['issues'].append('지정 페이지 밖의 여러 위치에서 원문이 일치하여 위치를 확정하지 못했습니다.')
        # An invented unit number is a location error, not proof that an exact
        # quotation is absent. Recover only within the declared, delivered
        # document; malformed offsets and another document's ID stay rejected.
        if (not item['verified'] and data['version'] == 2 and quote and alias in documents
                and 'start' not in row and 'end' not in row
                and re.fullmatch(re.escape(alias) + r'-P\d+-U\d+', text(row.get('source_id')))):
            matches = set()
            ambiguous = False
            for source_page, content in sources.get(alias, {}).get('pages', []):
                compact_quote = analysis_evidence.compact(quote)
                if analysis_evidence.compact(content).count(compact_quote) > 1:
                    ambiguous = True
                hit = analysis_evidence.anchor(quote, content)
                if hit:
                    matches.add((source_page, content[hit[0]:hit[1]]))
            if len(matches) == 1 and not ambiguous:
                page, quote = matches.pop()
                item.update(verified=True, issues=[], requested_source_id=text(row['source_id']),
                            resolution_method='unique_exact_quote',
                            location_note='선택 번호 오류를 같은 문헌의 전달 원문 내 유일한 일치 위치로 정정했습니다.')
        if not item['verified'] and not item['issues']:
            item['issues'].append('원문과 일치하는 발췌를 확인하지 못했습니다. 직접 인용으로 표시하지 않습니다.')
        item.update(quote=quote, page=page, translation=text(row.get('translation')))
        foreign = row.get('language') == 'foreign' or analysis_evidence.needs_translation(quote)
        item['foreign'] = foreign
        if foreign and not item['translation']:
            item['issues'].append('한국어 번역이 누락되었습니다.')
        if re.search(r'\([a-z]\)\s*(?:\d+\s*)?$', quote):
            item['issues'].append('발췌가 열거 항목 중간에서 끝납니다. 다음 페이지 근거와 번역 범위를 확인해야 합니다.')
        evidence[eid] = item

    def reference(alias):
        doc = documents.get(alias)
        return (f"**인용발명 {doc['citation_number']}** ({prose(doc['document_number'])})"
                if doc else '**출처 미확인**')

    def narrative(value, *, break_references=False):
        # Resolve only explicit aliases; never infer a missing reference from context.
        pieces = re.split(r'(\{\{ATT-\d+\}\})', text(value))
        for part in pieces:
            if re.fullmatch(r'\{\{ATT-\d+\}\}', part) and part[2:-2] not in documents:
                issues.append('서술에서 확인되지 않은 문헌을 참조했습니다: ' + part[2:-2])
        return ''.join(('\n\n' if break_references else '') + reference(part[2:-2]) if re.fullmatch(r'\{\{ATT-\d+\}\}', part) else
                       (' ' if part[:1].isspace() else '') + prose(part) + (' ' if part[-1:].isspace() else '')
                       for part in pieces)

    def refs_list(value):
        # Only an unambiguous single ID is safe to wrap. Existence, attachment,
        # local linkage and original-text verification are checked separately.
        if isinstance(value, str) and re.fullmatch(r'E[1-9]\d*', value.strip()):
            value = [value.strip()]
        if not isinstance(value, list) or any(not isinstance(e, str) or not e.strip() for e in value):
            return [], ['근거 참조 목록 형식 오류']
        return list(dict.fromkeys(e.strip() for e in value)), []

    def mentioned_documents(values):
        mentions = set()
        for value in values:
            mentions.update(re.findall(r'\{\{(ATT-\d+)\}\}', text(value)))
            mentions.update(re.findall(r'(?<![A-Za-z0-9])ATT-\d+', text(value)))
            for number in re.findall(r'인용발명\s*(\d+)', text(value)):
                mentions.add(next((alias for alias, doc in documents.items()
                                   if doc['citation_number'] == int(number)), f'인용발명 {number}'))
            mentions.update(alias for alias, doc in documents.items()
                            if doc['document_number'] != '문헌번호 확인 불가'
                            and doc['document_number'] in text(value))
        return mentions

    def evidence_problems(refs, values, *, gap_values=(), declared=None, uses=None,
                          allow_absence=False, allow_unavailable=False, legacy_negative=False):
        problems = []
        usable = set()
        for eid in refs:
            item = evidence.get(eid)
            if item is None:
                problems.append(f'{eid}: 없는 근거 ID')
            elif not item['verified']:
                problems.append(f"{eid} ({item['attachment']}): 원문 발췌 검증 실패")
            elif item['foreign'] and not item['translation']:
                problems.append(f"{eid} ({item['attachment']}): 번역 누락")
            else:
                if uses is None or uses.get(eid) == 'support':
                    usable.add(item['attachment'])
        # Naming a document is not automatically a positive citation. Check
        # supporting uses, while preserving scoped no-match/unavailable reviews.
        mentions = mentioned_documents(values) | (mentioned_documents(gap_values) - {main})
        comparison_only = set()
        derived_support = set()
        if uses is not None:
            # Positive roles follow the actual support links. Never infer an
            # absence from a contrast quote or overwrite an explicit judgment.
            declared = dict(declared) if isinstance(declared, dict) else declared
            if declared is None:
                declared = {}
            if isinstance(declared, dict):
                for eid in refs:
                    if eid in evidence:
                        alias = evidence[eid]['attachment']
                        mentions.add(alias)
                        if uses.get(eid) == 'support':
                            if alias not in declared:
                                derived_support.add(alias)
                            declared.setdefault(alias, 'support')
                        elif uses.get(eid) == 'contrast':
                            comparison_only.add(alias)
                for alias in mentions:
                    if alias not in declared and alias not in comparison_only:
                        problems.append(f'{alias}: 긍정 대응 근거가 없는 문헌의 검토 결과가 누락되었습니다.')
        role_values = [*values, *[v for v in gap_values if mentioned_documents([v]) - {main}]]
        reviews, role_problems = report_references.assess(mentions, role_values, declared,
            known=set(documents), readable={a for a, s in sources.items()
                                            if any(text(body) for _, body in s.get('pages', []))},
            allow_absence=allow_absence, allow_unavailable=allow_unavailable,
            legacy_negative=legacy_negative, comparison_only=comparison_only)
        problems += role_problems
        for alias, review in reviews.items():
            if alias in derived_support and review['role'] == 'support':
                review['origin'] = 'support_links'
            review['source_ids'] = [s['id'] for s in source_rows if s['attachment'] == alias]
            if review['source_ids']:
                review['scope'] = 'retrieved_passages' if bundle is not None else 'provided_document_text'
            linked = [eid for eid in refs if eid in evidence and evidence[eid]['attachment'] == alias]
            positive = linked if uses is None else [eid for eid in linked if uses.get(eid) == 'support']
            if review['role'] != 'support':
                if review['role'] == 'unavailable' and linked:
                    problems.append(f'{alias}: 판독·판단 불가로 표시한 문헌에 발췌가 연결되었습니다. 실제 검토 가능 범위를 확인해야 합니다.')
                elif positive:
                    problems.append(f'{alias}: 미발견·확인 불가 문헌을 긍정 근거로도 연결했습니다. 참조 역할을 구분해야 합니다.')
                continue
            if alias in usable:
                continue
            doc = documents.get(alias)
            name = f"인용발명 {doc['citation_number']} ({alias})" if doc else alias
            linked = [eid for eid in refs if eid in evidence and evidence[eid]['attachment'] == alias]
            if not positive:
                if multi_comparison:
                    problems.append(f'{name}: 긍정 대응 문헌으로 표시했지만 이 항목에 해당 문헌의 대응 발췌가 연결되지 않았습니다.')
                else:
                    problems.append(f'{name}: 설명의 근거로 사용했지만 이 항목에 해당 문헌의 발췌가 연결되지 않았습니다.')
            # Invalid linked excerpts/translation already have a specific error above.
        return problems, reviews

    def excerpt(item):
        location = f"PDF 페이지 {item['page']}" if item['page'] else '텍스트 자료 · 페이지 구분 없음'
        # Derive paragraph numbers from verified source text, never model labels.
        paragraph_locations = set()
        paragraph_numbers = set()
        display_quotes = set()
        for page, content in sources.get(item['attachment'], {}).get('pages', []):
            if page != item['page']:
                continue
            hit = analysis_evidence.anchor(item['quote'], content)
            if not hit:
                continue
            markers = [m for m in paragraph_markers(content) if m.start('label') < hit[1]]
            preceding = [m for m in markers if m.start('label') <= hit[0]]
            relevant = ([preceding[-1]] if preceding else []) + [m for m in markers if m.start('label') > hit[0]]
            if relevant:
                numbers = list(dict.fromkeys(m['number'] for m in relevant))
                paragraph_locations.add('단락 ' + ', '.join('[' + n + ']' for n in numbers))
                paragraph_numbers.update(numbers)
                # Remove only labels at verified source positions, preserving
                # bibliography years even if they share a paragraph's digits.
                display_quote = content[hit[0]:hit[1]]
                for marker in reversed(relevant):
                    start, end = marker.span('label')
                    if start >= hit[0] and end <= hit[1]:
                        display_quote = display_quote[:start - hit[0]] + display_quote[end - hit[0]:]
                display_quotes.add(display_quote.strip())
        if len(paragraph_locations) == 1:
            location = prose(next(iter(paragraph_locations)))
        else:
            paragraph_numbers.clear()
        # Display paragraph labels only in the location, preserving stored raw evidence.
        def display_excerpt(value):
            def remove_label(match):
                line_prefix = value[:match.start()].rsplit('\n', 1)[-1]
                is_label = match[1].startswith('0') or not line_prefix.strip()
                return '' if match[1] in paragraph_numbers and is_label else match[0]
            return re.sub(r'\[\s*(\d{4,6})\s*\]',
                          remove_label, value).strip()

        original = next(iter(display_quotes)) if len(paragraph_locations) == 1 and len(display_quotes) == 1 else item['quote']
        quote = '"' + prose(original, original=True) + '"'
        if item['foreign']:
            translation = '"' + prose(display_excerpt(item['translation'])) + '"' if item['translation'] else '[번역 미제공]'
            if multi_comparison:
                quote = '"' + prose(original_preview(original), original=True) + '"'
            return translation + ' (' + location + '; ' + quote + ')'
        return quote + ' (' + location + ')'

    displayed_evidence = {}

    def render_evidence(refs, label, *, show_reference=True, uses=None):
        output = []
        for eid in refs:
            item = evidence.get(eid)
            if item is None:
                output += ['', f'> 확인 필요: {prose(eid)} 근거가 없습니다.']
                issues.append(label + ': 없는 근거 ID ' + eid)
                continue
            if item['verified']:
                key = (item['attachment'], item['page'], re.sub(r'\s+', ' ', item['quote']).strip())
                previous_excerpt = displayed_evidence.get(key) if multi_comparison else None
                if previous_excerpt:
                    first_label, first_id = previous_excerpt
                    purpose = ' (비교 설명)' if uses and uses.get(eid) == 'contrast' else ''
                    output += ['', f'근거 {prose(eid)}{purpose}: {prose(first_label)}의 근거 {prose(first_id)} 참조.']
                else:
                    needs_reference = multi_comparison or show_reference or documents[item['attachment']]['citation_number'] != 1
                    prefix = reference(item['attachment']) + ': ' if needs_reference else ''
                    if multi_comparison:
                        prefix += f'근거 {prose(eid)} — '
                    if uses and uses.get(eid) == 'contrast':
                        prefix += '비교 설명: '
                    output += ['', prefix + excerpt(item)]
                    # A missing translation must not suppress a later valid one.
                    if not item['issues']:
                        displayed_evidence[key] = (label, eid)
            for issue in item['issues']:
                detail = f"{eid} ({item['attachment']}): {issue}"
                issues.append(detail)
            if item['issues']:
                output += ['', f'> {prose(eid)}: 근거 확인 필요 (보고서 항목 점검 참조).']
        return output

    resolution_checks = []

    def render_resolution(value, label, component_refs, symbol):
        # Historical V1–V3 payloads only. V4 places all support in comparison
        # and renders only the explicit residual difference below.
        if value is None or value == '':
            return []
        output = []
        problems = []
        if not isinstance(value, dict):
            explanation = text(value)
            refs = []
            gap, remaining, conclusion = '', '', ''
            problems.append('차이 해소 검토가 근거와 연결된 구조로 제공되지 않았습니다.')
        else:
            gap, explanation = text(value.get('gap')), text(value.get('explanation'))
            remaining, conclusion = text(value.get('remaining_difference')), text(value.get('conclusion'))
            refs, problems = refs_list(value.get('evidence'))
            if 'supplements' in value:
                chunks = []
                supplements = value['supplements']
                if not isinstance(supplements, list) or any(not isinstance(s, dict) for s in supplements):
                    problems.append('보완 문헌별 설명 목록 형식이 잘못되었습니다.')
                    supplements = []
                for supplement in supplements:
                    alias = text(supplement.get('attachment')).upper()
                    selected, selection_problems = refs_list(supplement.get('evidence'))
                    detail = text(supplement.get('explanation'))
                    if alias not in documents or alias == main:
                        problems.append('차이점 보완 문헌에는 주 인용발명 외의 실제 문헌만 사용할 수 있습니다.')
                        continue
                    if (selection_problems or not selected or not detail or
                            any(eid not in refs or eid not in evidence or evidence[eid]['attachment'] != alias for eid in selected)):
                        problems.append(f'{alias}: 보완 문헌·근거 연결 또는 대응 설명을 확인해야 합니다.')
                        if detail:
                            # Preserve authored analysis without presenting an
                            # invalid link as a source-verified quotation.
                            chunks.append('{{' + alias + '}} 관련 설명 (근거 연결 확인 필요): ' +
                                          re.sub(r'\{\{E\d+\}\}', '[근거 연결 확인 필요]', detail))
                        continue
                    if re.search(r'\{\{E\d+\}\}', detail):
                        problems.append(f'{alias}: 설명에 발췌 참조를 중복 작성했습니다.')
                        continue
                    chunks.append('{{' + alias + '}}에는 ' + ', '.join('{{' + eid + '}}' for eid in selected)
                                  + '라는 기재가 있으며, ' + detail)
                derivation = text(value.get('derivation'))
                if re.search(r'\{\{E\d+\}\}', derivation):
                    problems.append('도출 설명에서 구성대비 발췌를 반복하지 않아야 합니다.')
                elif derivation:
                    chunks.append(derivation)
                explanation = '\n\n'.join(chunks)
            # A comparison passage is not automatically needed for this gap.
            # Only explicitly linked resolution evidence belongs in this section.
            if not gap:
                problems.append('보완할 차이가 누락되었습니다.')
            if not explanation and not (conclusion == 'insufficient' and remaining):
                problems.append('보완 방법과 기술적 이유가 누락되었습니다.')
            if conclusion not in {'supported', 'remaining_gap', 'insufficient'}:
                problems.append('해소 판단 상태가 누락되었거나 잘못되었습니다.')
            no_gap = no_remaining_difference(remaining)
            if conclusion in {'remaining_gap', 'insufficient'} and (not remaining or no_gap):
                problems.append('잔존 차이 또는 부족한 근거 설명이 누락되었습니다.')
            if conclusion == 'supported' and remaining and not no_gap:
                problems.append('해소 판단과 잔존 차이가 모순됩니다.')
        if not refs and conclusion != 'insufficient':
            problems.append('보완 판단에 필요한 원문 근거가 연결되지 않았습니다.')
        declared = value.get('reference_roles') if isinstance(value, dict) else None
        reference_problems, source_reviews = evidence_problems(refs, [explanation], gap_values=[gap, remaining],
            declared=declared, allow_absence=True, allow_unavailable=conclusion == 'insufficient',
            legacy_negative=conclusion == 'insufficient' and not refs)
        problems += reference_problems
        inline_refs = re.findall(r'\{\{(E\d+)\}\}', explanation)
        for eid in inline_refs:
            if eid not in refs:
                problems.append(f'{eid}: 차이점 문장에 사용한 근거가 근거 목록에 연결되지 않았습니다.')
        status = 'needs_review' if problems else conclusion
        resolution_checks.append({'component': label, 'status': status, 'model_conclusion': conclusion,
                                  'evidence': refs, 'issues': problems, 'source_reviews': source_reviews})
        paragraph = '구성 ' + prose(symbol) + '에 대해 ' + narrative(gap, break_references=True)
        if problems:
            output += ['', '**출력 점검: 근거·항목 연결 확인 필요 (분석 서술은 아래에 보존).**']
            for problem in problems:
                issues.append(label + ' 차이 해소 검토: ' + problem)
                output += ['', '> 확인 필요: ' + prose(problem)]
        # Older payloads without inline anchors still keep all evidence visible.
        structured_difference = isinstance(value, dict) and 'supplements' in value
        for eid in refs if not structured_difference else []:
            if eid not in inline_refs:
                item = evidence.get(eid)
                if item and item['verified'] and item['attachment'] != main:
                    paragraph += '\n\n' + reference(item['attachment']) + '의 ' + excerpt(item) + '.'
        if explanation:
            parts = re.split(r'(\{\{E\d+\}\}|\n\s*\n)', explanation)
            rendered = []
            for part in parts:
                if re.fullmatch(r'\n\s*\n', part):
                    rendered.append('\n\n')
                elif re.fullmatch(r'\{\{E\d+\}\}', part):
                    eid = part[2:-2]
                    item = evidence.get(eid)
                    if eid in refs and item and item['verified'] and item['attachment'] == main:
                        rendered.append('구성대비에 제시한 근거')
                    else:
                        rendered.append(excerpt(item) if eid in refs and item and item['verified'] else '[근거 확인 필요]')
                else:
                    rendered.append((' ' if part[:1].isspace() else '') + narrative(part, break_references=True)
                                    + (' ' if part[-1:].isspace() else ''))
            paragraph += '\n\n' + ''.join(rendered).strip()
        if remaining:
            paragraph += '\n\n' + narrative(remaining, break_references=True).strip()
        if any(r['role'] == 'not_found' for r in source_reviews.values()):
            paragraph += '\n\n검토 범위 안내: 미발견은 제공된 자료 범위에서의 AI 검토 결과입니다.'
        paragraph = re.sub(r'\n(?:[ \t]*\n)+', '\n\n', paragraph)
        output += ['', paragraph]
        # Preserve evidence validation notices without duplicating quotations.
        for eid in refs:
            item = evidence.get(eid)
            if not item or item['issues']:
                notices = render_evidence([eid], label + ' 차이 해소 검토')
                output += [line for line in notices if not line or line.startswith('>')]
        return output

    lines = ['# 구성대비 분석 보고서', '', '## 1. 문헌 매핑', '',
             '| 인용발명 | 문헌명 또는 파일명 | 고유 문헌번호 |', '|---|---|---|']
    for doc in sorted(documents.values(), key=lambda row: row['citation_number']):
        lines.append(f"| 인용발명 {doc['citation_number']} | {prose(doc.get('title') or aliases[doc['attachment']].original_filename)} | {prose(doc['document_number'])} |")
    lines += ['', '## 2. 청구항 구성별 분석']
    lines += ['', '※ 발췌의 단락번호는 위치 표시에 모아 표시합니다. 검증용 원문은 그대로 보존합니다.']
    if multi_comparison:
        lines += ['', '※ 외국어 문헌은 필요한 범위의 번역과 위치 확인용 짧은 원문을 표시합니다. 유사도는 인용발명 1 기준입니다.']
    components = []
    current_claim = None
    differences = []
    comparison_checks = []
    main = next((alias for alias, doc in documents.items() if doc['citation_number'] == 1), None)
    seen = set()
    normalizations = []
    occupied_labels = {(text(r.get('claim')), text(r.get('symbol'))) for r in data['components']}
    for index, original_row in enumerate(data['components'], 1):
        row = dict(original_row)
        if multi_comparison and not text(row.get('symbol')) and (
                row.get('symbol') is None or isinstance(row.get('symbol'), str)):
            number = 1
            while (text(row.get('claim')), f'(자동 구분 {number})') in occupied_labels:
                number += 1
            row['symbol'] = f'(자동 구분 {number})'
            occupied_labels.add((text(row.get('claim')), row['symbol']))
            normalizations.append({'component_index': index, 'field': 'symbol',
                                   'original': original_row.get('symbol'), 'value': row['symbol']})
        label = text(row.get('claim')) + ' ' + text(row.get('symbol'))
        if label in seen:
            raise ReportError(f'중복 구성입니다: {label}')
        seen.add(label)
        all_refs, problems = refs_list(row.get('evidence', []))
        if multi_comparison:
            # An explicit reference is an authored link, even when the model
            # omitted it from the array. Never infer a link from a document name.
            for eid in re.findall(r'\{\{(E\d+)\}\}', text(row.get('reasoning'))):
                item = evidence.get(eid)
                if eid not in all_refs and item and item['verified'] and not item['issues']:
                    all_refs.append(eid)
                    normalizations.append({'component_index': index, 'field': 'evidence',
                                           'added': eid, 'reason': 'explicit_reasoning_reference'})
        # Preserve the meaning of stored V1–V3 reports on replay. New V4
        # reports have no primary-only comparison filter or mixed-source error.
        excluded = [] if multi_comparison else [eid for eid in all_refs if eid in evidence and evidence[eid]['attachment'] != main]
        refs = [eid for eid in all_refs if eid not in excluded]
        mentioned = set(re.findall(r'(?<![A-Za-z0-9])ATT-\d+', text(row.get('reasoning'))))
        for number in re.findall(r'인용발명\s*(\d+)', text(row.get('reasoning'))):
            mentioned.update(alias for alias, doc in documents.items() if doc['citation_number'] == int(number))
        mentioned.update(alias for alias, doc in documents.items()
                         if doc['document_number'] != '문헌번호 확인 불가'
                         and doc['document_number'] in text(row.get('reasoning')))
        mixed = not multi_comparison and bool(excluded or mentioned - {main})
        if mixed:
            problems.append('구성대비 근거 또는 대응 이유에 보완 문헌이 혼입되었습니다. 구성대비에는 주 문헌 근거만 표시하며 모델의 유사도는 그대로 보존합니다.')
        if not main:
            problems.append('주 인용발명 1의 자료가 없어 구성대비를 확정하지 않았습니다.')
        comparison_checks.append({'component': label, 'component_index': index,
                                  'evidence': refs, 'excluded_evidence': excluded,
                                  'status': 'needs_review' if mixed or not main else
                                  'multi_document' if multi_comparison else 'main_only'})
        score = row.get('similarity')
        status = 'unreadable' if score is None else 'matched' if type(score) is int and score >= 80 else 'below_threshold'
        try:
            parsed = analysis_manifest.parse(analysis_manifest._OPEN + json.dumps({'items': [{**row, 'similarity': score, 'status': status}]}) + analysis_manifest._CLOSE)['items'][0]
            parsed['id'] = f'C{index:03d}'
            components.append(parsed)
            grade = ('원문 재확인 필요' if score is None else
                     f"{'동일 🔵' if score >= 95 else '실질적 동일 🟢' if score >= 80 else '일부 차이 🟡' if score > 0 else '대응 없음 ⚪'} ({score}%)")
        except analysis_manifest.ComponentAnalysisError as exc:
            problems.append(str(exc))
            grade = '결과 항목 확인 필요'
        claim = text(row.get('claim')) or '청구항 미기재'
        if claim != current_claim:
            if current_claim is not None:
                lines += ['', '---', '', '#### [차이점]', *(differences or ['', '확인된 차이점이 없습니다.'])]
                differences = []
            lines += ['', '### ' + prose(claim)]
            lines += ['', '유사도 기준 문헌: ' + reference(main), '', '#### [구성요소]']
            current_claim = claim
        lines += ['', f"**{prose(row.get('symbol'))} {grade}**"]
        lines += ['', prose(row.get('feature'))]
        uses = None
        if data['version'] >= 5 or 'evidence_uses' in row:
            uses = row.get('evidence_uses')
            if not isinstance(uses, dict):
                problems.append('근거별 용도 evidence_uses는 support/contrast 객체여야 합니다.')
                uses = {}
            for eid in refs:
                if uses.get(eid) not in ('support', 'contrast'):
                    problems.append(f'{eid}: 근거 용도 support/contrast가 누락되었거나 잘못되었습니다.')
            for eid in uses:
                if eid not in refs:
                    problems.append(f'{eid}: 근거 용도만 있고 구성의 발췌 목록에 연결되지 않았습니다.')
            for eid in refs:
                if eid not in re.findall(r'\{\{(E\d+)\}\}', text(row.get('reasoning'))):
                    problems.append(f'{eid}: 선택한 발췌가 구성의 설명에 연결되지 않았습니다.')
        reference_problems, source_reviews = evidence_problems(refs, [row.get('reasoning')],
            declared=row.get('reference_roles'), uses=uses, allow_absence=multi_comparison or type(score) is int and score == 0,
            allow_unavailable=multi_comparison or score is None,
            legacy_negative=type(score) is int and score == 0 and not refs)
        problems += reference_problems
        if multi_comparison:
            main_role = source_reviews.get(main, {}).get('role')
            if main_role == 'unavailable' and score is not None:
                problems.append('주 인용발명 확인 불가 표시와 유사도가 일치하지 않습니다.')
            if main_role == 'not_found' and score != 0:
                problems.append('주 인용발명에서 대응 근거 미발견으로 판단했으면 해당 문헌 기준 유사도는 0이어야 합니다.')
            if type(score) is int and score > 0 and not any(
                    eid in evidence and evidence[eid]['attachment'] == main
                    and (uses is None or uses.get(eid) == 'support')
                    and evidence[eid]['verified'] and not evidence[eid]['issues'] for eid in refs):
                problems.append('주 인용발명 유사도를 뒷받침하는 해당 문헌의 근거가 누락되었습니다.')
            for eid in re.findall(r'\{\{(E\d+)\}\}', text(row.get('reasoning'))):
                if eid not in refs:
                    problems.append(f'{eid}: 대응 이유의 근거가 구성대비 근거 목록에 연결되지 않았습니다.')
            if row.get('resolution'):
                problems.append('새 보고서는 보완 근거·설명을 구성대비에, 잔존 차이만 difference에 기록해야 합니다. 이전 차이 해소 객체는 표시하지 않았습니다.')
        comparison_checks[-1].update(source_reviews=source_reviews)
        if uses is not None:
            comparison_checks[-1]['evidence_uses'] = uses
        lines += render_evidence(refs, label, show_reference=False, uses=uses)
        if not refs:
            negative = any(r['role'] == 'not_found' for r in source_reviews.values())
            lines += ['', ('대응 근거 미발견 — 제공된 자료 범위에서의 AI 검토 결과입니다.' if negative else
                          '원문 근거 미확인 — 확인한 범위와 한계는 아래 분석을 참조하십시오.')]
            if type(score) is int and score > 0:
                problems.append('대응 판단을 뒷받침하는 근거가 누락되었습니다.')
        difference_problem = None
        if multi_comparison and not isinstance(row.get('difference'), str):
            difference_problem = '복수 문헌 대비 후 차이점 필드가 누락되었거나 잘못되었습니다.'
            problems.append(difference_problem)
        if multi_comparison and re.search(r'\{\{E\d+\}\}', text(row.get('difference'))):
            problems.append('차이점에는 발췌 참조 대신 미대응 부분만 작성해야 합니다.')
        if multi_comparison and not text(row.get('reasoning')):
            problems.append('대응 이유가 누락되었습니다.')
        comparison_checks[-1]['issues'] = list(dict.fromkeys(problems))
        if multi_comparison:
            comparison_checks[-1]['repair_scope'] = (
                'difference_only' if difference_problem and set(problems) == {difference_problem} else 'component')
        if problems:
            comparison_checks[-1]['status'] = 'needs_review'
            lines += ['', '**출력 점검: 근거·항목 연결 확인 필요**']
            for problem in problems:
                issues.append(label + ': ' + problem)
                lines += ['', '> 확인 필요: ' + prose(problem)]
        reasoning = text(row.get('reasoning'))
        if not reasoning and not multi_comparison:
            issues.append(label + ': 대응 이유가 누락되었습니다.')
        elif reasoning and not mixed:
            # Describe the selected passages without printing their quotations again.
            readable_reasoning = re.sub(r'\{\{(E\d+)\}\}',
                lambda m: (f'근거 {m[1]}' if multi_comparison else f'위 {refs.index(m[1]) + 1}번째 발췌')
                if m[1] in refs else '[근거 연결 확인 필요]', reasoning)
            lines += ['', '대응 이유: ' + narrative(readable_reasoning)]
        if multi_comparison:
            # V4 difference is the residual after ALL cited documents, independent
            # of the primary-document similarity score. No resolution quotations.
            if not isinstance(row.get('difference'), str):
                differences += ['', '구성 ' + prose(row.get('symbol')) + ': 잔존 차이 확인 필요.']
            else:
                remaining = text(row['difference'])
                if remaining and not no_remaining_difference(remaining):
                    if re.search(r'\{\{E\d+\}\}', remaining):
                        remaining = re.sub(r'\{\{E\d+\}\}', '[구성대비 근거 참조]', remaining)
                    differences += ['', '구성 ' + prose(row.get('symbol')) + ': ' +
                                    narrative(re.sub(r'\s+', ' ', remaining))]
            continue
        resolution = row.get('resolution')
        if type(score) is int and 0 <= score < 95 and not text(row.get('difference')) and not (
                isinstance(resolution, dict) and text(resolution.get('gap'))):
            issue = f'{score}% 평가에 대한 구체적인 차이점 설명이 누락되었습니다. 점수와 대응 범위를 재확인해야 합니다.'
            issues.append(label + ': ' + issue)
            differences += ['', '구성 ' + prose(row.get('symbol')) + ': ' + issue]
        if text(row.get('difference')) and (resolution is None or resolution == ''):
            resolution = {'gap': row['difference']}  # A missing review must not disappear silently.
        differences += render_resolution(resolution, label, refs, text(row.get('symbol')))
    lines += ['', '---', '', '#### [차이점]', *(differences or ['', '확인된 차이점이 없습니다.'])]
    summary = data.get('summary') if isinstance(data.get('summary'), dict) else {}
    if any(check['status'] == 'needs_review' for check in resolution_checks):
        lines += ['', '> 출력 점검: 일부 근거·항목 연결을 확인해야 합니다. 분석 서술과 근거·항목 점검 결과를 구분하여 확인하십시오.']
    lines += ['', '## 3. 종합 분석 요약', '', '### 주 인용발명 선정 이유', '',
              narrative(summary.get('main_reason')) or '미기재', '', '### 청구항 전체의 관계와 잔존 차이', '',
              narrative(summary.get('relationships')) or '미기재']
    for field in ('main_reason', 'relationships'):
        if not text(summary.get(field)):
            issues.append('종합 분석 요약의 ' + field + ' 항목이 누락되었습니다.')
    issues = list(dict.fromkeys(issues))
    if issues:
        lines += ['', '## 보고서 항목 점검', '', '확인 가능한 결과를 보존했습니다. 아래 누락·불일치 항목은 자동으로 추정하지 않았습니다.',
                  *['- ' + prose(issue) for issue in issues]]
    result = {'version': 1, 'threshold': 80, 'items': components,
              'report': {'version': 1, 'data': data, 'evidence': evidence,
                         'resolution_checks': resolution_checks, 'comparison_checks': comparison_checks,
                         'normalizations': normalizations, 'issues': issues}}
    return '\n'.join(lines) + '\n', result, mapping
