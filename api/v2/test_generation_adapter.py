"""Validate generated candidates; never accepts actual execution fields."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from api.errors import APIError
from api.v2.storage import resource
from api.v2.testing import CaseCreate, create_case, lock_project, update_spec, validate_case

_SCHEMA = json.loads((Path(__file__).resolve().parents[2] / 'docs/schemas/test-generation.schema.json').read_text())
_VALIDATOR = Draft202012Validator(_SCHEMA)


def ingest_test_generation(db, p, t, output):
    lock_project(db, p)
    spec = resource(db, p, t, 'test_specification')
    run = resource(db, p, spec.data['verification_run_id'], 'verification_run')
    if run.data['status'] != 'completed':
        raise APIError(409, 'VERIFICATION_NOT_COMPLETED', '코드 판정 완료 후 모델 시험을 채택하세요')
    if list(_VALIDATOR.iter_errors(output)) or output['verification_run_id'] != run.id:
        raise APIError(422, 'MODEL_OUTPUT_INVALID', '시험 생성 계약/실행이 유효하지 않습니다')
    candidates = output['test_candidates']
    if not candidates or len({c['candidate_key'] for c in candidates}) != len(candidates):
        raise APIError(422, 'MODEL_OUTPUT_INVALID', '빈 후보 또는 중복 후보 키입니다')
    planned = []
    try:
        for c in candidates:
            payload = CaseCreate.model_validate({k: v for k, v in c.items() if k not in ('candidate_key', 'prediction', 'assumptions', 'warnings')} | {'test_specification_id': t}).model_dump(mode='json')
            validate_case(db, p, spec.data, payload)
            if c['prediction']['verification_run_id'] != run.id:
                raise ValueError
            allowed_evidence = {e['chunk_id'] for link in payload['requirement_links'] for e in run.data['results'].get(link['requirement_id'], {}).get('evidence', []) if e['scope'] == 'this_screen'}
            if not set(c['prediction']['primary_chunk_ids']) <= allowed_evidence:
                raise ValueError
            if c['prediction']['status'] in ('normal_expected', 'failure_expected') and not c['prediction']['primary_chunk_ids']:
                raise ValueError
            for chunk_id in c['prediction']['primary_chunk_ids']:
                chunk = resource(db, p, chunk_id, 'chunk')
                if chunk.data['snapshot_id'] != run.data['snapshot_id']:
                    raise ValueError
            planned.append((payload, c['prediction'], c['assumptions'], c['warnings']))
    except (ValidationError, APIError, ValueError):
        raise APIError(422, 'MODEL_OUTPUT_INVALID', '시험 단계·참조·예측 근거가 유효하지 않습니다') from None
    versions, ids = dict(spec.data['case_versions']), []
    for payload, prediction, assumptions, warnings in planned:
        case = create_case(db, p, spec, {**payload, 'assumptions': assumptions, 'warnings': warnings}, 'generated', prediction)
        versions[case.id] = 1
        ids.append(case.id)
    # A new generation appends candidates and never overwrites manual edits/executions.
    update_spec(db, spec, versions, '모델 시험 후보 추가')
    return ids
