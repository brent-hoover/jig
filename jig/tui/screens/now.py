from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class NowScreen(Screen):
    """The active-interaction screen — scrolling transcript with
    structured prompts inline. Idle implementation in v1; full
    implementation in Phase 3."""

    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Now — idle\n\n(Phase 3 will add the transcript and input)")
