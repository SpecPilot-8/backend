from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import re
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from api.main import create_app
from api.v2 import server_llm, verification_runner
from api.v2.storage import resource
from llm.base import LLMResponse

KEY = "fake-api-key-ui-tests"


@pytest.fixture
def linked(config, monkeypatch):
    config = replace(config, llm_provider="anthropic", llm_model="test-model", llm_api_key=KEY)
    monkeypatch.setattr(
        server_llm,
        "probe_client",
        lambda _: SimpleNamespace(complete_json=lambda **k: LLMResponse({"ok": True}, "", "test-model")),
    )
    control = SimpleNamespace(mode="normal", hook=None)
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        if control.mode == "auth":
            return LLMResponse(None, KEY, "test-model", error="authentication_error")
        context = kwargs["context"]
        by_file = {
            path: int(cid)
            for cid, path in re.findall(
                r'<(?:chunk|primary|reference) id="(\d+)"[^>]*file="([^"]+)"', context + kwargs["prompt"]
            )
        }
        if kwargs["schema"]["required"] == ["matches"]:
            matches = []
            for key in re.findall(r"^\[(.+)\]$", kwargs["prompt"], re.M):
                ids = (
                    [by_file["src/main.js"], by_file["src/other.js"]]
                    if key == "main-rule"
                    else [by_file["src/other.js"]]
                )
                if control.mode == "bad-match" and key == "main-rule":
                    ids = [99999]
                if control.mode == "empty" and key == "main-rule":
                    ids = []
                if control.mode == "outside-extra" and key == "main-rule":
                    ids.append(by_file["src/unrelated.js"])
                matches.append({"requirement_key": key, "chunk_ids": ids})
            data = {"matches": matches}
        else:
            if control.hook:
                control.hook()
            cid = by_file["src/main.js"] if "main-rule" in kwargs["prompt"] else by_file["src/other.js"]
            status = "mismatch" if "main-rule" in kwargs["prompt"] else "implemented"
            if control.mode == "foreign" and "main-rule" in kwargs["prompt"]:
                cid, status = by_file["src/other.js"], "implemented"
            data = {
                "conditions": [
                    {
                        "condition_id": int(n),
                        "status": status,
                        "evidence_chunk_ids": [cid],
                        "reasoning": "50자 요구와 10자 구현 비교"
                        if status == "mismatch"
                        else "구현 근거 확인",
                        "message_match": "not_applicable",
                        "server_validation": "missing",
                        "spec_suspect": "",
                    }
                    for n in re.findall(r"^- id (\d+):", kwargs["prompt"], re.M)
                ]
            }
        return LLMResponse(data, "", "test-model", {"input_tokens": 20, "output_tokens": 10})

    monkeypatch.setattr(
        verification_runner, "analysis_client", lambda _: SimpleNamespace(complete_json=complete)
    )
    with TestClient(create_app(config)) as c:
        p = c.post("/api/v2/projects", json={"name": "실행 연결"}).json()["id"]
        base = f"/api/v2/projects/{p}"
        import pymupdf

        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((50, 50), "Title at most 50. CSS color needs visual review.")
            doc = c.post(base + "/documents", files={"file": ("test.pdf", pdf.tobytes())}).json()["id"]
        c.post(base + f"/documents/{doc}/parse", json={"document_revision": 1})
        reqs = []
        for key, title in [("main-rule", "제목 50자"), ("other-rule", "다른 화면")]:
            group = c.post(
                base + f"/documents/{doc}/requirement-groups",
                json={"document_revision": 1, "topic_title": "시험 화면", "subtopic_title": title},
            ).json()
            r = c.post(
                base + "/requirements",
                json={
                    "document_id": doc,
                    "document_revision": 1,
                    "subtopic_id": group["subtopic_id"],
                    "title": title,
                    "author_reason": "검증 실행 연결 테스트",
                    "conditions": [
                        {
                            "stable_key": key + "-c1",
                            "text": "제목 최대 50자",
                            "category": "validation",
                            "target": "frontend",
                            "verifiable": "code",
                        }
                    ]
                    + (
                        [
                            {
                                "stable_key": key + "-c2",
                                "text": "색상 확인",
                                "category": "display",
                                "target": "unknown",
                                "verifiable": "not_statically_verifiable",
                            }
                        ]
                        if key == "main-rule"
                        else []
                    ),
                },
            ).json()
            # 사람이 지정한 검토 전 키를 테스트용으로 고정한다. 모델 판정은 매칭·judge 호출로만 얻는다.
            with c.app.state.database.session() as db:
                row = resource(db, p, r["id"], "requirement")
                row.data = {**row.data, "stable_key": key}
            reqs.append(
                c.put(
                    base + f"/requirements/{r['id']}/review", json={"base_revision": 1, "status": "approved"}
                ).json()
            )
        out = BytesIO()
        with ZipFile(out, "w") as z:
            z.writestr(
                "uploaded/src/main.js", "export function validateTitle(title) { return title.length <= 10; }"
            )
            z.writestr(
                "uploaded/src/other.js", "export function otherScreen(title) { return title.length <= 50; }"
            )
            z.writestr(
                "uploaded/tests/only.test.js",
                'export const fakeImplementation = "test-only-should-never-match";',
            )
            z.writestr("uploaded/application.yml", "title-limit: 50")
            z.writestr("uploaded/src/unrelated.js", "export function unusedScreen() { return false; }")
            z.writestr("uploaded/package-lock.json", "lock-file-not-implementation")
            z.writestr("uploaded/schema.sql", "CREATE TABLE notices (title VARCHAR(50));")
        source = c.post(base + "/code/sources", files={"file": ("src.zip", out.getvalue())}).json()[
            "source_id"
        ]
        snap = c.post(base + "/code/index", json={"source_id": source}).json()["result_ref"]["id"]
        c.put(base + "/llm", json={"base_version": 0, "profile_id": "primary"})
        c.post(base + "/llm/connection-check", json={"config_version": 1})
        body = {
            "snapshot_id": snap,
            "requirement_versions": [{"requirement_id": r["id"], "revision": r["revision"]} for r in reqs],
            "model_config_version": 1,
            "scope_config": {
                "stack": "react",
                "confirmed": True,
                "entries": {
                    reqs[0]["subtopic_id"]: ["uploaded/src/main.js"],
                    reqs[1]["subtopic_id"]: ["uploaded/src/other.js"],
                },
            },
        }
        yield c, p, reqs, body, control, calls, config


