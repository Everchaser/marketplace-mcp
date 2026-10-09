"""Bounded TTL cache with coalesced, cancellation-safe work."""

import asyncio
import copy
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, ttl=60.0, max_entries=64):
        if ttl < 0 or not 1 <= max_entries <= 64:
            raise ValueError("Invalid cache bounds")
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._inflight = {}
        self._condition = asyncio.Condition()

    @property
    def size(self):
        self._purge_expired()
        return len(self._entries)

    def _purge_expired(self):
        now = time.monotonic()
        for key in list(self._entries):
            if self._entries[key][0] <= now:
                del self._entries[key]

    @property
    def inflight_size(self):
        return len(self._inflight)

    async def get_or_fetch(self, key, fetch):
        async with self._condition:
            while True:
                self._purge_expired()
                if key in self._entries:
                    self._entries.move_to_end(key)
                    return copy.deepcopy(self._entries[key][1])
                if key in self._inflight:
                    task = self._inflight[key]
                    break
                if len(self._entries) + len(self._inflight) >= self.max_entries:
                    if self._entries:
                        self._entries.popitem(last=False)
                    else:
                        await self._condition.wait()
                        continue
                task = asyncio.create_task(self._load(key, fetch))
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                self._inflight[key] = task
                break
        return copy.deepcopy(await asyncio.shield(task))

    async def _load(self, key, fetch):
        loaded = False
        value = None
        try:
            value = await fetch()
            loaded = True
            return value
        finally:
            async with self._condition:
                self._inflight.pop(key, None)
                if loaded and self.ttl:
                    self._entries[key] = (time.monotonic() + self.ttl, copy.deepcopy(value))
                self._condition.notify_all()
