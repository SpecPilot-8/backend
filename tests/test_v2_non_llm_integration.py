"""Real HTTP contracts and storage, without mocking AI success or calling a provider."""
from io import BytesIO
from uuid import uuid4
from openpyxl import load_workbook


def test_manual_review_execution_and_excel_flow(client, v2_project, v2_document, v2_snapshot):
    base = f'/api/v2/projects/{v2_project}'
    block = client.get(base + f'/documents/{v2_document}/blocks').json()['items'][0]
    group = client.post(base + f'/documents/{v2_document}/requirement-groups', json={
        'document_revision':1, 'topic_title':'공지사항', 'subtopic_title':'공지사항 작성'}).json()
    req = client.post(base + '/requirements', json={
        'document_id':v2_document,'document_revision':1,'subtopic_id':group['subtopic_id'],
        'title':'제목 제한', 'author_reason':'통합시험 입력', 'original_text':block['text'],
        'source_refs':[{'block_id':block['id'],'quote':block['text']}],
        'conditions':[{'stable_key':'title-limit','text':'제목 최대 50자','category':'validation',
                       'target':'unknown','verifiable':'code'}]}).json()
    approved = client.put(base + f'/requirements/{req["id"]}/review',json={'base_revision':1,'status':'approved'}).json()
    job = client.post(base + '/verification-runs',json={'snapshot_id':v2_snapshot,
        'requirement_versions':[{'requirement_id':req['id'],'revision':approved['revision']}], 'model_config_version':0}).json()
    assert job['status'] == 'blocked'
    run = client.get(base + '/verification-runs').json()['items'][-1]
    detail = client.get(base + f'/verification-runs/{run["id"]}/requirements/{req["id"]}').json()
    assert detail['status'] is None and detail['evidence'] == []
    spec = client.post(base + '/test-specifications',json={'verification_run_id':run['id'],'requirement_ids':[req['id']]}).json()
    assert spec['requirement_versions'] == [{'requirement_id':req['id'],'revision':2}]
    links = [{'requirement_id':req['id'],'requirement_revision':2,'condition_ids':[approved['conditions'][0]['id']]}]
    case = client.post(base+'/test-cases',json={'test_specification_id':spec['id'], 'export_section_key':'notice',
        'subtopic_id':group['subtopic_id'],'title':'경계 시험','type':'boundary','requirement_links':links,
        'steps':[{'step_key':f'step-{i}','order':i,'action':f'단계 {i}','expected':f'기대 {i}',
                  'test_data':{'input':str(i)},'requirement_links':links} for i in [1,2]]}).json()
    current = client.get(base+f'/test-specifications/{spec["id"]}').json()
    payload = {'base_specification_revision':current['revision'],'client_save_id':str(uuid4()),
        'changes':[{'test_case_id':case['id'],'base_test_case_revision':1,'execution':{
            'operation':'create','bind_to':'after_save','step_results':[
                {'step_key':'step-1','status':'passed','actual':'통합시험 기록','performed_at':'2026-10-25T00:00:00Z'},
                {'step_key':'step-2','status':'on_hold','notes':'통합시험 보류 기록'}]}}]}
    saved = client.post(base+f'/test-specifications/{spec["id"]}/edit-batches',json=payload)
    assert saved.status_code == 200, saved.text
    assert client.post(base+f'/test-specifications/{spec["id"]}/edit-batches',json=payload).json() == saved.json()
    summary = client.get(base+f'/test-specifications/{spec["id"]}/execution-summary').json()
    assert summary['step_summary']['passed'] == summary['step_summary']['on_hold'] == 1
    exported = client.post(base+'/exports/test-specification',json={'test_specification_id':spec['id'],
        'revision':current['revision'],'execution_selection':'latest_per_case','template_id':'thinktree-test-spec-v1','format':'xlsx'}).json()
    download = client.get(base+f'/artifacts/{exported["result_ref"]["id"]}/download')
    assert download.status_code == 200
    sheet = load_workbook(BytesIO(download.content))['04. 공지사항']
    assert (sheet['J7'].value,sheet['J8'].value) == ('O','보류')
    changed = client.patch(base+f'/test-cases/{case["id"]}/procedure',json={'base_revision':1,'title':'수정 절차'})
    assert changed.status_code == 200
    assert client.post(base+f'/test-specifications/{spec["id"]}/edit-batches',json={**payload,'client_save_id':str(uuid4())}).status_code == 409
    after = client.get(base+f'/test-specifications/{spec["id"]}/execution-summary').json()
    assert after['step_summary']['not_run'] == 2
    history = client.get(base+f'/test-cases/{case["id"]}/executions').json()['items']
    assert len(history) == 1 and history[0]['test_case_revision'] == 1
