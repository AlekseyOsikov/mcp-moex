import os
import socket

import pytest

from .fake_iss import RECORD_ENV


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """Тесты работают только на фикстурах: любая попытка сетевого соединения падает.

    В режиме записи фикстур (`RECORD_FIXTURES=1`) защита выключена.
    """
    if os.environ.get(RECORD_ENV) == "1":
        return

    def blocked(self, address, *args, **kwargs):
        raise RuntimeError(f"Тест пытался открыть сетевое соединение: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
