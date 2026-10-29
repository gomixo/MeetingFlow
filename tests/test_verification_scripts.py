from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_blocker_records_attempt_before_failing() -> None:
    script = _load_script("verify-offline.py")
    attempts: list[str] = []
    blocked = script._network_blocker(attempts, "socket.connect")

    with pytest.raises(RuntimeError, match="阻止了网络访问"):
        blocked("example.com")

    assert attempts == ["socket.connect"]


def test_three_scenario_reads_review_flags_from_full_sha_directory(tmp_path: Path) -> None:
    script = _load_script("verify-three-scenario.py")
    full_job_id = "a" * 64
    job_dir = tmp_path / "jobs" / full_job_id
    job_dir.mkdir(parents=True)
    (job_dir / "transcript.raw.json").write_text(json.dumps({"review_flags": ["repetition"]}), encoding="utf-8")

    assert script._read_review_flags(tmp_path, full_job_id[:8]) == ["repetition"]


def test_three_scenario_command_forces_current_worktree(tmp_path: Path) -> None:
    script = _load_script("verify-three-scenario.py")
    command = script._meetingflow_command(tmp_path / "config.toml", tmp_path / "scene.wav")

    assert command[-1] == "--force"
    assert command[:3] == ["uv", "run", "meetingflow"]


def test_three_scenario_default_paths_follow_repository_root() -> None:
    script = _load_script("verify-three-scenario.py")
    prototype_root = ROOT / ".scratch" / "meeting-transcription-selection" / "prototype"

    assert ROOT == script.REPOSITORY_ROOT
    assert prototype_root / "benchmark-outputs" == script.PROTO_DIR
    assert prototype_root / "benchmark-audio" == script.BENCHMARK_DIR


def test_three_scenario_gate_rejects_drift_and_review_flags() -> None:
    script = _load_script("verify-three-scenario.py")
    accepted = {"new_chars": 100, "new_paragraphs": 10, "review_flags": [], "diff_vs_proto": {"similarity": 0.98}}

    assert script._scene_passes(accepted)
    assert not script._scene_passes({**accepted, "review_flags": ["repetition"]})
    assert not script._scene_passes({**accepted, "diff_vs_proto": {"similarity": 0.80}})


def test_prepare_models_does_not_rewrite_existing_directory(tmp_path: Path) -> None:
    script = _load_script("prepare-models.py")
    spec = dict(script.MODELS[0])
    target = tmp_path / str(spec["dir_name"])
    target.mkdir()
    manifest = target / "manifest.json"
    manifest.write_text("existing-manifest\n", encoding="utf-8")
    downloaded: list[Path] = []

    result = script._prepare_model(tmp_path, spec, lambda _model, _revision, path: downloaded.append(path))

    assert result == target
    assert downloaded == []
    assert manifest.read_text(encoding="utf-8") == "existing-manifest\n"


def test_prepare_models_publishes_only_after_anchor_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from meetingflow import analyze

    script = _load_script("prepare-models.py")
    spec = dict(script.MODELS[0])

    def download(_model: str, _revision: str, path: Path) -> None:
        (path / "weights.bin").write_bytes(b"frozen")

    staging = tmp_path / f".{spec['dir_name']}.preparing"
    staging.mkdir()
    download("", "", staging)
    files = [{"path": "weights.bin", "bytes": 6, "sha256": script._sha256(staging / "weights.bin")}]
    payload = script._canonical_manifest(staging, str(spec["model_id"]), str(spec["revision"]), files)
    monkeypatch.setitem(analyze.FROZEN_MANIFEST_HASHES, str(spec["role"]), script._manifest_digest(payload))
    monkeypatch.setattr(script, "_remote_files", lambda _model, _revision: files)
    (staging / "weights.bin").unlink()
    staging.rmdir()

    target = script._prepare_model(tmp_path, spec, download)

    assert target.is_dir()
    assert (target / "manifest.json").is_file()
    assert not staging.exists()


def test_prepare_models_builds_manifest_from_frozen_remote_inventory(tmp_path: Path) -> None:
    script = _load_script("prepare-models.py")
    directory = tmp_path / "model"
    directory.mkdir()
    (directory / "weights.bin").write_bytes(b"frozen")
    (directory / ".cache").mkdir()
    (directory / ".cache" / "client.json").write_text("{}", encoding="utf-8")
    files = [{"path": "weights.bin", "bytes": 6, "sha256": script._sha256(directory / "weights.bin")}]

    manifest = script._canonical_manifest(directory, "iic/test", "revision", files)
    script._remove_unlisted_files(directory, files)

    assert manifest["files"] == files
    assert not (directory / ".cache").exists()


def test_prepare_models_rejects_remote_inventory_mismatch(tmp_path: Path) -> None:
    script = _load_script("prepare-models.py")
    directory = tmp_path / "model"
    directory.mkdir()
    (directory / "weights.bin").write_bytes(b"changed")
    files = [{"path": "weights.bin", "bytes": 6, "sha256": script._sha256(directory / "weights.bin")}]

    with pytest.raises(ValueError, match="字节数不匹配"):
        script._canonical_manifest(directory, "iic/test", "revision", files)


def test_prepare_models_uses_cross_platform_frozen_path_order(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script("prepare-models.py")
    entries = [
        type("Entry", (), {"type": "blob", "path": "README.md", "size": 1, "sha256": "a"})(),
        type("Entry", (), {"type": "blob", "path": "model.pt", "size": 1, "sha256": "b"})(),
        type("Entry", (), {"type": "blob", "path": "am.mvn", "size": 1, "sha256": "c"})(),
    ]
    fake_api = type("FakeApi", (), {"list_repo_files": lambda self, *_args, **_kwargs: entries})
    monkeypatch.setattr("modelscope_hub.api.HubApi", fake_api)

    assert [item["path"] for item in script._remote_files("iic/test", "revision")] == ["am.mvn", "model.pt", "README.md"]
