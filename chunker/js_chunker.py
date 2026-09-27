"""JS/JSX 소스를 tree-sitter로 파싱해 함수/컴포넌트 단위 chunk로 쪼갠다.

React 함수형 컴포넌트도 결국 "PascalCase 이름의 함수/화살표함수"라
별도 특수 케이스 없이 동일하게 처리된다. 인라인 콜백(예: arr.map(x => ...))
처럼 이름이 없는 함수는 대조 단위로서 의미가 없어 청크로 만들지 않는다.
"""

import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser

from chunker.models import Chunk

_LANGUAGE = Language(tsjs.language())
_PARSER = Parser(_LANGUAGE)

_NAMED_FUNCTION_VALUE_TYPES = ("arrow_function", "function_expression")


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def chunk_js_file(file_path: str, content: str) -> list[Chunk]:
    source = content.encode("utf-8")
    tree = _PARSER.parse(source)
    chunks: list[Chunk] = []

    def walk(node):
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                chunks.append(_make_chunk(file_path, node, source, _node_text(name_node, source)))

        elif node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            name_node = node.child_by_field_name("name")
            if value is not None and value.type in _NAMED_FUNCTION_VALUE_TYPES and name_node:
                chunks.append(_make_chunk(file_path, value, source, _node_text(name_node, source)))

        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                chunks.append(_make_chunk(file_path, node, source, _node_text(name_node, source)))

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return chunks


def _make_chunk(file_path: str, node, source: bytes, name: str) -> Chunk:
    return Chunk(
        chunk_type="function",
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        symbol_fqn=f"{file_path}::{name}",
        content=_node_text(node, source),
    )
