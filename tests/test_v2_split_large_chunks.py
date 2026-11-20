from api.v2.code import TEXT_CHUNK_MAX_BYTES, chunk_file, split_large


def big_java_method(statements=400):
    body = ''.join(f'        if (value{i} > {i}) {{\n            total += compute{i}(value{i});\n        }}\n'
                   for i in range(statements))
    return ('package app;\n\npublic class Big {\n    public int run(int value) {\n        int total = 0;\n'
            + body + '        return total;\n    }\n\n    public int small() { return 1; }\n}\n')


def test_small_chunks_are_untouched():
    item = {'file_path': 'a.js', 'start_line': 3, 'end_line': 5, 'symbol': 'a.js::f',
            'content': 'function f() {\n  return 1;\n}\n', 'chunk_type': 'function'}
    assert split_large(item) == [item]


def test_large_method_is_split_on_statement_boundaries():
    text = big_java_method()
    chunks = chunk_file('src/app/Big.java', text)
    run = [c for c in chunks if c['symbol'] == 'app.Big.run']
    assert len(run) > 1 and all(len(c['content'].encode()) <= TEXT_CHUNK_MAX_BYTES for c in run)
    assert [c['part'] for c in run] == [{'index': i, 'total': len(run)} for i in range(1, len(run) + 1)]
    method = text[text.index('    public int run'):text.index('\n\n    public int small')] + '\n'
    assert ''.join(c['content'] for c in run).strip() == method.strip()
    lines = text.splitlines(keepends=True)
    for c in run:  # 줄 좌표가 원문과 맞는다 (첫 조각은 구조 파서처럼 선언 앞 들여쓰기를 뺀다)
        assert ''.join(lines[c['start_line'] - 1:c['end_line']]).strip() == c['content'].strip()
    for c in run[1:]:  # 분기 중간이 아니라 최상위 문장(if) 앞에서 잘린다
        assert c['content'].startswith('        if (') or c['content'].startswith('        return')
    for a, b in zip(run, run[1:]):
        assert b['start_line'] == a['end_line'] + 1
    assert any(c['symbol'] == 'app.Big.small' and 'part' not in c for c in chunks)
