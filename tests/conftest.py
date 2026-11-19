import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Tests never touch the user's private learned dictionary or make real Copilot calls."""
    monkeypatch.setenv("RTT_LEARNED_CORRECTIONS", "none")
    monkeypatch.setenv("RTT_VOCABULARY", "none")      # tests opt in with their own term files
    import meetalign.ai

    def _no_network(prompt, model=None):
        raise RuntimeError("real Copilot call blocked in tests; monkeypatch meetalign.ai.copilot_call")
    monkeypatch.setattr(meetalign.ai, "copilot_call", _no_network)