def execute(linked):
    c, p, _, body, *_ = linked
    r = c.post(f"/api/v2/projects/{p}/verification-runs", json=body)
    assert r.status_code == 202, r.text
    return r.json(), c.get(f"/api/v2/projects/{p}/jobs/{r.json()['id']}").json()


def test_real_modules_are_called_and_scope_is_not_target_filter(linked):
    c, p, reqs, body, _, calls, _ = linked
    prepare = c.post(f"/api/v2/projects/{p}/verification-runs/prepare", json=body)
    assert prepare.status_code == 200 and prepare.json()["requires_confirmation"]
    assert all("tests/" not in f for f in prepare.json()["files"])
    submitted, job = execute(linked)
    assert submitted["status"] == "queued" and job["status"] == "completed", job
    assert len(calls) == 4  # 두 화면의 실제 selector+judge. 시각 조건은 judge로 보내지 않는다.
    assert all("test-only-should-never-match" not in v["context"] for v in calls)
    assert all("lock-file-not-implementation" not in v["context"] for v in calls)
    assert any("schema.sql" in v["context"] for v in calls)
    run = submitted["result_ref"]["id"]
    detail = c.get(f"/api/v2/projects/{p}/verification-runs/{run}/requirements/{reqs[0]['id']}").json()
    assert detail["status"] == "mismatch"
    assert [v["status"] for v in detail["condition_verdicts"]] == ["mismatch", "not_statically_verifiable"]
    assert detail["evidence"][0]["file_path"] == "uploaded/src/main.js"
    assert "<= 10" in detail["evidence"][0]["code_snippet"]
    assert detail["findings"] and detail["call_flow"]["nodes"]
    assert KEY not in str(job) and KEY not in str(detail)
    assert c.get(f"/api/v2/projects/{p}/verification-runs/{run}/summary").json()["implemented"] == 1


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("bad-match", "needs_review"),
        ("foreign", "needs_review"),
        ("empty", "not_found"),
        ("outside-extra", "mismatch"),
    ],
)
def test_invalid_or_other_screen_code_cannot_become_implemented(linked, mode, expected):
    c, p, reqs, _, control, _, _ = linked
    control.mode = mode
    submitted, job = execute(linked)
    assert job["status"] == "completed", job
    detail = c.get(
        f"/api/v2/projects/{p}/verification-runs/{submitted['result_ref']['id']}/requirements/{reqs[0]['id']}"
    ).json()
    assert detail["condition_verdicts"][0]["status"] == expected
    assert detail["condition_verdicts"][1]["status"] == "not_statically_verifiable"


