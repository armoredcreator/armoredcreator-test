import asyncio
import os
import unittest

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


class LiveReconnectWatchdogTests(unittest.TestCase):
    def test_timeout_forces_clean_disconnect_then_next_cycle_reconnects(self):
        previous = os.environ.get("ARMORED_LIVE_DISCOVERY_TIMEOUT")
        os.environ["ARMORED_LIVE_DISCOVERY_TIMEOUT"] = "0.1"
        try:
            coordinator = Coordinator.__new__(Coordinator)
            coordinator.source = _Source()

            async def scenario():
                with self.assertRaises(asyncio.TimeoutError):
                    await coordinator.run_live_once_async()

                self.assertEqual(coordinator.source.calls, 1)
                self.assertEqual(coordinator.source.reader.disconnect_calls, 1)

                result = await coordinator.run_live_once_async()
                self.assertEqual(result, [])
                self.assertEqual(coordinator.source.calls, 2)
                self.assertEqual(coordinator.source.reader.connect_calls, 2)

            asyncio.run(scenario())
        finally:
            if previous is None:
                os.environ.pop("ARMORED_LIVE_DISCOVERY_TIMEOUT", None)
            else:
                os.environ["ARMORED_LIVE_DISCOVERY_TIMEOUT"] = previous


if __name__ == "__main__":
    unittest.main()
