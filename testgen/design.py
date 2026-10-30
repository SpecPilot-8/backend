"""고정 조건에서 보수적인 시험 관점을 제안하고 실제 저장된 단계 연결을 집계한다.

계획은 규칙 기반 제안이며, 시험의 충분성이나 수행 성공을 증명하지 않는다.
이전 사양서의 생성 사유를 추정해서 채우지 않는다.
"""
from collections import Counter, defaultdict
import re

PLAN_VERSION = 'design-plan-v1'
PERSPECTIVES = {
    'display': ('화면 표시', '표시 조건이므로 명세에 적힌 요소·문구·형식을 확인하도록 제안합니다.'),
    'valid_input': ('허용 입력', '입력 검사 조건이므로 허용되는 입력의 처리 결과를 확인하도록 제안합니다.'),
    'invalid_input': ('입력 거부', '입력 검사 조건이므로 허용되지 않는 입력과 명세의 거부·안내 기준을 확인하도록 제안합니다.'),
    'boundary': ('경계값', '수치 제한이 명시되어 있어 경계 안쪽·정확값·바깥쪽을 비교하도록 제안합니다.'),
    'transition': ('동작 후 상태·이동', '이동·팝업 조건이므로 시작 상태와 동작 후 상태·목적지를 확인하도록 제안합니다.'),
    'business_rule': ('업무 규칙', '업무 조건과 그에 따른 결과의 관계를 확인하도록 제안합니다.'),
    'persistence': ('처리 결과·재확인', '데이터 처리 조건이므로 명세에 정의된 처리 결과와 반영 상태를 확인하도록 제안합니다.'),
    'error_feedback': ('오류 안내', '실패 상황과 그때 표시할 명세의 안내 문구를 함께 확인하도록 제안합니다.'),
    'runtime_check': ('실행 화면 확인', '코드만으로 확인하기 어려운 조건이므로 실행한 화면·동작을 직접 확인하도록 제안합니다.'),
    'permission': ('권한별 동작', '권한·역할에 관한 표현이 있어 명세에 정의된 허용·거부 대상을 구분하도록 제안합니다.'),
    'state_change': ('상태별 동작', '상태에 관한 표현이 있어 명세의 상태와 조건 조합별 결과를 확인하도록 제안합니다.'),
    'empty_state': ('데이터 없음', '목록·데이터가 없는 상황을 명시하여 해당 상태의 표시·동작을 확인하도록 제안합니다.'),
    'normal': ('기본 동작', '조건이 요구하는 동작과 결과를 직접 확인하도록 제안합니다.'),
}
_NUMERIC = re.compile(r'\d+\s*(?:자|글자|문자|바이트|bytes?(?![A-Za-z])|[KMGT]B(?![A-Za-z])|건|개|회|일|분)', re.I)


def perspectives(condition):
    if condition.get('verifiable') == 'not_statically_verifiable':
        return ['runtime_check']
    category, text = condition.get('category'), condition['text']
    primary = {'display': ['display'], 'validation': ['valid_input', 'invalid_input'],
               'navigation': ['transition'], 'business_logic': ['business_rule'],
               'data': ['persistence'], 'error_message': ['error_feedback']}
    selected = list(primary.get(category, ['normal']))
    if condition.get('constraint') or (_NUMERIC.search(text) and re.search(r'이하|이상|미만|초과|최대|최소|까지|제한|범위|길이|용량', text)):
        selected.append('boundary')
    if re.search(r'권한|관리자|역할|비로그인|미로그인|접근', text):
        selected.append('permission')
    if category == 'business_logic' and re.search(r'상태|활성|비활성|게시기간|세션|로그아웃|\b(?:ON|OFF)\b', text):
        selected.append('state_change')
    if re.search(r'목록|리스트|검색|데이터', text) and re.search(r'없|0건|빈', text):
        selected.append('empty_state')
    return list(dict.fromkeys(selected))


