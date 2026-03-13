from pathlib import Path

from fastapi.testclient import TestClient

from nanobot.config.schema import QQChatCompatConfig
from nanobot.qqchat_compat import CHANNEL
from nanobot.qqchat_compat.server import create_app


def _client() -> TestClient:
    repo_root = Path(__file__).resolve().parents[1]
    app = create_app(QQChatCompatConfig(), repo_root)
    return TestClient(app)


def test_query_returns_need_search() -> None:
    client = _client()
    payload = {
        "query": "查一下张三最近说了什么",
        "session_id": "s1",
        "user_uin": "10001",
    }

    resp = client.post("/query", json=payload)
    body = resp.json()

    assert resp.status_code == 200
    assert body["status"] == "need_search"
    assert body["channel"] == CHANNEL
    assert body["need_search"] is True
    assert len(body["mcp_calls"]) >= 1


def test_submit_without_session_returns_404() -> None:
    client = _client()
    payload = {
        "session_id": "no-session",
        "user_uin": "10001",
        "search_results": [],
    }

    resp = client.post("/submit_search_results", json=payload)

    assert resp.status_code == 404


def test_query_then_submit_returns_final_answer() -> None:
    client = _client()
    q_payload = {
        "query": "总结最近项目群讨论",
        "session_id": "s2",
        "user_uin": "10002",
    }
    s_payload = {
        "session_id": "s2",
        "user_uin": "10002",
        "search_results": [
            {
                "tool": "search_messages",
                "result": {"content": "项目群里今天讨论了发布计划和回归安排"},
            }
        ],
    }

    query_resp = client.post("/query", json=q_payload)
    submit_resp = client.post("/submit_search_results", json=s_payload)
    body = submit_resp.json()

    assert query_resp.status_code == 200
    assert submit_resp.status_code == 200
    assert body["status"] == "final_answer"
    assert body["channel"] == CHANNEL
    assert "项目群" in body["final_answer"]


def test_health_includes_channel() -> None:
    client = _client()
    resp = client.get("/health")
    body = resp.json()

    assert resp.status_code == 200
    assert body["channel"] == CHANNEL


def test_session_keys_are_channel_prefixed() -> None:
    """Session keys must be namespaced under qqchat_http to avoid cross-channel collision."""
    client = _client()
    payload = {
        "query": "测试channel隔离",
        "session_id": "iso1",
        "user_uin": "99999",
    }
    client.post("/query", json=payload)

    session_resp = client.get("/session/99999/iso1")
    body = session_resp.json()

    assert session_resp.status_code == 200
    assert body["key"].startswith(f"{CHANNEL}:")
    assert body["channel"] == CHANNEL
