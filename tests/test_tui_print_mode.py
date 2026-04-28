from jig.tui.print_mode import run_print


def test_run_print_rejects_non_slash(capsys):
    code = run_print("status")  # missing leading /
    assert code == 2
    err = capsys.readouterr().err
    assert "not a slash command" in err
