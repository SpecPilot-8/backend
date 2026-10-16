"""Internal teammate-2 boundary. Scopes/graph must come from the code parser, not an LLM."""
from copy import deepcopy
from typing import Literal
from pydantic import Field, ValidationError
from api.errors import APIError
from api.models import new_id
from api.v2.common import Input, job_out
from api.v2.storage import all_resources, replace, resource
from verdict.rules import aggregate

VerdictStatus = Literal['implemented', 'partial', 'mismatch', 'not_found', 'needs_review', 'not_statically_verifiable']


class ConditionVerdict(Input):
    condition_id: str
    status: VerdictStatus
    reason: str = Field(min_length=1, max_length=10000)
    primary_chunk_ids: list[str]
    reference_chunk_ids: list[str]
    message_match: Literal['matches', 'differs', 'not_applicable']
    server_validation: Literal['present', 'missing', 'not_applicable', 'unknown']
    spec_suspect: str


class Finding(Input):
    key: str
    title: str
    condition_ids: list[str] = Field(min_length=1)
    problem_chunk_ids: list[str]
    context_chunk_ids: list[str]
    problem_node_ids: list[str]
    suggested_code: str | None


class VerdictDraft(Input):
    contract_version: Literal['2.0']
    snapshot_id: str
    requirement_id: str
    requirement_revision: int = Field(ge=1)
    condition_verdicts: list[ConditionVerdict]
    findings: list[Finding]
    warnings: list[str]


