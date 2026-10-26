"""Validate teammate-1 output before allocating any persistent identifiers."""
from copy import deepcopy
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from api.errors import APIError

_SCHEMA = json.loads((Path(__file__).resolve().parents[2] / 'docs/schemas/requirement-extraction.schema.json').read_text(encoding='utf-8'))
_VALIDATOR = Draft202012Validator(_SCHEMA)


def validate_extraction(draft, artifact):
    def require(test):
        if not test:
            raise ValueError
    try:
        require(not list(_VALIDATOR.iter_errors(draft)))
        require((draft['document_id'], draft['document_revision']) == (artifact['document_id'], artifact['document_revision']))
        blocks = {b['id']: b for b in artifact['blocks']}
        require(len(blocks) == len(artifact['blocks']))
        screens = {s['screen_key']: s for s in draft['screens']}
        require(len(screens) == len(draft['screens']))
        topic_keys = [t['key'] for t in draft['topics']]
        require(len(topic_keys) == len(set(topic_keys)))
        subs = {}
        for t in draft['topics']:
            require(t['subtopics'])
            for s in t['subtopics']:
                require(s['key'] not in subs)
                subs[s['key']] = (t, s)
                require(set(s['screen_keys']) <= screens.keys())
            for group in [t] + t['subtopics']:
                if group['title_origin'] != 'unclassified':
                    require(group['source_refs'])
                if group['title_origin'] != 'extracted':
                    require(group['review_reasons'])
        rkeys = [r['stable_key'] for r in draft['requirements']]
        ckeys = [c['stable_key'] for r in draft['requirements'] for c in r['conditions']]
        require(len(rkeys) == len(set(rkeys)) and len(ckeys) == len(set(ckeys)))
        used = set()
        for r in draft['requirements']:
            t, sub = subs[r['subtopic_key']]
            used.add(r['subtopic_key'])
            require(r['source_refs'] and all(c['source_refs'] for c in r['conditions']))
            if r['screen_key'] is not None:
                require(r['screen_key'] in sub['screen_keys'])
            if t['title_origin'] != 'extracted' or sub['title_origin'] != 'extracted':
                require(r['review_reasons'])
            for m in r['source_markers']:
                b = blocks[m['block_id']]
                require((m['section'], m['label']) == (b['structure']['section'], b['structure']['label']))
            for c in r['conditions']:
                require(set(c['related_condition_keys']) <= set(ckeys) and c['stable_key'] not in c['related_condition_keys'])
        require(used == set(subs))
        for s in screens.values():
            seen = {s['screen_key']}
            parent = s['base_screen_key']
            while parent is not None:
                require(parent in screens and parent not in seen)
                seen.add(parent)
                parent = screens[parent]['base_screen_key']
        def walk(x):
            if isinstance(x, dict):
                for ref in x.get('source_refs', []):
                    require(ref['block_id'] in blocks and ref['quote'] in blocks[ref['block_id']]['text'])
                for k in ['block_ids', 'source_block_ids']:
                    if k in x:
                        require(set(x[k]) <= blocks.keys())
                for value in x.values():
                    walk(value)
            elif isinstance(x, list):
                for value in x:
                    walk(value)
        walk(draft)
        if not rkeys:
            require(not draft['topics'] and not draft['screens'] and any(w['code'] == 'NO_REQUIREMENTS' for w in draft['warnings']))
        normalized = deepcopy(draft)
        for requirement in normalized['requirements']:
            for condition in requirement['conditions']:
                condition.setdefault('constraint', None)
        return normalized
    except (ValueError, KeyError, TypeError):
        raise APIError(422, 'MODEL_OUTPUT_INVALID', '모델의 구조·출처·키·화면 관계를 확인하세요') from None
