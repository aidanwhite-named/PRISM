import asyncio
import copy
import json

import pytest

from app import comparison_generation as generation, comparison_review as review
from app.providers.base import ExecutionRequest
from .test_comparison_review import review_input, requirements, document, ScriptedProvider, emit
from .test_multi_document_report import multi_input
from .test_structured_report import report_input


def comparison(component, aliases=('ATT-01', 'ATT-02')):
    return {'id': component['id'], 'requested_source_ids': [], 'documents': [
        {'attachment': alias, 'aspects': [dict(a, status='matched' if alias == 'ATT-01' else 'not_found',
            sentence_ids=[f'{alias}-P0-T1'] if alias == 'ATT-01' else [], reason='원문의 동작과 관계를 비교했습니다.',
            missing='' if alias == 'ATT-01' else a['requirement'])
            for a in generation.aspects(component)]} for alias in aliases]}


def decision():
    return {'evidence': [{'id': 'E7', 'attachment': 'ATT-01', 'sentence_ids': ['ATT-01-P0-T1'],
                         'language': 'foreign', 'use': 'support'}],
        'component': {'similarity': 70, 'basis': 'direct', 'reference_roles': {},
            'reasoning': '청구항의 이득 변경은 {{ATT-01}}의 {{E7}}에 대응합니다.', 'difference': ''}, 'exclusions': []}


def selected_decision():
    row = decision()['component']
    return {'selected_documents': [{'attachment': 'ATT-01', 'use': 'support'}],
            'component': {k: v.replace('{{E7}}', '원문') if isinstance(v, str) else v
                          for k, v in row.items() if k != 'reference_roles'}}


def context_for(values):
    components = requirements(values)
    return {'components': components, 'documents': [document(components, a) for a in ('ATT-01', 'ATT-02')],
        'review_scope': 'provided_document_text', 'selection': {'primary_attachment': 'ATT-01',
            'document_order': ['ATT-01', 'ATT-02'], 'reason': '이득 변경 처리에 대응합니다.'}}


def test_component_pipeline_keeps_context_and_publication_cannot_reselect(tmp_path, review_input):
    context = context_for(review_input)
    sources = review.corpus(review_input[1], review_input[2])
    provider = ScriptedProvider([comparison(context['components'][0]), selected_decision()])
    request = ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='original',
                               user_message='entire original corpus', model='existing-model', reasoning_effort='medium')
    audit = {'attempts': []}
    options = dict(token_budget=None, max_chars=None, emit=emit, cancelled=lambda: False)
    async def run():
        context['component_comparisons'] = await generation.compare_components(provider, request, context, sources,
            '청구항 전체와 연결 관계', audit, **options)
        return await generation.plan_report(provider, request, context, sources, review_input[1], None, audit, **options)
    writer, plan = asyncio.run(run())
    assert all(r.model == 'existing-model' and r.reasoning_effort == 'medium' for r in provider.requests)
    assert json.loads(provider.requests[0].user_message)['claim_text'] == '청구항 전체와 연결 관계'
    assert 'entire original corpus' not in writer.user_message
    assert writer.system_prompt == generation.WRITE and writer.tool_policy.tools_disabled
    payload = json.loads(writer.user_message)
    assert payload['selected_evidence'][0]['text'] == sources[0]['sentences'][0]['text']
    assert plan['components'][0]['evidence'] == ['E1']
    assert '{{E1}}' in plan['components'][0]['reasoning']
    raw = json.dumps({'translations': [{'id': 'E1', 'translation': '제어기는 누적 소음에 따라 이득을 변경한다.'}],
        'summary': {'main_reason': '이득 변경에 대응합니다.', 'relationships': '이득 변경 관계를 확인했습니다.'}})
    result = json.loads(generation.publish(raw, plan))
    assert result['components'] == plan['components'] and result['documents'] == plan['documents']
    assert result['evidence'][0]['sentence_ids'] == plan['evidence'][0]['sentence_ids']
    assert not review.structured_report.compile_report(json.dumps(result), review_input[1], review_input[2])[1]['report']['issues']


