from matcher.data import ChunkRow
from matcher.scope.graph import Graph, build_identifier_edges

PKG = "src/main/java/app"


def chunk(cid, kind, fqn, content, file=None):
    name = fqn.split("#")[0].rsplit(".", 1)[-1 if kind == "class" else -2]
    return ChunkRow(cid, kind, file or f"{PKG}/{name}.java", fqn, content)


def graph(*chunks):
    g = Graph({c.id: c for c in chunks})
    build_identifier_edges(g)
    return g


COMMON = [
    chunk(10, "class", "app.MemberService", "public class MemberService {"),
    chunk(11, "method", "app.MemberService.signup", "public void signup(SignupForm form) { }"),
    chunk(20, "class", "app.VendorService", "public class VendorService {"),
    chunk(21, "method", "app.VendorService.signup", "public void signup(VendorForm form) { }"),
]


def test_field_type_picks_same_name_method_of_that_class_only():
    g = graph(*COMMON,
              chunk(1, "class", "app.AuthController", "public class AuthController {"),
              chunk(2, "field", "app.AuthController#memberService", "private final MemberService memberService;"),
              chunk(3, "method", "app.AuthController.signup", "public String signup(SignupForm f) { memberService.signup(f); }"))
    assert 11 in g.edges[3] and 21 not in g.edges[3]


def test_parameter_and_static_receivers_are_resolved():
    g = graph(*COMMON,
              chunk(1, "class", "app.Batch", "public class Batch {"),
              chunk(2, "method", "app.Batch.run", "void run(VendorService vendors) { vendors.signup(null); }"),
              chunk(3, "method", "app.Batch.once", "void once() { MemberService.signup(null); }"))
    assert g.edges[2] >= {21} and 11 not in g.edges[2]
    assert 11 in g.edges[3] and 21 not in g.edges[3]


def test_interface_receiver_reaches_implementation():
    g = graph(chunk(10, "class", "app.MemberService", "public interface MemberService {"),
              chunk(11, "method", "app.MemberService.signup", "void signup(SignupForm form);"),
              chunk(12, "class", "app.MemberServiceImpl", "public class MemberServiceImpl implements MemberService {"),
              chunk(13, "method", "app.MemberServiceImpl.signup", "public void signup(SignupForm form) { }"),
              *COMMON[2:],
              chunk(1, "class", "app.AuthController", "public class AuthController {"),
              chunk(2, "field", "app.AuthController#memberService", "private final MemberService memberService;"),
              chunk(3, "method", "app.AuthController.join", "public String join(SignupForm f) { memberService.signup(f); }"))
    assert {11, 13} <= g.edges[3] and 21 not in g.edges[3]


def test_unknown_receiver_falls_back_to_all_same_name_methods():
    g = graph(*COMMON,
              chunk(1, "class", "app.Job", "public class Job {"),
              chunk(2, "method", "app.Job.run", "void run() { lookup().signup(null); }"))
    assert {11, 21} <= g.edges[2]