def build_plan(requirements):
    items = []
    for req in requirements:
        for condition in req['conditions']:
            for perspective in perspectives(condition):
                label, reason = PERSPECTIVES[perspective]
                items.append({'id': f"{condition['stable_key']}::{perspective}",
                              'requirement_id': req['id'], 'requirement_revision': req['revision'],
                              'requirement_title': req['title'], 'condition_id': condition['id'],
                              'condition_key': condition['stable_key'], 'condition_text': condition['text'],
                              'perspective': perspective, 'label': label, 'reason': reason})
    return {'version': PLAN_VERSION, 'origin': 'rule_suggestion', 'items': items,
            'summary': {'condition_count': sum(len(r['conditions']) for r in requirements),
                        'intent_count': len(items), 'perspectives': dict(Counter(i['label'] for i in items))},
            'limitations': ['조건의 분류와 명시된 표현에 따른 시험 관점 제안입니다. 충분성 검토가 필요합니다.',
                '명세에 없는 정책·오류 문구는 확정하지 않고 검토 필요로 표시합니다.']}


def validate_design(step, plan):
    """등록된 관점과 같은 개정의 조건만 연결한다. 사유의 의미적 정확성은 별도 검토 대상이다."""
    design = step.get('design')
    if not design:
        return
    intents = {item['id']: item for item in (plan or {}).get('items', [])}
    ids = design['intent_ids']
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= intents.keys():
        raise ValueError('unknown_or_duplicate_intent')
    planned = {(intents[i]['requirement_id'], intents[i]['requirement_revision'], intents[i]['condition_id']) for i in ids}
    linked = {(link['requirement_id'], link['requirement_revision'], cid)
              for link in step['requirement_links'] for cid in link['condition_ids']}
    if planned != linked:
        raise ValueError('intent_condition_mismatch')


def design_review(spec, cases):
    plan = spec.get('design_plan')
    condition_refs, intent_refs = defaultdict(list), defaultdict(list)
    conditions = []
    for req in spec['requirements']:
        for c in req['conditions']:
            conditions.append({'requirement_id': req['id'], 'requirement_revision': req['revision'],
                               'requirement_title': req['title'], 'condition_id': c['id'], 'condition_text': c['text']})
    known = {(c['requirement_id'], c['requirement_revision'], c['condition_id']) for c in conditions}
    for case in cases:
        metadata = case.get('design') or {}
        stale = bool(metadata) and metadata.get('generated_revision') != case['revision']
        for step in case['steps']:
            design = step.get('design') or {}
            ref = {'case_id': case['id'], 'test_no': case['test_no'], 'step_key': step['step_key'],
                   'step_order': step['order'], 'basis': design.get('basis', 'unrecorded'), 'stale': stale}
            for link in step['requirement_links']:
                for cid in link['condition_ids']:
                    key = (link['requirement_id'], link['requirement_revision'], cid)
                    if key in known:
                        condition_refs[key].append(ref)
            if plan and design:
                try:
                    validate_design(step, plan)
                except ValueError:
                    continue
                for intent in design['intent_ids']:
                    intent_refs[intent].append(ref)
    items = []
    gaps = {gap['intent_id']: gap['reason'] for gap in spec.get('design_gaps', [])}
    for item in (plan or {}).get('items', []):
        refs = intent_refs[item['id']]
        status = ('written' if any(not r['stale'] and r['basis'] == 'specification' for r in refs)
                  else 'review_needed' if refs else 'missing')
        items.append({**item, 'status': status, 'test_refs': refs,
                      'gap_reason': gaps.get(item['id'], '이 관점에 연결된 시험 설계 기록이 없습니다.') if status == 'missing' else ''})
    for c in conditions:
        c['test_refs'] = condition_refs[(c['requirement_id'], c['requirement_revision'], c['condition_id'])]
    return {'plan_available': bool(plan), 'plan_version': plan.get('version') if plan else None,
            'specification_revision': spec['revision'], 'items': items, 'conditions': conditions,
            'summary': {'conditions_total': len(conditions), 'conditions_linked': sum(bool(c['test_refs']) for c in conditions),
                        'intents_total': len(items), **{f'intents_{s}': sum(i['status'] == s for i in items)
                                                       for s in ('written', 'review_needed', 'missing')}},
            'limitations': ['작성·연결 여부를 확인한 결과입니다. 실제 시험 통과나 시험의 충분성을 보장하지 않습니다.']}
