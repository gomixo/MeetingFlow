from __future__ import annotations

from meetingflow import pipeline
from meetingflow.diarize import SpeakerSegment
from meetingflow.render import render_speakers_markdown


def test_word_level_split_when_speaker_changes_within_segment() -> None:
    transcript = {"segments": [{"start": 0.0, "end": 2.0, "text": "你好再见", "words": [
        {"word": "你好", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"word": "再见", "start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
    ]}]}
    speakers: list[SpeakerSegment] = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
    ]
    names = {"SPEAKER_00": "张三", "SPEAKER_01": "李四"}

    markdown = render_speakers_markdown(transcript, speakers, names)

    assert "张三: 你好" in markdown
    assert "李四: 再见" in markdown


def test_consecutive_same_speaker_words_merge_into_one_line() -> None:
    transcript = {"segments": [{"start": 0.0, "end": 3.0, "text": "你好世界再见", "words": [
        {"word": "你好", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"word": "世界", "start": 1.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"word": "再见", "start": 2.0, "end": 3.0, "speaker": "SPEAKER_01"},
    ]}]}
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


def test_assign_word_speakers_tags_each_word() -> None:
    transcript = {"segments": [{"start": 0.0, "end": 2.0, "words": [
        {"word": "你好", "start": 0.0, "end": 1.0},
        {"word": "再见", "start": 1.0, "end": 2.0},
    ]}]}
    speakers: list[SpeakerSegment] = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
    ]

    result = pipeline._assign_word_speakers(transcript, speakers)

    assert result["segments"][0]["words"][0]["speaker"] == "SPEAKER_00"
    assert result["segments"][0]["words"][1]["speaker"] == "SPEAKER_01"
