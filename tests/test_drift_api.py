"""漂移审计 API 集成测试。"""

from __future__ import annotations

import copy
import random

from fastapi.testclient import TestClient

from app.drift import DriftBatch, audit_drift
from app.drift_examples import DRIFT_DEMO_EXAMPLES
from app.main import app

client = TestClient(app)


def post(payload):
    return client.post("/drift-audit", json=payload)


def test_openapi_contains_drift_examples():
    spec = client.get("/openapi.json").json()
    examples = spec["paths"]["/drift-audit"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert {
        "gradual_positive_drift",
        "reset_after_alarm",
        "both_sides_uncertain",
    } <= set(examples)


def test_drift_example_gradual_positive_drift():
    payload = copy.deepcopy(DRIFT_DEMO_EXAMPLES["gradual_positive_drift"]["value"])
    resp = post(payload)
    assert resp.status_code == 200
    body = resp.json()
    assert [b["status"] for b in body["batches"]] == [
        "no_alarm",
        "may_alarm",
        "may_alarm",
        "may_alarm",
    ]
    assert body["batches"][1]["possible_alarm_deviations"] == [2]
    assert body["batches"][3]["possible_alarm_deviations"] == [2]
    assert body["summary"]["first_certain_alarm_index"] is None
    assert body["summary"] == {
        "batch_count": 4,
        "k": 0,
        "h": 4,
        "will_alarm_count": 0,
        "may_alarm_count": 3,
        "no_alarm_count": 1,
        "first_certain_alarm_index": None,
    }


def test_drift_example_reset_after_alarm():
    payload = copy.deepcopy(DRIFT_DEMO_EXAMPLES["reset_after_alarm"]["value"])
    resp = post(payload)
    assert resp.status_code == 200
    body = resp.json()
    assert [b["status"] for b in body["batches"]] == [
        "will_alarm",
        "no_alarm",
        "will_alarm",
    ]
    assert body["batches"][0]["possible_alarm_deviations"] == [3, 4]
    assert body["batches"][2]["possible_alarm_deviations"] == [-4, -3]
    assert body["summary"]["first_certain_alarm_index"] == 0


def test_drift_example_both_sides_uncertain():
    payload = copy.deepcopy(DRIFT_DEMO_EXAMPLES["both_sides_uncertain"]["value"])
    resp = post(payload)
    assert resp.status_code == 200
    body = resp.json()
    assert [b["status"] for b in body["batches"]] == [
        "no_alarm",
        "may_alarm",
        "will_alarm",
    ]
    second = body["batches"][1]
    assert second["possible_alarm_deviations"] == [-2, 2]
    assert second["alarm_certain"] is False
    assert second["alarm_possible"] is True
    assert body["summary"]["first_certain_alarm_index"] == 2


def test_batch_indices_are_contiguous_from_zero():
    resp = post({"batches": [{"low": 0, "high": 0}] * 24, "k": 0, "h": 40})
    assert resp.status_code == 200
    body = resp.json()
    assert [b["index"] for b in body["batches"]] == list(range(24))
    assert all(b["status"] == "no_alarm" for b in body["batches"])


def test_empty_batches_is_valid():
    resp = post({"batches": [], "k": 3, "h": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["batches"] == []
    assert body["summary"]["batch_count"] == 0
    assert body["summary"]["first_certain_alarm_index"] is None


def test_response_is_deterministic():
    payload = copy.deepcopy(DRIFT_DEMO_EXAMPLES["both_sides_uncertain"]["value"])
    assert post(payload).json() == post(payload).json()


def test_rejects_more_than_24_batches():
    resp = post({"batches": [{"low": 0, "high": 0}] * 25, "k": 1, "h": 1})
    assert resp.status_code == 422


def test_rejects_inverted_interval():
    resp = post({"batches": [{"low": 3, "high": 2}], "k": 1, "h": 1})
    assert resp.status_code == 422


def test_rejects_out_of_range_endpoints():
    for low, high in ((-21, 0), (0, 21)):
        resp = post({"batches": [{"low": low, "high": high}], "k": 0, "h": 5})
        assert resp.status_code == 422, (low, high)


def test_rejects_k_and_h_out_of_range():
    for k, h in ((-1, 5), (21, 5), (0, 0), (0, 41)):
        resp = post({"batches": [], "k": k, "h": h})
        assert resp.status_code == 422, (k, h)


def test_accepts_array_and_min_max_interval_shapes():
    expected = post(
        {"batches": [{"low": -1, "high": 2}], "k": 0, "h": 5}
    ).json()
    for batch in ([-1, 2], {"min": -1, "max": 2}):
        resp = post({"batches": [batch], "k": 0, "h": 5})
        assert resp.status_code == 200
        assert resp.json() == expected


def test_rejects_array_with_wrong_arity():
    assert post({"batches": [[1]], "k": 0, "h": 5}).status_code == 422
    assert post({"batches": [[1, 2, 3]], "k": 0, "h": 5}).status_code == 422


def test_accepts_matching_explicit_indices():
    plain = {"batches": [{"low": -1, "high": 2}, {"low": 0, "high": 1}], "k": 0, "h": 5}
    expected = post(plain).json()
    for key in ("index", "batch", "batch_index"):
        payload = {
            "batches": [
                {"low": -1, "high": 2, key: 0},
                {"low": 0, "high": 1, key: 1},
            ],
            "k": 0,
            "h": 5,
        }
        resp = post(payload)
        assert resp.status_code == 200, (key, resp.json())
        assert resp.json() == expected


def test_rejects_non_contiguous_or_mismatched_indices():
    for idx in (1, -1, 24):
        resp = post(
            {
                "batches": [{"low": 0, "high": 1, "index": idx}],
                "k": 0,
                "h": 5,
            }
        )
        assert resp.status_code == 422, idx


def test_rejects_non_integer_values():
    for payload in (
        {"batches": [{"low": 1.5, "high": 2}], "k": 0, "h": 5},
        {"batches": [{"low": 1.0, "high": 2}], "k": 0, "h": 5},
        {"batches": [{"low": "1", "high": 2}], "k": 0, "h": 5},
        {"batches": [{"low": 1, "high": 2}], "k": 0.0, "h": 5},
        {"batches": [{"low": 1, "high": 2}], "k": "0", "h": 5},
        {"batches": [{"low": 1, "high": 2}], "k": 0, "h": 5.0},
    ):
        assert post(payload).status_code == 422, payload


def test_rejects_unknown_fields():
    resp = post(
        {
            "batches": [{"low": -1, "high": 1, "midpoint": 0}],
            "k": 0,
            "h": 5,
        }
    )
    assert resp.status_code == 422
    resp = post({"batches": [], "k": 0, "h": 5, "instrument": "X"})
    assert resp.status_code == 422


def test_rejects_missing_fields():
    assert post({"batches": [{"low": 0, "high": 1}], "k": 0}).status_code == 422
    assert post({"batches": [{"low": 0}], "k": 0, "h": 5}).status_code == 422


def test_api_matches_core_for_random_payloads():
    rng = random.Random(42)
    for _ in range(40):
        n = rng.randint(0, 6)
        batches = []
        for _ in range(n):
            low = rng.randint(-8, 8)
            high = rng.randint(low, 8)
            batches.append({"low": low, "high": high})
        k = rng.randint(0, 6)
        h = rng.randint(1, 12)
        resp = post({"batches": batches, "k": k, "h": h})
        assert resp.status_code == 200
        expected = audit_drift(
            [DriftBatch(b["low"], b["high"]) for b in batches], k=k, h=h
        )
        body = resp.json()
        assert [
            (b["status"], b["alarm_certain"], b["alarm_possible"], b["possible_alarm_deviations"])
            for b in body["batches"]
        ] == [
            (
                e.status,
                e.alarm_certain,
                e.alarm_possible,
                list(e.possible_alarm_deviations),
            )
            for e in expected.batches
        ]


def test_docs_endpoint_serves_swagger_ui():
    resp = client.get("/docs")
    assert resp.status_code == 200
    spec = client.get("/openapi.json").json()
    assert "/match" in spec["paths"]
    assert "/drift-audit" in spec["paths"]


def test_max_scale_request_completes_quickly():
    import time

    payload = {
        "batches": [{"low": -20, "high": 20} for _ in range(24)],
        "k": 0,
        "h": 40,
    }
    start = time.monotonic()
    resp = post(payload)
    elapsed = time.monotonic() - start
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["batches"]) == 24
    assert elapsed < 5.0
