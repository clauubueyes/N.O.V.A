import pytest


@pytest.fixture(autouse=True)
def isolate_installation_state(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_HOME", str(tmp_path / "nova-home"))
    monkeypatch.delenv("NOVA_CONFIG", raising=False)
