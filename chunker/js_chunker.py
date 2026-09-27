"""JS/JSX 소스를 tree-sitter로 파싱해 함수/컴포넌트 단위 chunk로 쪼갠다.

React 함수형 컴포넌트도 결국 "이름 붙은 함수/화살표함수"라 별도 특수 케이스
없이 동일하게 처리된다. 인라인 콜백(예: arr.map(x => ...))처럼 이름이 없는
함수는 대조 단위로서 의미가 없어 청크로 만들지 않는다.

중첩 함수는 청크로 따로 만들지 않고 바깥 함수 청크에 포함시킨다.
원칙1에서 LLM은 후보 청크 중 하나를 고르는데, 부모와 자식이 둘 다 후보로
올라오면 같은 코드가 두 번 등장해 선택이 모호해지기 때문이다.
(예전엔 LoginPage(9-132)와 그 안의 handleSubmit(26-46)이 둘 다 청크였다.)
"""

import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser

from chunker.models import Chunk

_LANGUAGE = Language(tsjs.language())
_PARSER = Parser(_LANGUAGE)

_FUNCTION_VALUE_TYPES = ("arrow_function", "function_expression")


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def chunk_js_file(file_path: str, content: str) -> list[Chunk]:
    source = content.encode("utf-8")
    tree = _PARSER.parse(source)
    chunks: list[Chunk] = []

    def emit(node, name: str) -> None:
        # `export`까지 포함해야 청크만 보고도 공개 여부를 알 수 있다.
        if node.parent is not None and node.parent.type == "export_statement":
            node = node.parent
        chunks.append(Chunk(
            chunk_type="function",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            symbol_fqn=f"{file_path}::{name}",
            content=_node_text(node, source),
        ))

    def walk(node) -> None:
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                emit(node, _node_text(name_node, source))
                return

        elif node.type in ("lexical_declaration", "variable_declaration"):
            # `const Foo = () => {...}` — 선언문 전체를 청크로 잡아야 content에
            # 이름(`const Foo =`)이 남는다. 화살표함수 노드만 잘라내면 익명처럼 보인다.
            named = [
                c for c in node.children
                if c.type == "variable_declarator"
                and (v := c.child_by_field_name("value")) is not None
                and v.type in _FUNCTION_VALUE_TYPES
                and c.child_by_field_name("name") is not None
            ]
            if named:
                first_name = _node_text(named[0].child_by_field_name("name"), source)
                emit(node, first_name)
                return

        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                emit(node, _node_text(name_node, source))
                return

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return chunks
