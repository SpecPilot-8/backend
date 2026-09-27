"""Java 소스를 tree-sitter로 파싱해 필드/메서드 단위 chunk로 쪼갠다.

필드까지 포함하는 이유: "제목 50자" 같은 제약이 메서드가 아니라
DTO 필드의 @Size 어노테이션에 있는 경우가 실제로 있다 (CLAUDE.md 8.1).
"""

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from chunker.models import Chunk

_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(_LANGUAGE)


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def _package_name(root, source: bytes) -> str | None:
    for child in root.children:
        if child.type == "package_declaration":
            for c in child.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return _node_text(c, source)
    return None


def chunk_java_file(file_path: str, content: str) -> list[Chunk]:
    source = content.encode("utf-8")
    tree = _PARSER.parse(source)
    package = _package_name(tree.root_node, source)

    chunks: list[Chunk] = []

    def visit_class(class_node, outer_fqn: str):
        name_node = class_node.child_by_field_name("name")
        class_name = _node_text(name_node, source) if name_node else "Anonymous"
        class_fqn = f"{outer_fqn}.{class_name}" if outer_fqn else class_name

        body = class_node.child_by_field_name("body")
        if body is None:
            return
        for member in body.children:
            if member.type in ("method_declaration", "constructor_declaration"):
                mname_node = member.child_by_field_name("name")
                mname = _node_text(mname_node, source) if mname_node else "<init>"
                chunks.append(_make_chunk(
                    "method", file_path, member, source, f"{class_fqn}.{mname}"
                ))
            elif member.type == "field_declaration":
                for declarator in member.children:
                    if declarator.type == "variable_declarator":
                        fname_node = declarator.child_by_field_name("name")
                        fname = _node_text(fname_node, source) if fname_node else "?"
                        chunks.append(_make_chunk(
                            "field", file_path, member, source, f"{class_fqn}#{fname}"
                        ))
            elif member.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                visit_class(member, class_fqn)

    def walk_top_level(node):
        for child in node.children:
            if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                visit_class(child, package or "")
            else:
                walk_top_level(child)

    walk_top_level(tree.root_node)
    return chunks


def _make_chunk(chunk_type: str, file_path: str, node, source: bytes, symbol_fqn: str) -> Chunk:
    return Chunk(
        chunk_type=chunk_type,
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        symbol_fqn=symbol_fqn,
        content=_node_text(node, source),
    )
