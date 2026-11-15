import pytest

from scripts.setup_database import ADMIN_COMMAND, check_server


def test_administrator_commands_target_explicit_local_socket():
    assert "--socket=/var/run/mysqld/mysqld.sock" in ADMIN_COMMAND
    assert "--protocol=socket" in ADMIN_COMMAND


def test_server_guard_allows_only_expected_instance():
    check_server("8.4.13", 3307)
    for version, port in (("8.0.46", 3307), ("9.7.0", 3307), ("8.4.13", 3306)):
        with pytest.raises(SystemExit):
            check_server(version, port)
