from io import BytesIO
from zipfile import ZipFile

from api.v2.impact import changed_files
from tests.test_v2_verification_runner import execute, linked  # noqa: F401  (fixture 재사용)


def run_id(linked):
    c, p, *_ = linked
    _, job = execute(linked)
    assert job["status"] == "completed", job
    return c.get(f"/api/v2/projects/{p}/verification-runs").json()["items"][0]["id"]


def test_changed_files_classifies_modify_add_delete():
    assert changed_files({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "x", "d": "4"}) == {
        "b": "modified", "d": "added", "c": "deleted"}


def test_file_change_points_only_to_screens_that_reach_it(linked):
    c, p, reqs, *_ = linked
    v = run_id(linked)
    url = f"/api/v2/projects/{p}/verification-runs/{v}/impact"
    main = c.get(url, params={"file": "uploaded/src/main.js"}).json()
    assert [s["screen_key"] for s in main["screens"]] == [reqs[0]["subtopic_id"]]
    assert main["screens"][0]["via_files"] == [{"path": "uploaded/src/main.js", "change": "modified", "entry": True}]
    assert main["requirement_count"] == 1 and main["screens"][0]["requirements"][0]["requirement_id"] == reqs[0]["id"]
    unused = c.get(url, params={"file": "uploaded/src/unrelated.js"}).json()
    assert unused["screens"] == [] and unused["unreached_files"] == ["uploaded/src/unrelated.js"]
    assert c.get(url).status_code == 422
    assert c.get(url, params={"file": "nope.js"}).status_code == 422


def test_new_snapshot_diff_finds_affected_screen(linked):
    c, p, reqs, body, *_ = linked
    v = run_id(linked)
    snapshot = c.get(f"/api/v2/projects/{p}/code/snapshots/{body['snapshot_id']}/files").json()
    files = {f["path"]: f["content"] for f in snapshot["files"]}
    files["uploaded/src/other.js"] = files["uploaded/src/other.js"].replace("50", "100")
    out = BytesIO()
    with ZipFile(out, "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
    source = c.post(f"/api/v2/projects/{p}/code/sources", files={"file": ("src.zip", out.getvalue())}).json()["source_id"]
    new = c.post(f"/api/v2/projects/{p}/code/index", json={"source_id": source}).json()["result_ref"]["id"]
    report = c.get(f"/api/v2/projects/{p}/verification-runs/{v}/impact", params={"snapshot_id": new}).json()
    assert report["changed_files"] == [{"path": "uploaded/src/other.js", "change": "modified"}]
    assert [s["screen_key"] for s in report["screens"]] == [reqs[1]["subtopic_id"]]
