import pytest

from database import database_config


def test_missing_database_config_fails_explicitly(monkeypatch):
    for key in ("DB_NAME", "DB_USER", "DB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="DB_NAME"):
        database_config()


def test_local_defaults_and_password_is_not_trimmed(monkeypatch):
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.delenv("DB_PORT", raising=False)
    monkeypatch.setenv("DB_NAME", "studyflow")
    monkeypatch.setenv("DB_USER", "studyflow_app")
    monkeypatch.setenv("DB_PASSWORD", " keep spaces ")
    config = database_config()
    assert config["host"] == "127.0.0.1"
    assert config["port"] == 3307
    assert config["password"] == " keep spaces "
