from tests.test_paste import _setup


def test_backend_picker_arrows_and_enter(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    k.handle(b"b")
    assert "[openai]" in ui.prompt                      # current backend preselected
    k.handle(b"\x1b[B"); k.handle(b"\x1b[C")            # down, right
    assert "[mai]" in ui.prompt
    k.handle(b"\x1b[A")                                 # up
    assert "[llmspeech]" in ui.prompt
    k.handle(b"\r")
    assert ui.prompt is None and s.take_switch_request() == "llmspeech"


def test_backend_picker_wraps_and_typing_still_works(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    k.handle(b"b"); k.handle(b"\x1b[D")                 # left from first wraps to last
    assert "[local]" in ui.prompt
    k.handle(b"v")                                      # typed prefix moves highlight
    assert "[vibe]" in ui.prompt
    k.handle(b"\r")
    assert s.take_switch_request() == "vibe"
    k.handle(b"b"); k.handle(b"\x1b[B"); k.handle(b"\x1b\x1b")   # Esc cancels
    assert ui.prompt is None and s.take_switch_request() is None


def test_arrows_still_scroll_in_text_prompts(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    k.handle(b"M"); k.handle(b"\x1b[A")
    assert ui.prompt.startswith("Mark note: ")
