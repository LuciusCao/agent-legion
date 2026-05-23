import os

from server.app.settings import load_env_file


def test_load_env_file_preserves_quoted_secret_values(tmp_path, monkeypatch):
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.setenv("BASECMS_TOKEN", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text(
        'BASECMS_TOKEN="from-file"\n'
        'BASECMS_SECRET="fake#secret$value"\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["BASECMS_TOKEN"] == "already-set"
    assert os.environ["BASECMS_SECRET"] == "fake#secret$value"
