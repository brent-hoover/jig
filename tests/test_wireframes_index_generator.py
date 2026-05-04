"""Tests for the wireframes index generator + ``jig serve`` CLI (Track D MVP)."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli
from jig.wireframes.format import WireframeMeta, render_meta_comment
from jig.wireframes.index_generator import (
    DEFAULT_STATE_OPTIONS,
    generate_index,
)


def _wireframe(screen_id: str, title: str, persona: str = "merchant") -> str:
    meta = render_meta_comment(
        WireframeMeta(
            screen_id=screen_id,
            title=title,
            persona_targets=[persona],
            journey_refs=[f"j-{persona}-onboarding"],
        )
    )
    return f"{meta}\n<html><body><h1>{title}</h1></body></html>\n"


class TestEmptyDir:
    def test_empty_dir_renders_placeholder(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "wf"
        wf_dir.mkdir()
        out = generate_index(wf_dir)
        assert "<!doctype html>" in out
        assert "No wireframes authored yet" in out

    def test_missing_dir_renders_placeholder(self, tmp_path: Path) -> None:
        out = generate_index(tmp_path / "nope")
        assert "No wireframes authored yet" in out


class TestPopulatedDir:
    def test_lists_every_html_file_alphabetically(self, tmp_path: Path) -> None:
        (tmp_path / "signup.html").write_text(_wireframe("signup", "Sign up"))
        (tmp_path / "post-a-job.html").write_text(
            _wireframe("post-a-job", "Post a job")
        )
        out = generate_index(tmp_path)
        assert 'src="post-a-job.html"' in out
        assert 'src="signup.html"' in out
        # Stable ordering: filename-sort means post-a-job comes before signup.
        assert out.index("post-a-job.html") < out.index("signup.html")

    def test_pulls_title_from_meta(self, tmp_path: Path) -> None:
        (tmp_path / "signup.html").write_text(
            _wireframe("signup", "Sign up form")
        )
        out = generate_index(tmp_path)
        assert "Sign up form" in out

    def test_falls_back_to_filename_when_meta_missing(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "broken.html").write_text("<html><body>no meta</body></html>")
        out = generate_index(tmp_path)
        assert "broken" in out

    def test_falls_back_to_filename_when_meta_invalid(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "broken.html").write_text(
            "<!-- wireframe-meta: {bad json} -->\n<html></html>"
        )
        out = generate_index(tmp_path)
        # Stem ``broken`` shows up rather than the (unparseable) meta title.
        assert "broken" in out

    def test_excludes_index_html_itself(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<html><body>old index</body></html>")
        (tmp_path / "signup.html").write_text(_wireframe("signup", "Sign up"))
        out = generate_index(tmp_path)
        # index.html shouldn't appear as an iframe (would create infinite loop)
        assert 'src="index.html"' not in out

    def test_excludes_non_html_files(self, tmp_path: Path) -> None:
        (tmp_path / "wireframe.css").write_text("body { color: #888; }")
        (tmp_path / "signup.html").write_text(_wireframe("signup", "Sign up"))
        out = generate_index(tmp_path)
        assert 'src="wireframe.css"' not in out
        assert 'src="signup.html"' in out


class TestStateToggles:
    def test_emits_state_toggle_buttons(self, tmp_path: Path) -> None:
        (tmp_path / "signup.html").write_text(_wireframe("signup", "Sign up"))
        out = generate_index(tmp_path)
        for state in DEFAULT_STATE_OPTIONS:
            assert f'data-state="{state}"' in out

    def test_includes_inline_js_for_toggle(self, tmp_path: Path) -> None:
        (tmp_path / "signup.html").write_text(_wireframe("signup", "Sign up"))
        out = generate_index(tmp_path)
        # Vanilla JS — no framework load — that updates the iframe hash.
        assert "<script>" in out
        assert "iframe" in out


class TestServeCli:
    def test_serve_errors_when_wireframes_dir_missing(
        self, tmp_path: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["serve", "--path", str(tmp_path), "--port", "0"]
        )
        # Path exists but no .jig/spec/wireframes/ — should error.
        assert result.exit_code != 0
        assert "wireframes dir not found" in result.output

    def test_serve_regenerates_index_html(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        wf = tmp_path / ".jig" / "spec" / "wireframes"
        wf.mkdir(parents=True)
        (wf / "signup.html").write_text(_wireframe("signup", "Sign up"))

        # Stub out socketserver.TCPServer so we don't actually bind.
        # ``serve_forever`` is the blocking call; we want to exit
        # before reaching it. Simplest: monkeypatch socketserver.TCPServer
        # to raise after construction so the regenerate path runs but
        # the server doesn't.
        from jig import cli as cli_module

        class _FakeServer:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                raise KeyboardInterrupt

            def __exit__(self, *args):
                pass

        # The import is inside the command body; patch the lookup target.
        import socketserver as ss

        monkeypatch.setattr(ss, "TCPServer", _FakeServer)
        # Re-importing socketserver inside the handler picks up the
        # patched class because the module reference is the same.
        del cli_module  # quiet unused

        runner = CliRunner()
        # KeyboardInterrupt during __enter__ surfaces through Click's
        # standalone_mode default; we mostly care that the index file got
        # regenerated. The invocation result itself is discarded — we
        # gate on the side-effect (index.html on disk).
        runner.invoke(cli, ["serve", "--path", str(tmp_path), "--port", "0"])
        assert (wf / "index.html").is_file()
        idx_text = (wf / "index.html").read_text()
        assert "signup" in idx_text