def test_supplement_mlp_page_is_kept_in_candidates_and_decision(review_input):
    context = context_for(review_input)
    row = context['documents'][0]['components'][0]['limitations'][0]
    row['sentence_ids'] = ['ATT-01-P4-T1', 'ATT-01-P15-T1']
    context['documents'][0]['processes'] = []
    sources = [{'source_id': f'ATT-01-P{n}', 'attachment': 'ATT-01', 'pdf_page': n,
                'sentences': [{'id': f'ATT-01-P{n}-T1', 'text': 'MLP maps the decoder output.' if n == 15 else 'Mapping.'}]}
               for n in range(1, 17)]
    selected = generation.candidate_sources(context['components'][0], context, sources)
    assert 'ATT-01-P15' in {s['source_id'] for s in selected}
    comp = comparison(context['components'][0])
    comp['documents'][0]['aspects'][1]['sentence_ids'] = ['ATT-01-P15-T1']
    # Discarding a direct MLP fact for a less specific mapping sentence is caught.
    response = decision()
    response['evidence'][0]['sentence_ids'] = ['ATT-01-P4-T1']
    for aspect in comp['documents'][0]['aspects']:
        if aspect['id'] != 'operation':
            aspect['sentence_ids'] = ['ATT-01-P4-T1']
    with pytest.raises(ValueError, match='확인한 사실'):
        generation.validate_decision(response, context['components'][0], comp, context, selected)
    response['evidence'].append({'id': 'E8', 'attachment': 'ATT-01', 'sentence_ids': ['ATT-01-P15-T1'],
                                 'language': 'foreign', 'use': 'support'})
    response['component']['reasoning'] += ' MLP 변환은 {{E8}}에서 확인됩니다.'
    generation.validate_decision(response, context['components'][0], comp, context, selected)


def test_partial_facts_are_not_collapsed_into_document_absence(review_input):
    context = context_for(review_input)
    comp = comparison(context['components'][0])
    comp['documents'][0]['aspects'][-1].update(status='not_found', sentence_ids=[], missing='전체 연결 관계')
    generation.validate_comparison(comp, context['components'][0], context['documents'], review.corpus(review_input[1], review_input[2]))
    value = decision()
    value['component'].update(similarity=0, reference_roles={'ATT-01': 'not_found'})
    with pytest.raises(ValueError, match='부분 대응'):
        generation.validate_decision(value, context['components'][0], comp, context, review.corpus(review_input[1], review_input[2]))


@pytest.mark.parametrize('tamper', ['score', 'evidence', 'difference', 'duplicate_translation', 'missing_translation'])
def test_writer_cannot_change_the_frozen_analysis(review_input, tamper):
    plan = copy.deepcopy(review_input[0])
    value = {'translations': [{'id': e['id'], 'translation': e['translation']} for e in plan['evidence']],
             'summary': plan['summary']}
    if tamper in ('score', 'evidence', 'difference'):
        value[tamper] = 'changed'
    elif tamper == 'duplicate_translation':
        value['translations'].append(value['translations'][0])
    else:
        value['translations'].pop()
    with pytest.raises(ValueError):
        generation.publish(json.dumps(value), plan)


def test_unknown_or_cross_document_aspect_sources_are_rejected(review_input):
    context = context_for(review_input)
    comp = comparison(context['components'][0])
    comp['documents'][0]['aspects'][0]['sentence_ids'] = ['ATT-02-P0-T1']
    with pytest.raises(ValueError, match='원문'):
        generation.validate_comparison(comp, context['components'][0], context['documents'], review.corpus(review_input[1], review_input[2]))


def test_budget_failure_never_silently_cuts_candidate_sources(tmp_path, review_input):
    provider = ScriptedProvider([])
    context = context_for(review_input)
    with pytest.raises(ValueError, match='한도'):
        asyncio.run(generation.compare_components(provider, ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='', user_message=''),
            context, review.corpus(review_input[1], review_input[2]), 'claim', {'attempts': []},
            token_budget=None, max_chars=1, emit=emit, cancelled=lambda: False))
    assert not provider.requests


