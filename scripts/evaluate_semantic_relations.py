"""Offline, fixture-specific structural evaluation. Never imported by runtime.

This is a hand-authored oracle for two named catalog clauses, not a Korean
interpreter. It checks preserved fields and Boolean relations independently of
runtime acceptance. PASS covers only the enumerated facts, not overall meaning,
source grounding, official authority, or executable treatment of overlapping tiers.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE_RULES = {
    'card_contract_threshold': 'INST-KR-000010-1-C47962C303F-PREF-02',
    'salary_or_merchant': 'INST-KR-000010-1-C9788B1329E-PREF-06',
}
SALARY_ATOMS = ('salary_ratio', 'salary_amount', 'salary_window', 'merchant_ratio', 'merchant_network')
CARD_ATOMS = ('term_12', 'amount_5000', 'term_24', 'amount_10000', 'term_36', 'amount_15000', 'card_window')


def number(node):
    value = node.get('expected')
    if type(value) in (int, float):
        return value
    match = re.match(r'^\s*(\d[\d,]*(?:\.\d+)?)', node.get('expected_literal') or '')
    return float(match[1].replace(',', '')) if match else None


def project(node, case, issues, seen, path='expression'):
    """Map only explicitly structured fixture facts; quotes alone are not extraction."""
    op = node.get('op')
    if op in ('ALL', 'ANY', 'NOT'):
        children = node.get('children') or []
        if not children or (op == 'NOT' and len(children) != 1):
            issues.append(path + ': invalid logical arity')
            return ('UNKNOWN',)
        return (op, *(project(c, case, issues, seen, f'{path}.{i}') for i, c in enumerate(children)))
    if op != 'PREDICATE':
        issues.append(path + ': unsupported or UNKNOWN leaf')
        return ('UNKNOWN',)
    atoms = []
    kind, metric = node.get('kind'), node.get('metric')
    period, scope = node.get('period') or {}, node.get('scope') or {}
    value, unit = number(node), node.get('expected_unit')
    if case == 'salary_or_merchant':
        if kind in ('SALARY_DEPOSIT', 'MERCHANT_SETTLEMENT'):
            prefix = 'salary' if kind == 'SALARY_DEPOSIT' else 'merchant'
            if period.get('term_ratio') == '1/2' and node.get('comparator') == 'GTE':
                atoms.append(prefix + '_ratio')
        if kind == 'SALARY_DEPOSIT':
            if metric == 'AMOUNT' and node.get('comparator') == 'GTE' and (
                (value == 50 and unit == 'MAN_WON') or (value == 500000 and unit == 'KRW')
            ):
                atoms.append('salary_amount')
            if (period.get('basis') == 'SALARY_DESIGNATED_DATE'
                    and period.get('before_offset') == 5 and period.get('after_offset') == 10):
                atoms.append('salary_window')
                if period.get('unit') != 'BUSINESS_DAY':
                    issues.append(path + ': salary window missing BUSINESS_DAY unit')
        if kind == 'MERCHANT_SETTLEMENT' and metric == 'COUNT' and value == 1 and node.get('comparator') == 'GTE':
            if set(scope.get('networks') or []) == {'비씨', '신한', '삼성카드사'}:
                atoms.append('merchant_network')
            else:
                issues.append(path + ': merchant network list missing or different')
    else:
        if kind == 'OTHER' and metric == 'PERIOD' and unit == 'MONTH':
            for months, comparator in ((12, 'EQ'), (24, 'LTE'), (36, 'LTE')):
                if value == months and node.get('comparator') == comparator:
                    atoms.append(f'term_{months}')
        if kind == 'CARD_PAYMENT' and metric == 'AMOUNT' and node.get('comparator') == 'GTE':
            for amount in (5000, 10000, 15000):
                if (value == amount and unit == 'CHEON_WON') or (value == amount * 1000 and unit == 'KRW'):
                    atoms.append(f'amount_{amount}')
            if (period.get('start_quote') in ('이 예금 가입월', '가입월', '이 예금 가입월로부터', '가입월로부터')
                    and period.get('end_quote') in ('만기일 전전월말일까지', '만기일 전전월말')):
                atoms.append('card_window')
            if scope.get('institution') != 'THIS_INSTITUTION':
                issues.append(path + ': THIS_INSTITUTION scope missing')
    if not atoms:
        issues.append(path + ': leaf has no mapped structured fixture fact')
        return ('UNKNOWN',)
    seen.update(atoms)
    return ('ALL', *(('ATOM', atom) for atom in atoms))


def evaluate(tree, facts):
    op, *args = tree
    if op == 'UNKNOWN':
        return None
    if op == 'ATOM':
        return facts[args[0]]
    values = [evaluate(child, facts) for child in args]
    if op == 'NOT':
        return None if values[0] is None else not values[0]
    if op == 'ALL':
        return False if False in values else None if None in values else True
    if op == 'ANY':
        return True if True in values else None if None in values else False
    raise ValueError(op)


def expected(case, facts):
    if case == 'salary_or_merchant':
        return ((facts['salary_ratio'] and facts['salary_amount'] and facts['salary_window'])
                or (facts['merchant_ratio'] and facts['merchant_network']))
    return facts['card_window'] and any(facts[f'term_{m}'] and facts[f'amount_{a}']
                                        for m, a in ((12, 5000), (24, 10000), (36, 15000)))


def evaluate_case(expression, case):
    issues, seen = [], set()
    tree = project(expression or {'op': 'UNKNOWN'}, case, issues, seen)
    atoms = SALARY_ATOMS if case == 'salary_or_merchant' else CARD_ATOMS
    mismatches, undecided = [], 0
    for values in itertools.product((False, True), repeat=len(atoms)):
        facts = dict(zip(atoms, values))
        actual, wanted = evaluate(tree, facts), expected(case, facts)
        if actual is None:
            undecided += 1
        elif actual != wanted:
            mismatches.append({'facts': facts, 'expected': wanted, 'actual': actual})
    missing = sorted(set(atoms) - seen)
    return {
        'case_id': case,
        'status': 'PASS_ENUMERATED_CHECKS' if not (issues or missing or mismatches or undecided) else 'FAIL_OR_INCOMPLETE',
        'missing_structured_facts': missing, 'structural_issues': issues,
        'truth_table_rows': 2 ** len(atoms), 'undecided_rows': undecided,
        'logic_mismatch_count': len(mismatches), 'counterexamples': mismatches[:5],
        'projection': tree,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ROOT / 'reports/semantic-prompt-experiment-20260907')
    parser.add_argument('--cases-dir', type=Path, help='Original run directories when replay folders omit cases.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/semantic-validation-followup-20260907/relationship-evaluation.json')
    args = parser.parse_args()
    results = []
    for run in sorted(args.input_dir.iterdir()):
        if not run.is_dir():
            continue
        for stage in ('parsed', 'accepted'):
            source = run / f'{stage}.json'
            if not source.exists():
                continue
            clauses = json.loads(source.read_text()).get('clauses', [])
            # Only evaluate cases actually sent in this run, including rejected clauses.
            case_path = run / 'cases.json'
            if not case_path.exists() and args.cases_dir:
                case_path = args.cases_dir / run.name / 'cases.json'
            selected = json.loads(case_path.read_text())
            for case in selected:
                name = case['case_id']
                if name not in CASE_RULES:
                    continue
                row = next((c for c in clauses if CASE_RULES[name] in c.get('clause_id', '')), None)
                result = evaluate_case(row.get('expression') if row else None, name)
                results.append({'run': run.name, 'stage': stage, 'clause_present': row is not None, **result})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'scope': __doc__, 'results': results}, ensure_ascii=False, indent=2) + '\n')
    for row in results:
        print(row['run'], row['stage'], row['case_id'], row['status'],
              'missing=', len(row['missing_structured_facts']), 'logic_errors=', row['logic_mismatch_count'])


if __name__ == '__main__':
    main()
