"""The ``flipradar`` CLI end to end against a temporary database."""

import pytest

from flipradar import cli
from flipradar.config import reset_settings_cache


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("FLIPRADAR_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("FLIPRADAR_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("FLIPRADAR_FX_PUBLIC_SOURCES_ENABLED", "false")  # hermetic: no public FX calls
    monkeypatch.delenv("FLIPRADAR_BANXICO_TOKEN", raising=False)
    for name in ("FLIPRADAR_BANXICO_TOKEN", "FLIPRADAR_FX_USD_MXN_MANUAL"):
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    yield tmp_path
    reset_settings_cache()


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_init_and_token_lifecycle(cli_env, capsys):
    code, out, _ = run(capsys, "init")
    assert code == 0 and "Database ready" in out and (cli_env / "cli.db").exists()
    code, out, _ = run(capsys, "create-token", "--name", "telefono")
    assert code == 0
    token = out.strip().splitlines()[-1]
    assert token.startswith("fr_")
    code, out, _ = run(capsys, "list-tokens")
    assert code == 0 and "telefono" in out and token[:8] in out and token not in out
    code, out, _ = run(capsys, "revoke-token", "--prefix", token[:8])
    assert code == 0 and "Revoked 1" in out
    code, _, err = run(capsys, "revoke-token", "--prefix", token[:8])
    assert code == 1 and "No active token" in err
    code, _, err = run(capsys, "create-token", "--name", " ")
    assert code == 2


@pytest.mark.usefixtures("fake_pipeline")
def test_import_recompute_and_demo(cli_env, capsys):
    csv_path = cli_env / "precios.csv"
    csv_path.write_text("title,price,brand,model,condition\n" + "".join(
        f"iPhone 15 Pro usado {i},{p},Apple,iPhone 15 Pro,used\n" for i, p in enumerate([15000, 15500, 16000, 9500])
    ), encoding="utf-8")
    code, out, _ = run(capsys, "import-csv", str(csv_path), "--source", "tianguis")
    assert code == 0 and "imported 4" in out
    code, out, _ = run(capsys, "recompute")
    assert code == 0 and "Products analysed: 1" in out
    code, _, err = run(capsys, "import-csv", str(cli_env / "missing.csv"))
    assert code == 2 and "not found" in err
    bad = cli_env / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    code, _, err = run(capsys, "import-csv", str(bad))
    assert code == 2 and "Import failed" in err

    code, out, _ = run(capsys, "seed-demo")
    assert code == 0 and "SYNTHETIC" in out and "products 6" in out
    code, out, _ = run(capsys, "clear-demo")
    assert code == 0 and "products 6" in out


@pytest.mark.usefixtures("fake_pipeline")
def test_collect_and_searches(cli_env, capsys, client, fake_collectors):
    # ``client``/``fake_collectors`` register the fake collector; the CLI bootstraps its own DB rows.
    code, out, _ = run(capsys, "collect", "iphone 15 pro", "--source", "fakesource", "--limit", "10")
    assert code == 0 and "fakesource: fetched 6, new 6" in out
    code, out, _ = run(capsys, "collect", "iphone", "--source", "brokensource")
    assert code == 1 and "HTTP 503" in out
    code, out, _ = run(capsys, "run-searches")
    assert code == 0 and "Ran 0" in out
    with pytest.raises(SystemExit):
        cli.main(["collect", "x", "--limit", "500"])


def test_fx_refresh_requires_a_source(cli_env, capsys):
    code, _, err = run(capsys, "fx-refresh")
    assert code == 1 and "BANXICO" in err


def test_serve_refuses_public_host_without_auth(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("FLIPRADAR_REQUIRE_AUTH", "false")
    reset_settings_cache()
    code, _, err = run(capsys, "serve", "--host", "0.0.0.0")
    assert code == 2 and "Refusing" in err
    called = {}

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: called.update(args=a, kwargs=k))
    code, _, _ = run(capsys, "serve", "--host", "127.0.0.1", "--port", "8123")
    assert code == 0 and called["args"] == ("flipradar.main:get_app",)
    assert called["kwargs"]["factory"] is True and called["kwargs"]["port"] == 8123


def test_python_dash_m_entrypoint(cli_env):
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-m", "flipradar", "--version"], cwd=backend, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0 and "flipradar 0.1.0" in result.stdout
