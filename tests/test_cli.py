import os

from lab_agent_safety.cli import load_env


def test_load_env_reads_dotenv_from_cwd_upwards(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LAS_TEST_VAR=from-file\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("LAS_TEST_VAR", raising=False)
    assert load_env() == str(tmp_path / ".env")
    assert os.environ["LAS_TEST_VAR"] == "from-file"
    monkeypatch.delenv("LAS_TEST_VAR")


def test_shell_variable_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LAS_TEST_VAR=from-file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LAS_TEST_VAR", "from-shell")
    load_env()
    assert os.environ["LAS_TEST_VAR"] == "from-shell"


def test_chat_refuses_cli_approver_without_a_terminal(monkeypatch):
    import pytest
    from lab_agent_safety.cli import main

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        main(["chat", "do something", "--approver", "cli"])
    assert "interactive terminal" in str(exc.value)
