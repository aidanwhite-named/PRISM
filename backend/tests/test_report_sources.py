import re

from app import report_sources
from app.enums import AttachmentRole
from app.ingestion.service import ingest_one, IngestionLimits
from app.prompt_assembly import assemble
from app.retrieval.source_pool import SourcePool


def test_index_keeps_full_text_and_exact_noncontiguous_choices(tmp_path):
    body = ('--- PAGE 3 ---\n[0030] The stream is split into frames. '
            '[0031] Overlap is half. A window is applied. '
            '[0032] FFT transforms the signal.\n')
    item = ingest_one('ref.txt', body.encode(), tmp_path, True, IngestionLimits(), role=AttachmentRole.CITATION)
    prompt = assemble('본문', [item], '', False, None)
    assert report_sources.render_full_text('ATT-01', body) in prompt.user_message
    rows = report_sources.full_text_units('ATT-01', body)
    assert len(rows) == 3
    assert [r['pdf_page'] for r in rows] == [3, 3, 3]
    assert 'Overlap is half.' not in rows[0]['text']
    assert 'FFT' not in rows[1]['text']
    sentences = report_sources.sentences('ATT-01-P3', 'ATT-01', 3, report_sources.page_texts(body)[0][1])
    assert all(r['id'] in prompt.user_message and r['text'] in prompt.user_message for r in sentences)
    assert ''.join(r['text'] for r in sentences).strip() == report_sources.page_texts(body)[0][1].strip()
    assert ''.join(r['text'] for r in rows).strip() == report_sources.page_texts(body)[0][1].strip()


def test_long_paragraph_and_unicode_choices_are_exact_slices():
    body = '[0001] ' + '제어기는 입력을 분석합니다. 조건을 보존합니다. ' * 100
    rows = report_sources.units('S001', 'ATT-01', 2, body)
    assert len(rows) > 1
    assert ''.join(r['text'] for r in rows) == body
    assert all(body[r['start']:r['end']] == r['text'] for r in rows)


def test_retrieval_indexes_do_not_replace_source_or_change_existing_offsets():
    body = 'First sentence describes input. Second sentence describes output.'
    pool = SourcePool([('ATT-01', 8, body)])
    rendered = '\n'.join(pool.render())
    assert report_sources.render_sentences('S001', 'ATT-01', 8, body) in rendered
    assert 'S001-T1' in rendered and 'S001-T2' in rendered
    ref = pool.reference('ATT-01', 8, body)
    assert body[ref['start']:ref['end']] == body


def test_application_text_does_not_offer_citation_choices(tmp_path):
    item = ingest_one('app.txt', b'Application source text.', tmp_path, True, IngestionLimits(),
                      role=AttachmentRole.APPLICATION)
    prompt = assemble('본문', [item], '', False, None)
    assert not re.search(r'ATT-\d+-P\d+-U\d+', prompt.user_message.split('[ATTACHMENTS')[1])
    assert not re.search(r'ATT-\d+-P\d+-T\d+', prompt.user_message.split('[ATTACHMENTS')[1])


def test_index_has_no_truncated_preview_to_copy_as_quote():
    body = 'Using a secret random number generator, the transmitter generates frame keys. The receiver decrypts them.'
    index = report_sources.render_index(report_sources.units('S001', 'ATT-01', 3, body))
    assert 'S001-U1' in index and 'S001-U2' in index
    assert 'Using a secret' not in index
    assert '…' not in index and '...' not in index
