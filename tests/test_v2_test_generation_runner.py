from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.v2 import test_generation_runner as runner
from api.v2.storage import all_resources, project, replace, resource
from llm.base import LLMResponse
from test_v2_verification_runner import KEY, execute

pytest_plugins = ["test_v2_verification_runner"]


@pytest.fixture
def generation(linked, monkeypatch):
    c, p, reqs, _, _, _, config = linked
    submitted, done = execute(linked)
    assert done["status"] == "completed"
    control = SimpleNamespace(mode="normal", hook=None)
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        if control.hook:
            control.hook()
        if control.mode == "auth":
            return LLMResponse(None, KEY, "test-model", error="authentication_error")
        keys = kwargs["schema"]["properties"]["scenarios"]["items"]["properties"]["steps"]["items"][
            "properties"
        ]["condition_keys"]["items"]["enum"]
        data = {
            "scenarios": [
                {
                    "title": "명세에서 생성한 시험",
                    "type": "boundary",
                    "preconditions": "작성 화면",
                    "steps": [
                        {
                            "action": "제목을 입력한다",
                            "expected": "명세의 제한을 지킨다",
                            "account": None,
                            "input": "제목 50자",
                            "notes": "",
                            "condition_keys": [k],
                        }
                        for k in keys
                    ],
                    "assumptions": [],
                    "warnings": [],
                }
            ],
            "uncovered": [],
        }
        if control.mode == "foreign-key":
            data["scenarios"][0]["steps"][0]["condition_keys"] = ["invented-key"]
        if control.mode == "actual":
            data["scenarios"][0]["steps"][0]["actual"] = "AI가 통과했다고 생성"
        if control.mode == "empty":
            data = {
                "scenarios": [],
                "uncovered": [{"condition_key": k, "reason": "시험 환경 확인 필요"} for k in keys],
            }
        if control.mode == "second-invalid" and len(calls) == 2:
            data["scenarios"][0]["title"] = ""
        return LLMResponse(data, "", "test-model", {"input_tokens": 10, "output_tokens": 5})

    monkeypatch.setattr(runner, "analysis_client", lambda _: SimpleNamespace(complete_json=complete))
    body = {
        "verification_run_id": submitted["result_ref"]["id"],
        "requirement_ids": [r["id"] for r in reqs],
        "model_config_version": 1,
    }
    return c, p, body, control, calls, config


def start(generation, body=None):
    c, p, default, *_ = generation
    response = c.post(f"/api/v2/projects/{p}/test-specifications/generate", json=body or default)
    assert response.status_code == 202, response.text
    job = c.get(f"/api/v2/projects/{p}/jobs/{response.json()['id']}").json()
    spec_id = response.json()["result_ref"]["id"]
    return response.json(), job, c.get(f"/api/v2/projects/{p}/test-specifications/{spec_id}").json()


def test_generate_is_pinned_draft_and_export_has_no_actual_results(generation):
    c, p, body, _, calls, _ = generation
    b = f"/api/v2/projects/{p}"
    options = c.post(b + "/test-specifications/generate/prepare", json=body)
    assert options.status_code == 200 and options.json()["max_model_calls"] == 2 and not calls
    submitted, job, spec = start(generation)
    assert submitted["status"] == "queued" and job["status"] == "completed"
    assert job["result"] == {
        "test_case_count": 2,
        "step_count": 3,
        "uncovered_condition_count": 0,
        "warning_count": 0,
    }
    assert len(calls) == 2 and all(x["context"] == "" and "<= 10" not in x["prompt"] for x in calls)
    assert spec["generation_status"] == "completed" and spec["status"] == "draft" and spec["revision"] == 2
    assert (
        spec["prompt_version"] == runner.PROMPT_VERSION
        and spec["model_config_snapshot"]["config_version"] == 1
    )
    cases = c.get(b + f"/test-cases?test_specification_id={spec['id']}").json()["items"]
    assert {x["prediction"]["status"] for x in cases} == {"normal_expected", "failure_expected"}
    assert all(x["origin"] == "generated" for x in cases)
    assert all(c.get(b + f"/test-cases/{x['id']}/executions").json()["total"] == 0 for x in cases)
    summary = c.get(b + f"/test-specifications/{spec['id']}/execution-summary").json()
    assert summary["step_summary"] == {"total": 3, "not_run": 3, "passed": 0, "failed": 0, "on_hold": 0}
    # 작업 상태 전환이 절차 개정을 밀어내면 과거 Excel/조회가 다른 개정을 읽는 회귀를 막는다.
    changed = c.patch(
        b + f"/test-cases/{cases[0]['id']}/procedure",
        json={"base_revision": 1, "title": "사람이 수정한 제목"},
    )
    assert changed.status_code == 200
    old = c.get(b + f"/test-specifications/{spec['id']}?revision=2").json()
    assert old["revision"] == 2 and len(old["test_case_ids"]) == 2 and old["generation_status"] == "completed"
    again = start(generation)[2]
    assert (
        again["id"] != spec["id"]
        and c.get(b + f"/test-cases/{cases[0]['id']}").json()["title"] == "사람이 수정한 제목"
    )
    assert KEY not in str(job) + str(spec) + str(cases)


