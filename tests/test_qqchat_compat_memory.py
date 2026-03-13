from pathlib import Path

from nanobot.qqchat_compat.memory_store import AccountMemoryStore


def test_account_memory_is_isolated(tmp_path: Path) -> None:
    store = AccountMemoryStore(tmp_path)

    store.append_record("10001", "Q1", "A1", 1)
    store.append_record("10002", "Q2", "A2", 1)

    records_a = store.read_records("10001")
    records_b = store.read_records("10002")

    assert len(records_a) == 1
    assert len(records_b) == 1
    assert records_a[0]["query"] == "Q1"
    assert records_b[0]["query"] == "Q2"
