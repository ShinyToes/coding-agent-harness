"""repo-lock-ttl：锁 TTL 回归的契约测试。"""

from __future__ import annotations

from harness.store import Store


def make_store(tmp_path):
    return Store(tmp_path / "harness.db")


def test_expired_lock_can_be_taken_over(tmp_path):
    store = make_store(tmp_path)
    assert store.acquire_lock("s1", "owner-a", ttl_seconds=0) is True
    assert store.acquire_lock("s1", "owner-b", ttl_seconds=0) is True
    assert store.lock_holder("s1") == "owner-b"


def test_fresh_lock_still_blocks_other_owner(tmp_path):
    store = make_store(tmp_path)
    assert store.acquire_lock("s1", "owner-a", ttl_seconds=3600) is True
    assert store.acquire_lock("s1", "owner-b", ttl_seconds=3600) is False
    assert store.lock_holder("s1") == "owner-a"


def test_same_owner_can_reacquire(tmp_path):
    store = make_store(tmp_path)
    assert store.acquire_lock("s1", "owner-a", ttl_seconds=3600) is True
    assert store.acquire_lock("s1", "owner-a", ttl_seconds=3600) is True


def test_release_frees_lock_for_others(tmp_path):
    store = make_store(tmp_path)
    assert store.acquire_lock("s1", "owner-a", ttl_seconds=3600) is True
    store.release_lock("s1", "owner-a")
    assert store.lock_holder("s1") is None
    assert store.acquire_lock("s1", "owner-b", ttl_seconds=3600) is True
