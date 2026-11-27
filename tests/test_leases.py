"""Leases and fencing (Mission 003).

Real SQLite, real threads, real separate connections: the point of a lease is that it holds
under concurrency, so nothing here is mocked.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from jarvis.leases import DEFAULT_TTL_SECONDS, LeaseError, LeaseStore, MAX_TTL_SECONDS, MIN_TTL_SECONDS
from jarvis.util import to_iso


@pytest.fixture
def store(app_factory):
    return app_factory().store


@pytest.fixture
def leases(store):
    return LeaseStore(store)


def _expire(store, resource: str) -> None:
    """Move a lease's expiry into the past (a crashed worker's lease, deterministically)."""
    with store.write() as db:
        db.execute("UPDATE objective_leases SET expires_at = ? WHERE resource = ?",
                   (to_iso(store.now() - timedelta(seconds=1)), resource))


def test_acquire_is_exclusive_and_returns_a_fencing_token(leases):
    first = leases.acquire("obj_1", owner="worker-a", ttl=60)
    assert first is not None and first.owner == "worker-a" and first.token == 1
    assert leases.acquire("obj_1", owner="worker-b", ttl=60) is None
    assert leases.held_by("obj_1").owner == "worker-a"
    assert leases.is_held_by(first)


def test_reacquiring_as_the_same_owner_returns_the_same_lease(leases):
    first = leases.acquire("obj_1", owner="worker-a", ttl=60)
    again = leases.acquire("obj_1", owner="worker-a", ttl=60)
    assert again is not None and again.token == first.token == 1


def test_expired_lease_can_be_taken_over_and_the_token_increases(store, leases):
    stale = leases.acquire("obj_1", owner="worker-a", ttl=60)
    _expire(store, "obj_1")
    fresh = leases.acquire("obj_1", owner="worker-b", ttl=60)
    assert fresh is not None and fresh.token == stale.token + 1
    # The stale worker can no longer prove it owns the objective.
    assert leases.renew(stale) is False
    assert leases.is_held_by(stale) is False
    assert leases.release(stale) is False
    assert leases.is_held_by(fresh) is True


def test_renew_extends_a_live_lease_and_requires_ownership(leases):
    lease = leases.acquire("obj_1", owner="worker-a", ttl=30)
    assert leases.renew(lease, ttl=600) is True
    assert leases.held_by("obj_1").expires_at >= lease.expires_at
    impostor = replace(lease, owner="worker-b")
    assert leases.renew(impostor) is False
    wrong_token = replace(lease, token=lease.token + 5)
    assert leases.renew(wrong_token) is False


def test_release_frees_the_resource(leases):
    lease = leases.acquire("obj_1", owner="worker-a", ttl=60)
    assert leases.release(lease) is True
    assert leases.held_by("obj_1") is None
    successor = leases.acquire("obj_1", owner="worker-b", ttl=60)
    assert successor is not None and successor.owner == "worker-b"
    # A released lease stays dead: its holder can neither renew it nor claim the resource.
    assert leases.renew(lease) is False and leases.is_held_by(lease) is False
    assert leases.is_held_by(successor) is True


def test_purge_expired_removes_only_dead_leases(store, leases):
    dead = leases.acquire("obj_dead", owner="worker-a", ttl=60)
    alive = leases.acquire("obj_alive", owner="worker-a", ttl=60)
    _expire(store, "obj_dead")
    assert leases.purge_expired() == 1
    assert leases.held_by("obj_dead") is None and leases.is_held_by(alive)


@pytest.mark.parametrize("ttl", [0, -5, MIN_TTL_SECONDS - 1, MAX_TTL_SECONDS + 1, 10_000])
def test_ttl_is_bounded(leases, ttl):
    with pytest.raises(LeaseError):
        leases.acquire("obj_1", owner="w", ttl=ttl)


def test_default_ttl_is_used_when_none_is_given(store, leases):
    lease = leases.acquire("obj_1", owner="w")
    expires = to_iso(store.now() + timedelta(seconds=DEFAULT_TTL_SECONDS))
    assert lease.expires_at <= expires


def test_resource_name_is_required(leases):
    with pytest.raises(LeaseError):
        leases.acquire("", owner="w")


def test_structural_namespaces_are_leases_too(leases):
    """A coarse namespace (e.g. one scheduler tick) can be leased without an objective row."""
    assert leases.acquire("scheduler:tick", owner="a", ttl=30) is not None
    assert leases.acquire("scheduler:tick", owner="b", ttl=30) is None


def test_concurrent_acquire_yields_exactly_one_winner(app_factory):
    app = app_factory()
    winners: list[str] = []
    losers: list[str] = []
    barrier = threading.Barrier(6)

    def worker(name: str) -> None:
        leases = LeaseStore(app.store)                     # a separate lease store per thread
        barrier.wait()
        lease = leases.acquire("obj_race", owner=name, ttl=60)
        (winners if lease and lease.owner == name else losers).append(name)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(winners) == 1, f"expected one winner, got {winners} (losers {losers})"
    assert len(winners) + len(losers) == 6


def test_fencing_prevents_a_stale_worker_writing_after_takeover(app_factory):
    """The point of the token: a worker whose lease expired cannot keep mutating state."""
    app = app_factory()
    leases = LeaseStore(app.store)
    stale = leases.acquire("obj_fence", owner="worker-old", ttl=60)
    _expire(app.store, "obj_fence")
    fresh = leases.acquire("obj_fence", owner="worker-new", ttl=60)
    assert fresh.token > stale.token
    # A well-behaved worker checks before each write; the check fails for the stale holder.
    assert not leases.is_held_by(stale)
    assert leases.is_held_by(fresh)
    # Renewal by token, so an old token can never resurrect ownership.
    assert leases.renew(stale) is False
