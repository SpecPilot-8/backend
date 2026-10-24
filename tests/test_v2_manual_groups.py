def test_manual_group_reuse_revision_and_project_scope(client, v2_project, v2_document):
    url = f'/api/v2/projects/{v2_project}/documents/{v2_document}/requirement-groups'
    body = {'document_revision': 1, 'topic_title': ' 공지사항 ', 'subtopic_title': '공지사항 작성'}
    first = client.post(url, json=body)
    assert first.status_code == 201
    assert client.post(url, json=body).json() == first.json()
    groups = client.get(url).json()['topics']
    assert len(groups) == 1 and groups[0]['title'] == '공지사항'
    assert groups[0]['title_origin'] == 'manual'
    assert client.post(url, json={**body, 'document_revision':2}).status_code == 409
    assert client.post(url, json={**body, 'topic_title':'  '}).status_code == 422
    other = client.post('/api/v2/projects', json={'name':'Other'}).json()['id']
    assert client.post(url.replace(v2_project, other), json=body).status_code == 404
