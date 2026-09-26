import os
import stat

import pytest

from wsdarr.__main__ import main
from wsdarr.db import Database


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("WSDARR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    return tmp_path


def test_apikey_from_database(env, capsys):
    db = Database(env / "wsdarr.db")
    db.kv_set("api_key", "abc123")
    assert main(["apikey"]) == 0
    assert capsys.readouterr().out.strip() == "abc123"
    db.close()


def test_apikey_from_environment(env, monkeypatch, capsys):
    monkeypatch.setenv("WSDARR_API_KEY", "fromenv")
    assert main(["apikey"]) == 0
    assert capsys.readouterr().out.strip() == "fromenv"


def test_apikey_before_first_start(env, capsys):
    assert main(["apikey"]) == 1
    assert "start wsdarr first" in capsys.readouterr().err
    # Must not create the database (it would be owned by root inside the container).
    assert not (env / "wsdarr.db").exists()


def test_apikey_does_not_write(env, capsys):
    db = Database(env / "wsdarr.db")
    db.kv_set("api_key", "abc123")
    db.close()
    before = {p.name: p.stat().st_mtime_ns for p in env.iterdir()}
    os.chmod(env / "wsdarr.db", stat.S_IRUSR)
    try:
        assert main(["apikey"]) == 0
    finally:
        os.chmod(env / "wsdarr.db", stat.S_IRUSR | stat.S_IWUSR)
    assert {p.name: p.stat().st_mtime_ns for p in env.iterdir()} == before


def test_apikey_while_server_running(env, capsys):
    # Server holds the database open (WAL mode, key possibly not checkpointed yet).
    db = Database(env / "wsdarr.db")
    db.kv_set("api_key", "live-key")
    assert main(["apikey"]) == 0
    assert capsys.readouterr().out.strip() == "live-key"
    db.close()