def test_expansion_and_validation_repair_use_only_the_same_component(tmp_path, review_input):
    context = context_for(review_input)
    sources = review.corpus(review_input[1], review_input[2])
    sources.extend({'source_id': f'ATT-01-P{n}', 'attachment': 'ATT-01', 'pdf_page': n,
                    'sentences': [{'id': f'ATT-01-P{n}-T1', 'text': 'Additional passage.'}]} for n in range(1, 8))
    comp = comparison(context['components'][0])
    bad = copy.deepcopy(comp)
    bad['documents'][0]['aspects'].pop()
    provider = ScriptedProvider([{'id': 'C001', 'documents': [], 'requested_source_ids': ['ATT-01-P7']}, bad, comp])
    result = asyncio.run(generation.compare_components(provider, ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='', user_message=''),
        context, sources, 'whole claim', {'attempts': []}, token_budget=None, max_chars=None, emit=emit, cancelled=lambda: False))
    assert 'ATT-01-P7' in result[0]['provided_source_ids']
    assert provider.requests[-1].work_dir.name == 'component-C001-expand-1-repair'
    assert json.loads(provider.requests[-1].user_message)['target_component']['id'] == 'C001'


def test_document_order_and_fixed_number_are_preserved(tmp_path, review_input):
    context = context_for(review_input)
    context['component_comparisons'] = [dict(comparison(context['components'][0]), provided_source_ids=['ATT-01-P0', 'ATT-02-P0'])]
    prior = {'items': [{'citation_number': 1, 'attachment_sha256': review_input[1]['ATT-01'].sha256, 'document_number': 'US-UNCHANGED'}]}
    _, plan = asyncio.run(generation.plan_report(ScriptedProvider([selected_decision()]), ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='', user_message=''),
        context, review.corpus(review_input[1], review_input[2]), review_input[1], prior, {'attempts': []},
        token_budget=None, max_chars=None, emit=emit, cancelled=lambda: False))
    assert plan['documents'][0]['document_number'] == 'US-UNCHANGED'
    assert [d['attachment'] for d in plan['documents']] == ['ATT-01', 'ATT-02']


def test_filtered_candidates_do_not_make_nonadjacent_sentences_contiguous(review_input):
    context = context_for(review_input)
    comp = comparison(context['components'][0])
    value = decision()
    value['evidence'][0]['sentence_ids'] = ['S001-T1', 'S001-T9']
    source = {'source_id': 'S001', 'attachment': 'ATT-01', 'pdf_page': 1,
              'sentences': [{'id': 'S001-T1', 'text': 'First.'}, {'id': 'S001-T9', 'text': 'Last.'}],
              'sentence_order': {'S001-T1': 0, 'S001-T9': 8}}
    with pytest.raises(ValueError, match='떨어진'):
        generation.validate_decision(value, context['components'][0], comp, context, [source])


def test_retrieved_sentence_namespace_is_preserved(review_input):
    context = context_for(review_input)
    comp = comparison(context['components'][0])
    for a in comp['documents'][0]['aspects']:
        a['sentence_ids'] = ['S001-T1']
    value = decision()
    value['evidence'][0]['sentence_ids'] = ['S001-T1']
    source = {'source_id': 'S001', 'attachment': 'ATT-01', 'pdf_page': 4,
              'sentences': [{'id': 'S001-T1', 'text': 'The controller changes gain.'}]}
    generation.validate_comparison(comp, context['components'][0], context['documents'], [source])
    result = generation.validate_decision(value, context['components'][0], comp, context, [source])
    assert result['evidence'][0]['sentence_ids'] == ['S001-T1']


def test_source_splitting_preserves_exact_selected_sentences_and_links():
    source = {'source_id': 'S001', 'attachment': 'ATT-01', 'pdf_page': 15,
              'sentences': [{'id': 'S001-T1', 'text': 'Mapping.'}, {'id': 'S001-T9', 'text': 'MLP.'}],
              'sentence_order': {'S001-T1': 0, 'S001-T9': 8}}
    value = decision()
    value['evidence'][0]['sentence_ids'] = ['S001-T9', 'S001-T1']
    normalized = generation.split_evidence(value, [source])
    assert [e['sentence_ids'] for e in normalized['evidence']] == [['S001-T1'], ['S001-T9']]
    assert '{{E1}}, {{E2}}' in normalized['component']['reasoning']
    assert normalized['component']['similarity'] == value['component']['similarity']
    assert normalized['component']['difference'] == value['component']['difference']
    value['component']['reasoning'] = '잘못된 참조 {{E1}}'
    assert generation.split_evidence(value, [source]) == value
