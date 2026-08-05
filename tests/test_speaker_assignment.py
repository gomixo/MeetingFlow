from __future__ import annotations

import pytest

from meetingflow.analyze import SpeakerSegment, derive_speakers, derive_transcript
from meetingflow.render import render_speakers_markdown


def test_word_level_split_when_speaker_changes_within_segment() -> None:
    transcript = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "你好再见",
                "words": [
                    {"word": "你好", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
                    {"word": "再见", "start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
                ],
            }
        ]
    }
    speakers: list[SpeakerSegment] = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
    ]
    names = {"SPEAKER_00": "张三", "SPEAKER_01": "李四"}

    markdown = render_speakers_markdown(transcript, speakers, names)

    assert "张三: 你好" in markdown
    assert "李四: 再见" in markdown


def test_consecutive_same_speaker_words_merge_into_one_line() -> None:
    transcript = {
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "你好世界再见",
                "words": [
                    {"word": "你好", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
                    {"word": "世界", "start": 1.0, "end": 2.0, "speaker": "SPEAKER_00"},
                    {"word": "再见", "start": 2.0, "end": 3.0, "speaker": "SPEAKER_01"},
                ],
            }
        ]
    }
    speakers: list[SpeakerSegment] = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 3.0, "speaker": "SPEAKER_01"},
    ]
    names = {"SPEAKER_00": "张三", "SPEAKER_01": "李四"}

    markdown = render_speakers_markdown(transcript, speakers, names)

    assert "张三: 你好世界" in markdown
    assert "李四: 再见" in markdown


def test_segment_level_fallback_without_words() -> None:
    transcript = {"segments": [{"start": 0.0, "end": 1.0, "text": "你好"}]}
    speakers: list[SpeakerSegment] = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
    names = {"SPEAKER_00": "张三"}

    markdown = render_speakers_markdown(transcript, speakers, names)

    assert "张三: 你好" in markdown


def test_derive_merges_consecutive_same_speaker_into_turns() -> None:
    analysis = {
        "sentences": [
            {"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": "你好，"},
            {"start": 1000, "end": 2500, "speaker": "SPEAKER_00", "text": "世界。"},
            {"start": 2500, "end": 4000, "speaker": "SPEAKER_01", "text": "再见。"},
        ]
    }

    transcript = derive_transcript(analysis)
    speakers = derive_speakers(analysis)

    assert transcript["language"] == "zh"
    assert transcript["segments"] == [
        {"start": 0.0, "end": 2.5, "text": "你好，世界。"},
        {"start": 2.5, "end": 4.0, "text": "再见。"},
    ]
    assert speakers == [
        {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
        {"start": 2.5, "end": 4.0, "speaker": "SPEAKER_01"},
    ]


def test_derive_empty_sentences_yields_empty_results() -> None:
    analysis: dict[str, object] = {"sentences": []}

    assert derive_transcript(analysis) == {"language": "zh", "segments": [], "review_flags": []}
    assert derive_speakers(analysis) == []


def test_derive_ms_to_seconds_rounded_to_three_decimals() -> None:
    analysis = {"sentences": [{"start": 1234, "end": 5678, "speaker": "SPEAKER_00", "text": "x"}]}

    assert derive_transcript(analysis)["segments"] == [{"start": 1.234, "end": 5.678, "text": "x"}]
    assert derive_speakers(analysis) == [{"start": 1.234, "end": 5.678, "speaker": "SPEAKER_00"}]


def test_derive_missing_sentences_raises_recoverable_error() -> None:
    with pytest.raises(ValueError, match="retry"):
        derive_transcript({"text": "x"})
    with pytest.raises(ValueError, match="retry"):
        derive_speakers({"sentences": "not-a-list"})  # type: ignore[dict-item]


def test_derive_rejects_non_dict_sentence() -> None:
    analysis = {"sentences": [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": "ok"}, "not a dict"]}
    with pytest.raises(RuntimeError, match="畸形"):
        derive_transcript(analysis)


def test_derive_rejects_non_numeric_time() -> None:
    analysis = {"sentences": [{"start": "abc", "end": 1000, "speaker": "SPEAKER_00", "text": "x"}]}
    with pytest.raises(RuntimeError, match="畸形"):
        derive_transcript(analysis)


def test_derive_rejects_invalid_time_window() -> None:
    analysis = {"sentences": [{"start": 2000, "end": 1000, "speaker": "SPEAKER_00", "text": "x"}]}
    with pytest.raises(RuntimeError, match="畸形"):
        derive_transcript(analysis)
    analysis = {"sentences": [{"start": -1, "end": 1000, "speaker": "SPEAKER_00", "text": "x"}]}
    with pytest.raises(RuntimeError, match="畸形"):
        derive_transcript(analysis)


def test_derive_rejects_empty_speaker() -> None:
    analysis = {"sentences": [{"start": 0, "end": 1000, "speaker": "", "text": "x"}]}
    with pytest.raises(RuntimeError, match="畸形"):
        derive_transcript(analysis)


def test_derive_allows_empty_text() -> None:
    """VAD 静默短段允许空文本——属于正常场景，不应判畸形。"""
    analysis = {"sentences": [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": ""}]}
    assert derive_transcript(analysis)["segments"] == [{"start": 0.0, "end": 1.0, "text": ""}]


def test_derive_transcript_passes_through_review_flags() -> None:
    analysis = {"sentences": [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": "x"}], "review_flags": ["repetition"]}
    assert derive_transcript(analysis)["review_flags"] == ["repetition"]


def test_render_markdown_includes_review_notice() -> None:
    from meetingflow.render import render_speakers_markdown

    transcript = {"segments": [{"start": 0.0, "end": 1.0, "text": "x"}], "review_flags": ["repetition"]}
    speakers: list[SpeakerSegment] = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
    md = render_speakers_markdown(transcript, speakers, {"SPEAKER_00": "张三"})
    assert "⚠ 存在待人工核听标记" in md
    assert "repetition" in md

    plain = {"segments": [{"start": 0.0, "end": 1.0, "text": "x"}]}
    md_plain = render_speakers_markdown(plain, speakers, {"SPEAKER_00": "张三"})
    assert "⚠" not in md_plain
