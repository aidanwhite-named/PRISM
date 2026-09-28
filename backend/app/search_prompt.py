"""Validate user-written search strategies without prescribing a template."""
SEARCH_PROMPT_ID = 'search_prompt.md'


class SearchPromptError(Exception):
    """The selected strategy is missing or empty."""


def validate_strategy_body(body, *, prompt_id=SEARCH_PROMPT_ID):
    if not body.strip():
        raise SearchPromptError(f'{prompt_id} 의 검색 전략 본문이 비어 있습니다.')
