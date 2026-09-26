import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "api.db"))) as c:
        yield c


def add(client, name, email=None):
    r = client.post("/api/candidates", json={"name": name, "email": email})
    assert r.status_code == 201, r.text
    return r.json()


def move(client, cid, action, expected, note=None):
    return client.post(f"/api/candidates/{cid}/transitions",
                       json={"action": action, "expected_stage": expected, "note": note})


def test_full_flow(client):
    c = add(client, "Priya Sharma", "priya@example.com")
    assert c["stage"] == "Applied" and c["next_stage"] == "Screening"
    for expected in ["Applied", "Screening", "Interview", "Offer"]:
        r = move(client, c["id"], "advance", expected)
        assert r.status_code == 200
    d = client.get(f"/api/candidates/{c['id']}").json()
    assert d["stage"] == "Hired" and d["next_stage"] is None and not d["can_reject"]
    assert [h["to_stage"] for h in d["history"]] == ["Applied", "Screening", "Interview", "Offer", "Hired"]


def test_board_lists_everyone(client):
    add(client, "A")
    add(client, "B")
    assert [c["name"] for c in client.get("/api/candidates").json()] == ["A", "B"]


def test_stale_move_is_409_and_final_outcome_is_422(client):
    c = add(client, "A")
    assert move(client, c["id"], "advance", "Applied").status_code == 200
    r = move(client, c["id"], "advance", "Applied")
    assert r.status_code == 409 and "now in Screening" in r.json()["error"]["message"]
    assert move(client, c["id"], "reject", "Screening", "no").status_code == 200
    r = move(client, c["id"], "advance", "Rejected")
    assert r.status_code == 422 and "can't be changed" in r.json()["error"]["message"]


def test_history_has_no_write_endpoints(client):
    c = add(client, "A")
    assert client.delete(f"/api/candidates/{c['id']}").status_code == 405
    assert client.put(f"/api/candidates/{c['id']}", json={"name": "B"}).status_code == 405
    assert client.patch(f"/api/candidates/{c['id']}", json={"name": "B"}).status_code == 405


def test_validation_errors(client):
    assert client.post("/api/candidates", json={"name": " "}).status_code == 422
    assert client.get("/api/candidates/999").status_code == 404
    c = add(client, "A")
    assert move(client, c["id"], "skip", "Applied").status_code == 422


def test_search_endpoint(client):
    c = add(client, "Priya Sharma")
    add(client, "Rahul Nair")
    move(client, c["id"], "advance", "Applied")
    r = client.get("/api/search", params={"q": "sharam in:screening", "tz": "Asia/Kolkata"}).json()
    assert r["count"] == 1 and r["results"][0]["name"] == "Priya Sharma"
    assert r["interpretation"] == ["Name is like “sharam”", "Current stage is Screening"]
    assert r["results"][0]["reasons"] and r["empty"] is None


def test_search_error_is_400_with_span(client):
    r = client.get("/api/search", params={"q": "priya in:intervew"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["span"] == [6, 17] and "interview" in err["hint"]


def test_search_empty_is_explained(client):
    add(client, "A")
    r = client.get("/api/search", params={"q": "in:offer"}).json()
    assert r["count"] == 0 and "Offer" in r["empty"]["message"]


def test_bad_timezone_falls_back_to_utc(client):
    assert client.get("/api/search", params={"q": "", "tz": "Mars/Base"}).json()["timezone"] == "UTC"


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
