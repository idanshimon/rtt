from chunked_backends import VibeVoiceLocalBackend


def test_format_segments_labels_and_drops_noise_tags():
    segs = [
        {"Start": 0, "End": 1, "Speaker": 0, "Content": "שלום"},
        {"Start": 1, "End": 2, "Speaker": None, "Content": "[Music]"},
        {"Start": 2, "End": 3, "Speaker": 1, "Content": "  "},
        {"Start": 3, "End": 4, "Speaker": None, "Content": "no speaker"},
    ]
    assert VibeVoiceLocalBackend._format_segments(segs) == ["Speaker 0: שלום", "no speaker"]


def test_auth_header_has_no_credentials_and_encodes_prompt():
    b = VibeVoiceLocalBackend(sample_rate=16000, prompt="עמית, NVIDIA")
    h = b._auth_header()
    assert "Authorization" not in h and h["Content-Type"] == "audio/wav"
    assert h["X-Prompt"].isascii()
    assert b._url == "http://127.0.0.1:8765/transcribe"
