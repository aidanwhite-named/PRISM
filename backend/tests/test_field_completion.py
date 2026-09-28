import json
import pytest
from app.patent_search import literature_parser as lp

@pytest.mark.parametrize('parts,expected', [([2020], '2020'), ([2020, 3], '2020-03'), ([2020, 3, 2], '2020-03-02'), ([2020, 2, 31], '')])
def test_crossref_computed_metadata_reextracts_from_original_bytes(parts, expected):
    body = json.dumps({'message': {'DOI': '10.1234/a', 'author': [{'given': 'Ada', 'family': 'Lovelace'}, {'name': 'Research Group'}],
                                    'issued': {'date-parts': [parts]}}}).encode()
    work = lp.read_crossref_work(body)
    assert work.publication_date == expected
    assert lp._extract(body, work.paths['authors']) == work.authors == 'Ada Lovelace; Research Group'
    if expected:
        assert lp._extract(body, work.paths['publication_date']) == expected
    else:
        assert 'publication_date' not in work.paths
