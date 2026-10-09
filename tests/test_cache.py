import asyncio
import importlib

import pytest


def cache(**kwargs):
    return importlib.import_module("marketplace_mcp.cache").TTLCache(**kwargs)


@pytest.mark.asyncio
async def test_cache_size_excludes_expired_entries_without_lookup():
    c = cache(ttl=0.005)

    async def fetch():
        return "ok"

    assert await c.get_or_fetch("x", fetch) == "ok"
    assert c.size == 1
    await asyncio.sleep(0.02)
    assert c.size == 0
    assert c.inflight_size == 0


@pytest.mark.asyncio
async def test_cache_coalesces_same_key_and_defends_mutation():
    c = cache(ttl=60, max_entries=64)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"value": [1]}

    a, b = await asyncio.gather(c.get_or_fetch("x", fetch), c.get_or_fetch("x", fetch))
    a["value"].append(2)
    assert b == {"value": [1]}
    assert await c.get_or_fetch("x", fetch) == {"value": [1]}
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_bounds_completed_and_pending_entries_and_expiry():
    c = cache(ttl=0.01, max_entries=2)

    async def fetch():
        await asyncio.sleep(0.005)
        return "ok"

    await asyncio.gather(*(c.get_or_fetch(str(i), fetch) for i in range(5)))
    assert c.size <= 2 and c.inflight_size == 0
    await asyncio.sleep(0.02)
    called = False

    async def fresh():
        nonlocal called
        called = True
        return "new"

    assert await c.get_or_fetch("4", fresh) == "new" and called


@pytest.mark.asyncio
async def test_cache_failure_is_not_cached_and_cancelled_waiter_does_not_cancel_work():
    c = cache()

    async def fail():
        raise ValueError("test")

    with pytest.raises(ValueError):
        await c.get_or_fetch("a", fail)

    async def good():
        await asyncio.sleep(0.02)
        return "ok"

    task = asyncio.create_task(c.get_or_fetch("a", good))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await c.get_or_fetch("a", good) == "ok"
    assert c.inflight_size == 0


@pytest.mark.asyncio
async def test_combined_cache_and_inflight_size_never_exceeds_bound():
    c = cache(ttl=60, max_entries=2)
    observed = []
    original = c._entries

    class Observed(type(original)):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            observed.append(c.size + c.inflight_size)

    c._entries = Observed()

    async def fetch():
        await asyncio.sleep(0.001)
        return "ok"

    await asyncio.gather(*(c.get_or_fetch(str(i), fetch) for i in range(5)))
    assert max(observed) <= 2