def ingest_verdict(db, p, v, output, trusted_scopes, trusted_graph=None):
    try:
        draft = VerdictDraft.model_validate(output)
    except ValidationError:
        raise APIError(422, 'MODEL_OUTPUT_INVALID', '판정 출력 계약이 유효하지 않습니다') from None
    run = resource(db, p, v, 'verification_run')
    req = next((r for r in run.data['requirements'] if r['id'] == draft.requirement_id), None)
    graph = deepcopy(trusted_graph or {'nodes': [], 'edges': [], 'unresolved': []})
    # target은 원문 메타데이터다. 구현 계층과 무관하게 같은 스냅샷의 청크를 검증한다.
    chunks = {c.id: c for c in all_resources(db, p, 'chunk') if c.data['snapshot_id'] == run.data['snapshot_id']}
    def check(condition):
        if not condition:
            raise APIError(422, 'MODEL_OUTPUT_INVALID', '조건·청크·화면 범위·노드 관계가 유효하지 않습니다')
    check(req is not None and draft.snapshot_id == run.data['snapshot_id'])
    check(draft.requirement_revision == req['revision'])
    check(draft.requirement_id not in run.data['results'])
    check(set(trusted_scopes) <= chunks.keys())
    check(all(s in ('this_screen', 'other_screen', 'unreachable') for s in trusted_scopes.values()))
    conditions = {c['id']: c for c in req['conditions']}
    ids = [c.condition_id for c in draft.condition_verdicts]
    check(len(ids) == len(set(ids)) and set(ids) == conditions.keys())
    node_ids = {n['id'] for n in graph['nodes']}
    check(len(node_ids) == len(graph['nodes']))
    check(all(n.get('chunk_id') in chunks for n in graph['nodes']))
    check(all(e['from'] in node_ids and e['to'] in node_ids for e in graph['edges']))
    evidence, evidence_ids = {}, {}
    for c in draft.condition_verdicts:
        check(len(c.primary_chunk_ids) == len(set(c.primary_chunk_ids)))
        check(len(c.reference_chunk_ids) == len(set(c.reference_chunk_ids)))
        check(all(trusted_scopes.get(k) == 'this_screen' for k in c.primary_chunk_ids))
        check(all(trusted_scopes.get(k) == 'other_screen' for k in c.reference_chunk_ids))
        if conditions[c.condition_id]['verifiable'] == 'not_statically_verifiable':
            check(c.status == 'not_statically_verifiable')
        if c.status in ('implemented', 'partial', 'mismatch'):
            check(c.primary_chunk_ids)
        if not c.primary_chunk_ids and c.reference_chunk_ids:
            check(c.status in ('needs_review', 'not_statically_verifiable'))
        for k in c.primary_chunk_ids + c.reference_chunk_ids:
            if k not in evidence_ids:
                evidence_ids[k] = new_id()
                chunk = chunks[k].data
                evidence[k] = {'id': evidence_ids[k], 'chunk_id': k, 'file_path': chunk['file_path'],
                               'start_line': chunk['start_line'], 'end_line': chunk['end_line'], 'code_snippet': chunk['content'],
                               'role': 'context', 'scope': trusted_scopes[k], 'condition_ids': [],
                               'explanation': '', 'suggested_code': None}
            ev = evidence[k]
            if c.condition_id not in ev['condition_ids']:
                ev['condition_ids'].append(c.condition_id)
            if k in c.primary_chunk_ids:
                ev['role'] = 'problem' if c.status in ('partial', 'mismatch') else ev['role']
            ev['explanation'] += c.reason + '\n'
    statuses = [c.status for c in draft.condition_verdicts]
    findings = []
    check(len({f.key for f in draft.findings}) == len(draft.findings))
    for f in draft.findings:
        check(set(f.condition_ids) <= conditions.keys() and set(f.problem_node_ids) <= node_ids)
        check(all(trusted_scopes.get(k) == 'this_screen' for k in f.problem_chunk_ids))
        check(set(f.problem_chunk_ids + f.context_chunk_ids) <= evidence.keys())
        if f.suggested_code is not None:
            check(aggregate(statuses) in ('partial', 'mismatch'))
        findings.append({'id': new_id(), 'title': f.title, 'condition_ids': f.condition_ids,
                         'evidence_ids': [evidence_ids[k] for k in f.problem_chunk_ids + f.context_chunk_ids],
                         'problem_node_ids': f.problem_node_ids, 'suggested_code': f.suggested_code})
    for node in graph['nodes']:
        node['evidence_ids'] = [evidence_ids[node['chunk_id']]] if node['chunk_id'] in evidence_ids else []
    result = {'run_id': v, 'snapshot_id': draft.snapshot_id, 'requirement_id': draft.requirement_id,
              'requirement_revision': draft.requirement_revision, 'analysis_state': 'completed', 'status': aggregate(statuses),
              'reason': '\n'.join(c.reason for c in draft.condition_verdicts),
              'condition_verdicts': [{'condition_id': c.condition_id, 'status': c.status, 'reason': c.reason,
                  'evidence_ids': [evidence_ids[k] for k in c.primary_chunk_ids + c.reference_chunk_ids],
                  'message_match': c.message_match, 'server_validation': c.server_validation, 'spec_suspect': c.spec_suspect}
                  for c in draft.condition_verdicts], 'findings': findings, 'evidence': list(evidence.values()),
              'call_flow': graph, 'counts': {'findings': len(findings),
                  'affected_conditions': len({c for f in findings for c in f['condition_ids']}),
                  'evidence_locations': len(evidence), 'files': len({e['file_path'] for e in evidence.values()})}, 'warnings': draft.warnings}
    results = {**run.data['results'], draft.requirement_id: result}
    status = 'completed' if len(results) == len(run.data['requirements']) else 'running'
    replace(db, run, {**run.data, 'results': results, 'status': status, 'missing_features': []})
    if status == 'completed':
        job = resource(db, p, run.data['job_id'], 'job')
        data = {**job.data, 'status': status, 'missing_features': [], 'result_ref': {'kind': 'verification_run', 'id': v}}
        event_data = {**job_out(job), 'status': status, 'missing_features': [], 'result_ref': data['result_ref']}
        data['events'] = job.data['events'] + [{'id': len(job.data['events'])+1, 'event': status, 'data': event_data}]
        replace(db, job, data)
    return result
