from __future__ import annotations

from .diarize import SpeakerSegment


def render_speakers_markdown(transcript: dict[str, object], speakers: list[SpeakerSegment], names: dict[str, str]) -> str:
    lines = ["# 发言人转写", ""]
    for segment in _segments(transcript):
        speaker = _speaker_for(segment, speakers)
        name = names.get(speaker, speaker) if speaker is not None else "未知发言人"
        lines.append(f"[{_timestamp(segment['start'], '.')}] {name}: {_text(segment)}")
    return "\n".join(lines) + "\n"


def render_speakers_srt(transcript: dict[str, object], speakers: list[SpeakerSegment], names: dict[str, str]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(_segments(transcript), 1):
        speaker = _speaker_for(segment, speakers)
        name = names.get(speaker, speaker) if speaker is not None else "未知发言人"
        blocks.append(f"{index}\n{_timestamp(segment['start'], ',')} --> {_timestamp(segment['end'], ',')}\n{name}: {_text(segment)}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _segments(transcript: dict[str, object]) -> list[dict[str, object]]:
    segments = transcript.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("转写结果中的 segments 格式异常")
    return [segment for segment in segments if isinstance(segment, dict) and "start" in segment and "end" in segment]


def _text(segment: dict[str, object]) -> str:
    return str(segment.get("text", "")).strip()


def _speaker_for(segment: dict[str, object], speakers: list[SpeakerSegment]) -> str | None:
    start, end = float(segment["start"]), float(segment["end"])
    best = max(speakers, key=lambda item: max(0.0, min(end, item["end"]) - max(start, item["start"])), default=None)
    return best["speaker"] if best is not None and min(end, best["end"]) > max(start, best["start"]) else None


def _timestamp(value: object, decimal: str) -> str:
    milliseconds = max(0, round(float(value) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{decimal}{milliseconds:03}"
