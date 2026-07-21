from __future__ import annotations

from .diarize import SpeakerSegment


def render_speakers_markdown(transcript: dict[str, object], speakers: list[SpeakerSegment], names: dict[str, str]) -> str:
    lines = ["# 发言人转写", ""]
    for start, _end, name, text in _iter_rows(transcript, speakers, names):
        lines.append(f"[{_timestamp(start, '.')}] {name}: {text}")
    return "\n".join(lines) + "\n"


def render_speakers_srt(transcript: dict[str, object], speakers: list[SpeakerSegment], names: dict[str, str]) -> str:
    blocks: list[str] = []
    for index, (start, end, name, text) in enumerate(_iter_rows(transcript, speakers, names), 1):
        blocks.append(f"{index}\n{_timestamp(start, ',')} --> {_timestamp(end, ',')}\n{name}: {text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _iter_rows(transcript: dict[str, object], speakers: list[SpeakerSegment], names: dict[str, str]):
    """生成 (start, end, name, text) 行；有词级说话人时按词拆分，否则按段级 fallback。"""
    for segment in _segments(transcript):
        words = segment.get("words")
        if isinstance(words, list) and words and all(isinstance(word, dict) and "speaker" in word for word in words):
            yield from _word_rows(words, names)
        else:
            speaker = _speaker_for(segment, speakers)
            name = names.get(speaker, speaker) if speaker is not None else "未知发言人"
            yield (float(segment["start"]), float(segment["end"]), name, _text(segment))


def _word_rows(words: list[dict[str, object]], names: dict[str, str]):
    """相邻同说话人的词合并为一行，说话人变化时拆分。"""
    current_speaker: str | None = None
    current_words: list[dict[str, object]] = []
    for word in words:
        speaker = word.get("speaker")
        if speaker != current_speaker:
            if current_words:
                yield _word_row(current_speaker, current_words, names)
            current_speaker = speaker
            current_words = [word]
        else:
            current_words.append(word)
    if current_words:
        yield _word_row(current_speaker, current_words, names)


def _word_row(speaker: str | None, words: list[dict[str, object]], names: dict[str, str]) -> tuple[float, float, str, str]:
    name = names.get(speaker, speaker) if speaker is not None else "未知发言人"
    start = float(words[0].get("start", 0.0))
    end = float(words[-1].get("end", start))
    text = "".join(str(word.get("word", word.get("text", ""))) for word in words).strip()
    return (start, end, name, text)


def _segments(transcript: dict[str, object]) -> list[dict[str, object]]:
    segments = transcript.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("转写结果中的 segments 格式异常")
    return [segment for segment in segments if isinstance(segment, dict) and "start" in segment and "end" in segment]


def _text(segment: dict[str, object]) -> str:
    return str(segment.get("text", "")).strip()


def _speaker_for(segment: dict[str, object], speakers: list[SpeakerSegment]) -> str | None:
    """段级 fallback：选重叠时间最长的说话人；仅在缺少词级时间戳时使用。"""
    start, end = float(segment["start"]), float(segment["end"])
    best = max(speakers, key=lambda item: max(0.0, min(end, item["end"]) - max(start, item["start"])), default=None)
    return best["speaker"] if best is not None and min(end, best["end"]) > max(start, best["start"]) else None


def _timestamp(value: object, decimal: str) -> str:
    milliseconds = max(0, round(float(value) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{decimal}{milliseconds:03}"
