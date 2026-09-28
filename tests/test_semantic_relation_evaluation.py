"""Checks for the offline, fixture-specific semantic evaluation oracle."""
import copy
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('relation_eval', Path(__file__).resolve().parents[1] / 'scripts/evaluate_semantic_relations.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
evaluate_case = MODULE.evaluate_case


def salary_tree():
    return {'op': 'ANY', 'children': [
        {'op': 'PREDICATE', 'kind': 'SALARY_DEPOSIT', 'metric': 'AMOUNT',
         'comparator': 'GTE', 'expected': 50, 'expected_unit': 'MAN_WON',
         'period': {'term_ratio': '1/2', 'basis': 'SALARY_DESIGNATED_DATE',
                    'before_offset': 5, 'after_offset': 10, 'unit': 'BUSINESS_DAY'}},
        {'op': 'PREDICATE', 'kind': 'MERCHANT_SETTLEMENT', 'metric': 'COUNT',
         'comparator': 'GTE', 'expected': 1, 'period': {'term_ratio': '1/2'},
         'scope': {'networks': ['비씨', '신한', '삼성카드사']}},
    ]}


def card_tree():
    return {'op': 'ANY', 'children': [
        {'op': 'ALL', 'children': [
            {'op': 'PREDICATE', 'kind': 'OTHER', 'metric': 'PERIOD',
             'expected': months, 'expected_unit': 'MONTH', 'comparator': comparator},
            {'op': 'PREDICATE', 'kind': 'CARD_PAYMENT', 'metric': 'AMOUNT',
             'expected_literal': amount, 'expected_unit': 'CHEON_WON', 'comparator': 'GTE',
             'scope': {'institution': 'THIS_INSTITUTION'},
             'period': {'start_quote': '이 예금 가입월', 'end_quote': '만기일 전전월말일까지'}},
        ]} for months, comparator, amount in ((12, 'EQ', '5,000천원'), (24, 'LTE', '10,000천원'), (36, 'LTE', '15,000천원'))
    ]}


def test_salary_combined_leaf_and_split_all_have_identical_required_logic():
    combined = salary_tree()
    assert evaluate_case(combined, 'salary_or_merchant')['status'] == 'PASS_ENUMERATED_CHECKS'
    split = copy.deepcopy(combined)
    amount = split['children'][0]
    del amount['period']['term_ratio']
    ratio = {'op': 'PREDICATE', 'kind': 'SALARY_DEPOSIT', 'metric': 'RATIO',
             'comparator': 'GTE', 'period': {'term_ratio': '1/2'}}
    split['children'][0] = {'op': 'ALL', 'children': [ratio, amount]}
    assert evaluate_case(split, 'salary_or_merchant')['status'] == 'PASS_ENUMERATED_CHECKS'
    # Same extracted atoms, wrong relation: salary ratio alone must not qualify.
    split['children'][0]['op'] = 'ANY'
    result = evaluate_case(split, 'salary_or_merchant')
    assert not result['missing_structured_facts']
    assert result['logic_mismatch_count'] > 0


def test_missing_business_day_unit_is_not_full_extraction_success():
    tree = salary_tree()
    del tree['children'][0]['period']['unit']
    result = evaluate_case(tree, 'salary_or_merchant')
    assert result['logic_mismatch_count'] == 0
    assert result['status'] == 'FAIL_OR_INCOMPLETE'
    assert any('BUSINESS_DAY' in issue for issue in result['structural_issues'])


def test_merchant_ratio_cannot_disappear_even_if_other_path_has_it():
    tree = salary_tree()
    tree['children'][1].pop('period')
    result = evaluate_case(tree, 'salary_or_merchant')
    assert 'merchant_ratio' in result['missing_structured_facts']
    assert result['logic_mismatch_count'] > 0


def test_card_period_amount_pairing_and_window_are_required():
    tree = card_tree()
    assert evaluate_case(tree, 'card_contract_threshold')['status'] == 'PASS_ENUMERATED_CHECKS'
    amounts = [branch['children'][1] for branch in tree['children']]
    amounts[0]['expected_literal'], amounts[1]['expected_literal'] = amounts[1]['expected_literal'], amounts[0]['expected_literal']
    result = evaluate_case(tree, 'card_contract_threshold')
    assert not result['missing_structured_facts']
    assert result['logic_mismatch_count'] > 0


def test_unknown_does_not_pass_as_safe_and_quotes_do_not_replace_structure():
    for tree in ({'op': 'UNKNOWN'}, {'op': 'PREDICATE', 'kind': 'SALARY_DEPOSIT',
                                   'source_quote': '50만원, 지정일 전 5영업일 후 10영업일 가입기간 1/2'}):
        result = evaluate_case(tree, 'salary_or_merchant')
        assert result['status'] == 'FAIL_OR_INCOMPLETE'
        assert result['undecided_rows'] == 32
        assert result['missing_structured_facts']
