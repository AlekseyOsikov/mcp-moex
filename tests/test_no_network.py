import socket

import pytest


def test_real_network_is_blocked_in_tests():
    with socket.socket() as sock:
        with pytest.raises(RuntimeError, match="сетевое соединение"):
            sock.connect(("85.118.181.24", 443))
