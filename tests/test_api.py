"""API 集成测试。"""

import copy

from fastapi.testclient import TestClient

from app.examples import DEMO_EXAMPLES
from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_redirects_to_docs():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/docs"


def test_docs_page_served():
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_openapi_contains_three_demo_examples():
    spec = client.get("/openapi.json").json()
    examples = spec["paths"]["/match"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert {"duplicate_barcodes", "crossing_candidates", "equivalent_optima"} <= set(
        examples
    )


def test_demo_duplicate_barcodes():
    payload = copy.deepcopy(DEMO_EXAMPLES["duplicate_barcodes"]["value"])
    resp = client.post("/match", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["max_pairs"] == 3
    assert body["summary"]["min_total_time_diff_seconds"] == 840.0
    assert {
        (p["sampling_id"], p["detection_id"]) for p in body["certain_pairs"]
    } == {("S-01", "D-01"), ("S-02", "D-02"), ("S-03", "D-03")}
    assert body["summary"]["sampling_status"] == {
        "certain": 3,
        "review": 0,
        "unmatched": 0,
    }


def test_demo_crossing_candidates():
    payload = copy.deepcopy(DEMO_EXAMPLES["crossing_candidates"]["value"])
    resp = client.post("/match", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["max_pairs"] == 2
    assert body["summary"]["min_total_time_diff_seconds"] == 420.0
    assert {
        (p["sampling_id"], p["detection_id"]) for p in body["certain_pairs"]
    } == {("S-11", "D-11"), ("S-12", "D-12")}
    det = {r["id"]: r for r in body["detection_records"]}
    assert det["D-13"]["status"] == "unmatched"
    assert det["D-13"]["possible_partners"] == []


def test_demo_equivalent_optima():
    payload = copy.deepcopy(DEMO_EXAMPLES["equivalent_optima"]["value"])
    resp = client.post("/match", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["max_pairs"] == 3
    assert body["summary"]["min_total_time_diff_seconds"] == 780.0
    # 等价最优区域不产生确定配对，只有 BC-500 一对确定
    assert [(p["sampling_id"], p["detection_id"]) for p in body["certain_pairs"]] == [
        ("S-23", "D-23")
    ]
    sam = {r["id"]: r for r in body["sampling_records"]}
    det = {r["id"]: r for r in body["detection_records"]}
    for rid in ("S-21", "S-22"):
        assert sam[rid]["status"] == "review"
        assert sam[rid]["matched_in_all_optimal"] is True
        assert {o["id"] for o in sam[rid]["possible_partners"]} == {"D-21", "D-22"}
    for rid in ("D-21", "D-22"):
        assert det[rid]["status"] == "review"
        assert {o["id"] for o in det[rid]["possible_partners"]} == {"S-21", "S-22"}
    assert sam["S-23"]["status"] == "certain"
    assert sam["S-23"]["certain_partner_id"] == "D-23"
    assert sam["S-24"]["status"] == "unmatched"
    assert det["D-24"]["status"] == "unmatched"


def test_response_is_deterministic():
    payload = copy.deepcopy(DEMO_EXAMPLES["equivalent_optima"]["value"])
    first = client.post("/match", json=payload).json()
    second = client.post("/match", json=payload).json()
    assert first == second


def _one_record(rid="S1"):
    return {"id": rid, "barcode": "B", "timestamp": "2026-11-14T08:00:00Z"}


def test_rejects_more_than_12_records_per_side():
    payload = {
        "sampling_records": [
            {"id": f"S{i}", "barcode": "B", "timestamp": "2026-11-14T08:00:00Z"}
            for i in range(13)
        ],
        "detection_records": [],
    }
    resp = client.post("/match", json=payload)
    assert resp.status_code == 422


def test_rejects_duplicate_ids_within_group():
    payload = {
        "sampling_records": [_one_record("S1"), _one_record("S1")],
        "detection_records": [],
    }
    resp = client.post("/match", json=payload)
    assert resp.status_code == 422


def test_rejects_naive_timestamp():
    payload = {
        "sampling_records": [
            {"id": "S1", "barcode": "B", "timestamp": "2026-11-14T08:00:00"}
        ],
        "detection_records": [],
    }
    resp = client.post("/match", json=payload)
    assert resp.status_code == 422


def test_rejects_empty_barcode_and_id():
    payload = {
        "sampling_records": [{"id": "", "barcode": "B", "timestamp": "2026-11-14T08:00:00Z"}],
        "detection_records": [],
    }
    assert client.post("/match", json=payload).status_code == 422
    payload = {
        "sampling_records": [{"id": "S1", "barcode": "", "timestamp": "2026-11-14T08:00:00Z"}],
        "detection_records": [],
    }
    assert client.post("/match", json=payload).status_code == 422


def test_rejects_unknown_fields():
    payload = {
        "sampling_records": [
            {
                "id": "S1",
                "barcode": "B",
                "timestamp": "2026-11-14T08:00:00Z",
                "operator": "someone",
            }
        ],
        "detection_records": [],
    }
    assert client.post("/match", json=payload).status_code == 422


def test_accepts_timezone_offset_and_normalizes_to_utc():
    payload = {
        "sampling_records": [
            {"id": "S1", "barcode": "B", "timestamp": "2026-11-14T16:00:00+08:00"}
        ],
        "detection_records": [
            {"id": "D1", "barcode": "B", "timestamp": "2026-11-14T08:05:00Z"}
        ],
    }
    resp = client.post("/match", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["max_pairs"] == 1
    assert body["certain_pairs"][0]["time_diff_seconds"] == 300.0
    assert body["sampling_records"][0]["timestamp"].endswith("Z") or body[
        "sampling_records"
    ][0]["timestamp"].endswith("+00:00")


def test_empty_request_is_valid():
    resp = client.post("/match", json={"sampling_records": [], "detection_records": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["max_pairs"] == 0
    assert body["certain_pairs"] == []
