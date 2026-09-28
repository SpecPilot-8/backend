"""JS/JSX 소스를 tree-sitter로 파싱해 함수/컴포넌트/최상위 문장 단위 chunk로 쪼갠다.

React 함수형 컴포넌트도 결국 "이름 붙은 함수/화살표함수"라 별도 특수 케이스
없이 동일하게 처리된다. 함수가 아닌 최상위 문장(라우트 등록, 상수 선언 등)은
statement 청크가 된다. 익명 콜백은 따로 청크로 만들지 않고 그것을 담은 문장에
포함시킨다.

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


def _is_named_function_decl(node) -> bool:
    """`function foo(){}` 또는 `const foo = () => {}` 형태인가."""
    if node.type == "function_declaration":
        return node.child_by_field_name("name") is not None
    if node.type in ("lexical_declaration", "variable_declaration"):
        return any(
            c.type == "variable_declarator"
            and (v := c.child_by_field_name("value")) is not None
            and v.type in _FUNCTION_VALUE_TYPES
            and c.child_by_field_name("name") is not None
            for c in node.children
        )
    return False


def _unwrap_export(node):
    """`export <decl>`이면 decl을, 아니면 그대로 돌려준다."""
    if node.type == "export_statement":
        decl = node.child_by_field_name("declaration")
        if decl is not None:
            return decl
    return node


def _defines_named_function(node) -> bool:
    if _is_named_function_decl(node):
        return True
    return any(_defines_named_function(c) for c in node.children)


def _wrapper_scope_body(stmt):
    """`document.addEventListener("DOMContentLoaded", () => {...})`처럼 파일 전체를
    감싸는 익명 콜백이면 그 본문(statement_block)을 돌려준다.

    안에 이름 붙은 함수를 정의하는 콜백만 '감싸개'로 본다. 그런 콜백은 사실상
    모듈 스코프 역할이라 통째로 한 청크로 두면 로그인·회원가입 로직이 한 덩어리가
    된다. 반대로 `router.post("/login", (req, res) => {...})`처럼 이름 붙은 함수가
    없는 콜백은 그 자체가 하나의 처리 단위이므로 문장 전체를 청크로 둔다.
    """
    if stmt.type != "expression_statement":
        return None
    call = stmt.children[0] if stmt.children else None
    if call is None or call.type != "call_expression":
        return None
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for arg in args.children:
        if arg.type in _FUNCTION_VALUE_TYPES:
            body = arg.child_by_field_name("body")
            if body is not None and body.type == "statement_block" and _defines_named_function(body):
                return body
    return None


def _statement_name(stmt, source: bytes) -> str:
    """청크 식별용 이름. 선언이면 변수명, 호출이면 `router.post("/login")` 꼴."""
    node = _unwrap_export(stmt)
    if node.type in ("lexical_declaration", "variable_declaration"):
        names = [
            _node_text(n, source)
            for c in node.children
            if c.type == "variable_declarator" and (n := c.child_by_field_name("name")) is not None
        ]
        if names:
            return ",".join(names)
    if node.type == "class_declaration" and (n := node.child_by_field_name("name")) is not None:
        return _node_text(n, source)
    if node.type == "expression_statement" and node.children:
        expr = node.children[0]
        if expr.type == "call_expression":
            callee = _node_text(expr.child_by_field_name("function"), source)
            args = expr.child_by_field_name("arguments")
            first = next((a for a in args.children if a.type not in ("(", ")", ",")), None) if args else None
            if first is not None and first.type in ("string", "template_string"):
                return f"{callee}({_node_text(first, source)})"
            return callee
    return f"stmt@{stmt.start_point[0] + 1}"


def chunk_js_file(file_path: str, content: str) -> list[Chunk]:
    source = content.encode("utf-8")
    tree = _PARSER.parse(source)
    chunks: list[Chunk] = []

    def emit(node, name: str, chunk_type: str = "function", end_node=None) -> None:
        end_node = end_node or node
        chunks.append(Chunk(
            chunk_type=chunk_type,
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=end_node.end_point[0] + 1,
            symbol_fqn=f"{file_path}::{name}",
            content=source[node.start_byte:end_node.end_byte].decode("utf-8"),
        ))

    def emit_function(stmt) -> None:
        # `export`까지 포함해야 청크만 보고도 공개 여부를 알 수 있다.
        node = _unwrap_export(stmt)
        if node.type == "function_declaration":
            name = _node_text(node.child_by_field_name("name"), source)
        else:
            # `const Foo = () => {...}` — 선언문 전체를 청크로 잡아야 content에
            # 이름(`const Foo =`)이 남는다. 화살표함수 노드만 잘라내면 익명처럼 보인다.
            name = next(
                _node_text(c.child_by_field_name("name"), source)
                for c in node.children
                if c.type == "variable_declarator"
                and (v := c.child_by_field_name("value")) is not None
                and v.type in _FUNCTION_VALUE_TYPES
            )
        emit(stmt, name)

    def emit_class_methods(class_node) -> None:
        body = class_node.child_by_field_name("body")
        for member in body.children if body else []:
            if member.type == "method_definition" and (n := member.child_by_field_name("name")) is not None:
                emit(member, _node_text(n, source))

    def chunk_scope(statements) -> None:
        """한 스코프(모듈 최상위 또는 감싸개 콜백 본문)의 문장들을 청크로 나눈다.

        예전엔 이름 붙은 함수만 청크로 만들어서, 최상위 문장이 통째로 빠졌다.
        Express 라우트(`router.post(...)`), `const maxFileSize = 50 * 1024 * 1024`,
        `PASSWORD_PATTERN` 같은 요구사항의 핵심 근거가 어느 청크에도 없었다.
        이제 import를 뺀 모든 문장이 어떤 청크에든 속한다.

        여러 줄짜리 문장은 단독 청크로, 연속된 한 줄짜리 문장은 하나로 묶는다.
        `const __dirname = ...` 같은 한 줄 문장을 전부 따로 만들면 후보 목록이
        잡음으로 불어나기 때문이다.
        """
        group: list = []

        def flush() -> None:
            if group:
                name = _statement_name(group[0], source)
                if len(group) > 1:
                    name += f"+{len(group) - 1}"
                emit(group[0], name, "statement", end_node=group[-1])
                group.clear()

        for stmt in statements:
            if stmt.type in ("comment", "{", "}"):
                continue
            if stmt.type == "import_statement":
                flush()
                continue
            inner = _unwrap_export(stmt)
            if stmt.type == "export_statement" and inner is stmt:
                # `export default router;` / `export { a, b };` — 이름 재노출일 뿐 근거가 아니다.
                value = stmt.child_by_field_name("value")
                if value is None or value.type == "identifier":
                    continue
            if _is_named_function_decl(inner):
                flush()
                emit_function(stmt)
            elif inner.type == "class_declaration":
                flush()
                emit_class_methods(inner)
            elif (body := _wrapper_scope_body(stmt)) is not None:
                flush()
                chunk_scope(body.children)
            elif stmt.start_point[0] == stmt.end_point[0]:
                group.append(stmt)
            else:
                flush()
                emit(stmt, _statement_name(stmt, source), "statement")
        flush()

    chunk_scope(tree.root_node.children)
    return chunks
