"""v2 프로젝트의 최근 검증 실행으로 단계별 시험 사양서를 만든다 (LLM 호출).

    python scripts/generate_tests_v2.py                    # 씽크트리 샘플 프로젝트 전부
    python scripts/generate_tests_v2.py --subtopics LOGIN:로그인(이메일)   # 화면 일부만
    python scripts/generate_tests_v2.py --dry-run          # LLM 결과만 만들고 v2에 저장하지 않음

scripts/load_into_v2.py로 판정까지 채운 뒤 돌린다. 화면(소주제)마다 LLM을 한 번 부른다.
시험 절차는 기획서와 조건만으로 만들기 때문에 스택이 달라도 같다. 결과를 output/testgen/에 입력 해시로
저장해 두 번째 스택과 재실행은 LLM을 다시 부르지 않는다. output/은 기획서 내용이 들어가 git에서 제외한다.
코드 기반 예측은 각 프로젝트의 판정으로 붙인다 (adapters/v2_testgen.py).
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

from adapters.v2_testgen import build_test_generation
from api.main import create_app
from api.v2.storage import all_resources, resource
from api.v2.test_generation_adapter import ingest_test_generation
from llm import get_client
from testgen.generate import SpecRequirement, generate, input_hash

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "output" / "testgen"
BASE = "/api/v2/projects"


def _ok(res, expected=(200, 201, 202)):
    if res.status_code not in expected:
        sys.exit(f"{res.request.method} {res.request.url.path} 실패 {res.status_code}: {res.text[:500]}")
    return res.json()


def spec_requirements(run: dict, subtopic_id: str) -> list[SpecRequirement]:
    return [SpecRequirement(r["stable_key"], r["original_text"], tuple(r.get("review_reasons", [])),
                            tuple(r["conditions"]))
            for r in run["requirements"] if r["subtopic_id"] == subtopic_id]


def cached_generate(client, title: str, reqs: list[SpecRequirement]) -> tuple[dict, bool]:
    path = CACHE / f"{input_hash(client.model, title, reqs)}.json"
    if path.exists():
        return json.loads(path.read_text()), True
    result = generate(client, title, reqs)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**result, "subtopic": title}, ensure_ascii=False, indent=1))
    return result, False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-prefix", default="씽크트리 샘플")
    ap.add_argument("--subtopics", nargs="*", help="소주제 키 (예: LOGIN:로그인(이메일))")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    llm = get_client()
    with TestClient(create_app()) as api:
        projects = [p for p in _ok(api.get(BASE, params={"limit": 200}))["items"]
                    if p["name"].startswith(args.project_prefix)]
        if not projects:
            sys.exit(f"'{args.project_prefix}'로 시작하는 프로젝트가 없다. scripts/load_into_v2.py를 먼저 돌릴 것")
        db_ = api.app.state.database
        for project in projects:
            p = project["id"]
            with db_.session() as db:
                runs = [r for r in all_resources(db, p, "verification_run") if r.data["status"] == "completed"]
                if not runs:
                    sys.exit(f"{project['name']}: 완료된 검증 실행이 없다")
                run_item = max(runs, key=lambda r: r.created_at)
                run = {**run_item.data, "id": run_item.id}
                subtopics = [(s.id, s.data["key"], s.data["title"]) for s in all_resources(db, p, "subtopic")
                             if not args.subtopics or s.data["key"] in args.subtopics]

            jobs = [(sid, key, title, spec_requirements(run, sid)) for sid, key, title in subtopics]
            jobs = [j for j in jobs if j[3]]
            with ThreadPoolExecutor(args.workers) as pool:
                outs = list(pool.map(lambda j: cached_generate(llm, j[2], j[3]), jobs))

            print(f"[{project['name']}] 검증 실행 {run['id']}")
            usage = {"input_tokens": 0, "output_tokens": 0}
            outputs = []
            for (sid, key, title, reqs), (generated, hit) in zip(jobs, outs):
                if not hit:
                    for k in usage:
                        usage[k] += generated["usage"].get(k) or 0
                steps = sum(len(s["steps"]) for s in generated["scenarios"])
                print(f"  {key}: 시나리오 {len(generated['scenarios'])}, 단계 {steps}, 경고 {len(generated['warnings'])}"
                      + (" (저장된 결과 재사용)" if hit else ""))
                for w in generated["warnings"]:
                    print(f"    - {w['code']}: {w['message']}")
                outputs.append(build_test_generation(run, sid, key, generated))
            if usage["output_tokens"]:
                print(f"  LLM 사용량: 입력 {usage['input_tokens']}, 출력 {usage['output_tokens']} 토큰")
            if args.dry_run:
                continue

            req_ids = sorted({r["id"] for r in run["requirements"]})
            _ok(api.post(f"{BASE}/{p}/test-specifications/generate",
                         json={"verification_run_id": run["id"], "requirement_ids": req_ids}))
            specs = _ok(api.get(f"{BASE}/{p}/test-specifications", params={"limit": 200}))["items"]
            spec_id = max(specs, key=lambda s: s["created_at"])["id"]
            with db_.session() as db:
                total = 0
                for out in outputs:
                    if out["test_candidates"]:
                        total += len(ingest_test_generation(db, p, spec_id, out))
                spec = resource(db, p, spec_id, "test_specification")
            print(f"  사양서 {spec_id}: 시험 {total}개 저장 (revision {spec.data['revision']})")


if __name__ == "__main__":
    main()
