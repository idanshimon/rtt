# Third-party software and models

The MIT license in [LICENSE](LICENSE) covers first-party code only. Nothing below is stored
in this repository; it is installed by `pip`, the installers or `scripts/get-speaker-model.sh`,
and each keeps its own license.

## Python packages (`requirements.txt`)

| Package | License | Used for |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | Local Whisper backend |
| [PyAV](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause | Decoding audio files |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) | MIT | Live audio capture (PortAudio) |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause | FLAC/WAV recording |
| [NumPy](https://numpy.org) | BSD-3-Clause | Audio buffers |
| [Typer](https://github.com/fastapi/typer), [Rich](https://github.com/Textualize/rich) | MIT | CLI and split-view terminal UI |
| [Azure Speech SDK](https://pypi.org/project/azure-cognitiveservices-speech/) | Microsoft Software License Terms (not open source) | `azure` backend |
| [azure-identity](https://github.com/Azure/azure-sdk-for-python) | MIT | Azure AD sign-in |
| [Requests](https://github.com/psf/requests) | Apache-2.0 | Chunked REST backends |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Apache-2.0 | Speaker embeddings |

## Models and drivers

| Item | License | How RTT gets it |
|---|---|---|
| NVIDIA NeMo TitaNet-small speaker model (`nemo_en_titanet_small.onnx`) | CC-BY-4.0 (NVIDIA) | `scripts/get-speaker-model.sh` downloads it from the sherpa-onnx releases into the git-ignored `models/` |
| Whisper model weights | MIT (OpenAI) | Downloaded by faster-whisper on first use |
| [BlackHole](https://github.com/ExistentialAudio/BlackHole) virtual audio driver (macOS) | GPL-3.0 | Installed by `install/macos.sh` through Homebrew; RTT only reads audio from it |

## Cloud services

The `openai`, `llmspeech`, `mai` and `azure` backends call Azure services in **your own** Azure
subscription. Their terms and pricing are Microsoft's. The optional live summary calls GitHub
Copilot with your own `gh` sign-in.
