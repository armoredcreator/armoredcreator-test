import asyncio

from ArmoredSync.service import TelegramReader


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeClient:
    def __init__(self, *, fail_start=False):
        self.fail_start = fail_start
        self.connected = False
        self.disconnect_calls = 0
        self.session = _FakeSession()

    def is_connected(self):
        return self.connected

    async def start(self):
        if self.fail_start:
            raise ConnectionError("telegram unavailable")
        self.connected = True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False


def _reader(factory):
    reader = TelegramReader.__new__(TelegramReader)
    reader._session = __import__("pathlib").Path("test-session")
    reader._api_id = 1
    reader._api_hash = "hash"
    reader._TelegramClient = factory
    reader.client = factory()
    return reader


def test_failed_telegram_start_closes_session_before_next_reconnect():
    clients = [
        _FakeClient(fail_start=True),
        _FakeClient(fail_start=True),
        _FakeClient(fail_start=False),
    ]

    def factory(*_args):
        return clients.pop(0)

    reader = _reader(factory)

    async def scenario():
        first_error = None
        try:
            await reader.connect()
        except ConnectionError as exc:
            first_error = exc
        assert first_error is not None
        assert clients is not None
        await reader.connect()

    asyncio.run(scenario())

    failed_client = clients  # retained only to make the test body explicit
    assert reader.client.is_connected() is True


def test_disconnect_closes_session_even_when_client_reports_not_connected():
    reader = TelegramReader.__new__(TelegramReader)
    reader.client = _FakeClient()
    reader.client.session = _FakeSession()

    asyncio.run(reader.disconnect())

    assert reader.client.disconnect_calls == 1
    assert reader.client.session.closed is True
