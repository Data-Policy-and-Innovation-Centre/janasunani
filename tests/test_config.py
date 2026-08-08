from janasunani.config import Settings


def test_debug_release_env_is_false(monkeypatch):
    monkeypatch.setenv("DEBUG", "release")

    assert Settings().DEBUG is False


def test_debug_truthy_env_is_true(monkeypatch):
    monkeypatch.setenv("DEBUG", "debug")

    assert Settings().DEBUG is True


def test_sarvam_api_key_from_env(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")

    assert Settings().SARVAM_API_KEY == "test-key"