@pytest.mark.parametrize(
    "mode,code",
    [
        ("foreign-key", "MODEL_OUTPUT_INVALID"),
        ("actual", "MODEL_OUTPUT_INVALID"),
        ("auth", "LLM_AUTHENTICATION_FAILED"),
        ("second-invalid", "MODEL_OUTPUT_INVALID"),
    ],
)
def test_invalid_output_or_provider_failure_never_adopts_partial_cases(generation, mode, code):
    c, p, _, control, *_ = generation
    control.mode = mode
    _, job, spec = start(generation)
    assert job["status"] == spec["status"] == "failed" and job["error"]["code"] == code
    assert c.get(f"/api/v2/projects/{p}/test-cases?test_specification_id={spec['id']}").json()["total"] == 0
    assert KEY not in str(job) + str(spec)


def test_empty_output_preserves_uncovered_conditions_without_fake_cases(generation):
    _, _, _, control, *_ = generation
    control.mode = "empty"
    _, job, spec = start(generation)
    assert job["status"] == "completed" and job["result"]["test_case_count"] == 0
    assert len(spec["uncovered_conditions"]) == 3 and spec["warnings"][-1]["code"] == "NO_TEST_CANDIDATES"


def test_source_version_scope_and_missing_verdict_checks_happen_before_calls(generation):
    c, p, body, _, calls, _ = generation
    url = f"/api/v2/projects/{p}/test-specifications/generate"
    assert c.post(url, json={**body, "model_config_version": 99}).status_code == 409
    assert (
        c.post(
            url, json={**body, "requirement_ids": [*body["requirement_ids"], body["requirement_ids"][0]]}
        ).status_code
        == 422
    )
    other = c.post("/api/v2/projects", json={"name": "다른 프로젝트"}).json()["id"]
    assert c.post(f"/api/v2/projects/{other}/test-specifications/generate", json=body).status_code == 404
    with c.app.state.database.session() as db:
        run = resource(db, p, body["verification_run_id"], "verification_run")
        replace(db, run, {**run.data, "results": {}})
    assert c.post(url, json=body).status_code == 409 and not calls


def test_config_change_during_generation_stops_remaining_calls(generation):
    c, p, _, control, calls, _ = generation

    def change():
        with c.app.state.database.session() as db:
            project(db, p).config_version += 1

    control.hook = change
    _, job, spec = start(generation)
    assert job["status"] == "failed" and job["error"]["code"] == "CONFIG_CHANGED"
    assert len(calls) == 1 and not spec["test_case_ids"]


def test_pending_generation_cannot_duplicate_edit_or_replay_after_restart(generation, monkeypatch):
    c, p, body, _, calls, config = generation
    monkeypatch.setattr(runner, "run_generation", lambda *args: None)
    _, _, spec = start(generation)
    b = f"/api/v2/projects/{p}"
    assert c.post(b + "/test-specifications/generate", json=body).status_code == 409
    with c.app.state.database.session() as db:
        assert len(all_resources(db, p, "test_specification")) == 1
    assert (
        c.post(
            b + f"/test-specifications/{spec['id']}/edit-batches",
            json={
                "base_specification_revision": 1,
                "client_save_id": "00000000-0000-0000-0000-000000000001",
                "changes": [
                    {"test_case_id": "missing", "base_test_case_revision": 1, "procedure": {"title": "수정"}}
                ],
            },
        ).status_code
        == 409
    )
    with TestClient(create_app(config)) as restarted:
        current = restarted.get(b + f"/test-specifications/{spec['id']}").json()
        assert current["status"] == "failed" and current["error"]["code"] == "SERVER_RESTARTED"
    assert not calls
