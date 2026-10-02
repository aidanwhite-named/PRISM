"""The gap-only policy must survive every report-writing/review entry point."""
import pytest

from app import analysis_protocol, comparison_review, report_consistency, structured_report
from app.reference_selection import INSTRUCTIONS
from app.task_instructions import ANALYSIS


@pytest.mark.parametrize('prompt', [
    ANALYSIS.body,
    analysis_protocol.apply(ANALYSIS.body),
    structured_report.instructions(),
    structured_report.instructions(retrieved=True),
    comparison_review.SEMANTIC,
    comparison_review.CORRECTION,
    report_consistency.SYSTEM,
], ids=['builtin', 'assembled', 'full-text', 'retrieved', 'semantic', 'correction', 'link-repair'])
def test_gap_only_selection_is_present_in_all_generation_and_review_paths(prompt):
    assert INSTRUCTIONS in prompt


def test_semantic_review_checks_secondary_references_even_when_excerpts_validate():
    assert '불필요한 support/contrast 문헌과 보완 부분의 연결 설명 누락은 findings' in comparison_review.SEMANTIC


def test_claim_first_reasoning_is_written_and_independently_reviewed():
    assert '청구항 기준의 대응 이유 작성:' in INSTRUCTIONS
    assert '그 순서에 따라 인용발명 1의 실제 기재가 각각 대응하는지 설명' in INSTRUCTIONS
    assert '청구항에 없는 한정을 추가하지 않습니다' in INSTRUCTIONS
    assert '청구항 구성·한정을 먼저 제시하고 각 요구 사항' in comparison_review.SEMANTIC
    assert '서술 순서의 교정만으로 근거·점수·기술적 판단을 바꾸지 않습니다' in comparison_review.SEMANTIC
    assert '"reasoning":"청구항 구성 (A)의 첫 필수 한정은' in structured_report.INSTRUCTIONS
