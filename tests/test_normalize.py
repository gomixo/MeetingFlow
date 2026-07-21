from __future__ import annotations

import json
import subprocess
from pathlib import Path

from meetingflow.audio import normalize_audio


def test_normalize_audio_produces_16k_mono_wav(tmp_path: Path) -> None:
    source = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.5", str(source)], check=True)
    destination = tmp_path / "audio-16k-mono.wav"

    result_path, max_volume = normalize_audio(source, destination)

    assert result_path == destination
    assert destination.is_file()
    assert max_volume is not None
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(destination)], capture_output=True, text=True, check=True
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["sample_rate"] == "16000"
    assert stream["channels"] == 1


def test_normalize_audio_no_temporary_leftover_on_success(tmp_path: Path) -> None:
    source = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.3", str(source)], check=True)
    destination = tmp_path / "audio-16k-mono.wav"

    normalize_audio(source, destination)

    assert destination.is_file()
    assert not (tmp_path / ".audio-16k-mono.wav.tmp").exists()
