import json

from rbx.cli import main, parser
from rbx.common import Problem, locked


def test_json_error_and_flags_after_command(tmp_path, capsys):
    code = main(["status", "--json", "--no-input", "--library", str(tmp_path / "absent")])
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["schema_version"] == 1
    assert output["error"]["code"] == "not-configured"


def test_invalid_arguments_are_json(capsys):
    assert main(["issues", "resolve", "abc", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid-arguments"


def test_status_machine_output(library, capsys):
    assert main(["--library", str(library.root), "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["tracks"] == 0


def test_exclusive_library_lock(tmp_path):
    with locked(tmp_path):
        try:
            with locked(tmp_path):
                raise AssertionError("Acquired duplicate lock")
        except Problem as exc:
            assert exc.code == "busy"


def test_reconcile_cli_supports_review_bulk_approval_and_explicit_matches():
    args = parser().parse_args(
        [
            "reconcile",
            "Weekend digging",
            "--apply",
            "--accept-suggestions",
            "--match",
            "soundcloud:one=rb-content-1",
            "--json",
            "--no-input",
        ]
    )

    assert args.command == "reconcile"
    assert args.playlist == "Weekend digging"
    assert args.apply is True
    assert args.accept_suggestions is True
    assert args.match == ["soundcloud:one=rb-content-1"]
