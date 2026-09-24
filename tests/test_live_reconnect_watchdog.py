import asyncio

from armored_core.coordinator import Coordinator


class _Reader:
    def __init__(self):
        self.disconnect_calls = 0
        self.connect_calls = 0
        self.connected = False

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    async def connect(self):
        self.connect_calls += 1
        self.connected = True


class _Source:
    def __init__(self):
        self.reader = _Reader()
        self.calls = 0

    async def fetch_live_candidate_async(self):
        await self.reader.connect()
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(1)
        return None, {}


def test_live_discovery_timeout_forces_clean_disconnect_then_allows_next_cycle(monkeypatch):
    monkeypatch.setenv("ARMORED_LIVE_DISCOVERY_TIMEOUT", "0.1")

    coordinator = Coordinator.__new__(Coordinator)
    coordinator.source = _Source()

    async def scenario():
        try:
            await coordinator.run_live_once_async()
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("LIVE discovery timeout was expected")

        assert coordinator.source.calls == 1
        assert coordinator.source.reader.disconnect_calls == 1

        result = await coordinator.run_live_once_async()
        assert result == []
        assert coordinator.source.calls == 2
        assert coordinator.source.reader.connect_calls == 2

    asyncio.run(scenario())