def test_provider_failure_does_not_forge_completed_verdict(linked):
    c, p, _, _, control, _, _ = linked
    control.mode = "auth"
    submitted, job = execute(linked)
    assert (
        job["status"] == "failed"
        and job["error"]["code"] == "LLM_AUTHENTICATION_FAILED"
        and KEY not in str(job)
    )
    run = c.get(f"/api/v2/projects/{p}/verification-runs/{submitted['result_ref']['id']}").json()
    assert run["status"] == "failed"
    rows = c.get(f"/api/v2/projects/{p}/verification-runs/{run['id']}/traceability").json()["items"]
    assert all(r["status"] is None for r in rows)


def test_mapping_is_explicit_and_scoped_to_snapshot(linked):
    c, p, _, body, _, calls, _ = linked
    for scope in [
        None,
        {"stack": "react", "confirmed": True, "entries": {"wrong": ["uploaded/src/main.js"]}},
    ]:
        bad = {**body, "scope_config": scope}
        assert c.post(f"/api/v2/projects/{p}/verification-runs", json=bad).status_code in (409, 422)
    bad = deepcopy(body)
    bad["scope_config"]["confirmed"] = False
    assert c.post(f"/api/v2/projects/{p}/verification-runs", json=bad).status_code == 422
    assert calls == []


def test_config_change_during_judgment_leaves_unfinished_rows_unanalyzed(linked):
    c, p, _, _, control, _, _ = linked
    control.hook = lambda: c.put(
        f"/api/v2/projects/{p}/llm", json={"base_version": 1, "profile_id": "primary"}
    )
    submitted, job = execute(linked)
    assert job["status"] == "failed" and job["error"]["code"] == "CONFIG_CHANGED"
    assert (
        c.get(f"/api/v2/projects/{p}/verification-runs/{submitted['result_ref']['id']}/summary").json()[
            "not_run"
        ]
        == 2
    )


def test_queued_run_blocks_duplicates_and_restart_records_failure(linked, monkeypatch):
    c, p, _, body, _, calls, config = linked
    monkeypatch.setattr(verification_runner, "run_verification", lambda *a: None)
    queued = c.post(f"/api/v2/projects/{p}/verification-runs", json=body).json()
    assert queued["status"] == "queued"
    assert c.post(f"/api/v2/projects/{p}/verification-runs", json=body).status_code == 409
    with TestClient(create_app(config)) as restarted:
        job = restarted.get(f"/api/v2/projects/{p}/jobs/{queued['id']}").json()
        assert job["status"] == "failed" and job["error"]["code"] == "SERVER_RESTARTED"
        run = restarted.get(f"/api/v2/projects/{p}/verification-runs/{queued['result_ref']['id']}").json()
        assert run["status"] == "failed"
    assert calls == []


def test_cached_usage_is_recorded_separately_from_uncached_input():
    usage = {"input_tokens": 0, "output_tokens": 0}
    verification_runner.add_usage(
        usage,
        {
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30,
        },
    )
    assert usage == {
        "input_tokens": 7,
        "output_tokens": 3,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 30,
    }


def test_infer_suggested_files_matches_keywords_and_fallbacks():
    from api.v2.verification_scope import infer_suggested_files
    files = ["src/views/LoginView.tsx", "src/views/OrderView.tsx", "src/App.tsx"]
    # 1. 로그인 키워드 매칭
    matched = infer_suggested_files({"title": "로그인 / 회원가입"}, files, "react")
    assert "src/views/LoginView.tsx" in matched
    # 2. 주문 키워드 매칭
    matched_order = infer_suggested_files({"title": "주문 결제"}, files, "react")
    assert "src/views/OrderView.tsx" in matched_order
    # 3. 매칭 없는 경우 fallback
    matched_unknown = infer_suggested_files({"title": "기타 미정 화면"}, files, "react")
    assert len(matched_unknown) > 0


def test_cancel_running_job_transitions_to_cancelled(linked, monkeypatch):
    c, p, _, body, _, calls, _ = linked
    monkeypatch.setattr(verification_runner, "run_verification", lambda *a: None)
    # 검증 작업 생성 (queued 상태)
    r = c.post(f"/api/v2/projects/{p}/verification-runs", json=body)
    assert r.status_code == 202
    job_id = r.json()["id"]
    # 취소 호출
    cancel_resp = c.post(f"/api/v2/projects/{p}/jobs/{job_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"
    assert cancel_resp.json()["error"]["code"] == "JOB_CANCELLED"
    # 재취소 시 409
    cancel_again = c.post(f"/api/v2/projects/{p}/jobs/{job_id}/cancel")
    assert cancel_again.status_code == 409


