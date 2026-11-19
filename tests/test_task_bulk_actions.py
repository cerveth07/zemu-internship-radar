"""Task-focused bulk edits: preserve JD data and avoid false application changes."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "bulk.sqlite"))
    with TestClient(app) as c:
        yield c


def company(client, name="测试公司"):
    response=client.post("/api/companies", json={"name":name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def job(client, company_id, title, priority="unrated"):
    response=client.post("/api/jobs",json={
        "company_id":company_id,"title":title,"direction":"other",
        "jd_text":"熟悉 Python，参与 Agent 项目","priority":priority})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_batch_job_updates_are_atomic_and_preserve_application_and_jd(client):
    cid=company(client)
    first=job(client,cid,"Agent A")
    second=job(client,cid,"Agent B","high")
    assert client.put(f"/api/jobs/{first}/application",json={
        "stage":"resume_sent","resume_version":"AI v1"}).status_code == 200
    result=client.post("/api/jobs/bulk",json={"ids":[first,second],"priority":"high"})
    assert result.status_code == 200, result.text
    assert result.json()["changed"] == 1 and result.json()["unchanged"] == 1
    detail=client.get(f"/api/jobs/{first}").json()
    assert detail["priority"]=="high"
    assert detail["jd_text"]=="熟悉 Python，参与 Agent 项目"
    assert detail["application"]["stage"]=="resume_sent"
    assert detail["application"]["resume_version"]=="AI v1"
    assert client.post("/api/jobs/bulk",json={
        "ids":[first,second],"direction":"agent"}).json()["changed"]==2
    assert all(client.get(f"/api/jobs/{ident}").json()["direction"]=="agent"
               for ident in (first,second))
    missing=client.post("/api/jobs/bulk",json={
        "ids":[first,99999],"priority":"skip"})
    assert missing.status_code==422
    assert client.get(f"/api/jobs/{first}").json()["priority"]=="high"
    assert client.post("/api/jobs/bulk",json={
        "ids":[first,first],"priority":"low"}).status_code==422
    assert client.post("/api/jobs/bulk",json={
        "ids":[first]}).status_code==422


def test_gpt_priority_import_previews_and_keeps_reasons_in_history(client):
    cid=company(client,"优先级公司")
    first=job(client,cid,"AI 产品")
    second=job(client,cid,"Agent FDE","medium")
    pid=client.post("/api/exports/jobs",json={"ids":[first,second]}).json()["package_id"]
    rows=[
        {"job_id":first,"priority":"high","reason":"团队项目有实际 Agent 场景"},
        {"job_id":second,"priority":"skip","reason":"硬性出勤不符合"},
    ]
    preview=client.post("/api/jobs/priority-import",json={"package_id":pid,"rows":rows,"dry_run":True})
    assert preview.status_code==200,preview.text
    assert preview.json()["changed"]==2
    assert preview.json()["items"][0]["reason"]=="团队项目有实际 Agent 场景"
    assert client.get(f"/api/jobs/{first}").json()["priority"]=="unrated"
    commit=client.post("/api/jobs/priority-import",json={"package_id":pid,"rows":rows,"dry_run":False})
    assert commit.status_code==200,commit.text
    detail=client.get(f"/api/jobs/{first}").json()
    assert detail["priority"]=="high" and detail["jd_text"]=="熟悉 Python，参与 Agent 项目"
    assert any(event["event"]=="priority_assessed" and
               "团队项目有实际 Agent 场景" in event["payload_json"]
               for event in detail["history"])
    assert client.get(f"/api/jobs/{second}").json()["priority"]=="skip"
    before_events=len([event for event in detail["history"]
                       if event["event"]=="priority_assessed"])
    assert client.post("/api/jobs/priority-import",json={"package_id":pid,
        "rows":rows,"dry_run":False}).status_code==200
    after_events=len([event for event in client.get(f"/api/jobs/{first}").json()["history"]
                      if event["event"]=="priority_assessed"])
    assert after_events==before_events
    # Reject a stale or wrong package in full; no partial priority updates.
    before=client.get(f"/api/jobs/{first}").json()["priority"]
    assert client.post("/api/jobs/priority-import",json={"package_id":pid,"rows":[
        rows[0],{"job_id":999999,"priority":"low","reason":"旧文件"}
    ],"dry_run":False}).status_code==422
    assert client.get(f"/api/jobs/{first}").json()["priority"]==before
    assert client.post("/api/jobs/priority-import",json={"package_id":pid,"rows":[rows[0],rows[0]]}).status_code==422
    assert client.post("/api/jobs/priority-import",json={"package_id":pid,"rows":[
        {"job_id":first,"priority":"nonsense","reason":"bad"}
    ]}).status_code==422


def test_batch_capture_disposition_reopens_and_rejects_invalid_selection(client):
    one=client.post("/api/capture/batches",json={"label":"第一批"}).json()["id"]
    two=client.post("/api/capture/batches",json={"label":"第二批"}).json()["id"]
    index=[0]
    def add(batch):
        index[0]+=1
        r=client.post(f"/api/capture/batches/{batch}/items",json={
            "raw_text":f"要求 Python，Agent 开发 {index[0]}","source_url":""})
        assert r.status_code in (200,201),r.text
        return r.json()["id"]
    ids=[add(one),add(one)]
    foreign=add(two)
    result=client.post("/api/capture/items/disposition-bulk",json={
        "ids":ids,"batch_id":one,"action":"defer"})
    assert result.status_code==200,result.text
    records=client.get(f"/api/capture/batches/{one}/items").json()
    assert all(r["status"]=="deferred" for r in records)
    assert client.post("/api/capture/items/disposition-bulk",json={
        "ids":ids,"batch_id":one,"action":"reopen"}).status_code==200
    assert client.post("/api/capture/items/disposition-bulk",json={
        "ids":[ids[0],foreign],"batch_id":one,"action":"dismiss"}).status_code==409
    assert all(r["status"]=="pending" for r in client.get(
        f"/api/capture/batches/{one}/items").json())
    assert client.post("/api/capture/items/disposition-bulk",json={
        "ids":ids,"batch_id":one,"action":"dismiss"}).status_code==200
    assert all(r["status"]=="dismissed" for r in client.get(
        f"/api/capture/batches/{one}/items").json())
