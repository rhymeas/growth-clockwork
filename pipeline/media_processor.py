"""Optional local media understanding through small open-source command-line tools.

Uploaded bytes stay immutable. This module only returns bounded derived text and
metadata. It never uses a shell, network, model API, or caller-supplied command.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


MAX_EXTRACTED_CHARS = 12_000
PROCESS_TIMEOUT_SECONDS = 90
CONFIG_RELATIVE = Path("runtime/media-processing.json")
_EXECUTABLES = {
    "ocr": "tesseract",
    "probe": "ffprobe",
    "transcription": "whisper-cli",
    "conversion": "ffmpeg",
}


def _default_config() -> dict[str, Any]:
    return {
        "version": "1.0",
        "ocr": {"enabled": True, "language": "eng"},
        "probe": {"enabled": True},
        "transcription": {"enabled": False, "model_path": "", "language": "auto"},
    }


def _config(workspace: Path) -> tuple[dict[str, Any], str | None]:
    path = workspace / CONFIG_RELATIVE
    if not path.exists():
        return _default_config(), None
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
            raise ValueError()
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.pop("$comment", None)
        if not isinstance(value, dict) or set(value) != {"version", "ocr", "probe", "transcription"}:
            raise ValueError()
        if value["version"] != "1.0":
            raise ValueError()
        if not isinstance(value["ocr"], dict) or set(value["ocr"]) != {"enabled", "language"}:
            raise ValueError()
        if not isinstance(value["probe"], dict) or set(value["probe"]) != {"enabled"}:
            raise ValueError()
        if not isinstance(value["transcription"], dict) or set(value["transcription"]) != {"enabled", "model_path", "language"}:
            raise ValueError()
        if any(not isinstance(value[key]["enabled"], bool) for key in ("ocr", "probe", "transcription")):
            raise ValueError()
        language = value["ocr"]["language"]
        transcript_language = value["transcription"]["language"]
        model_path = value["transcription"]["model_path"]
        if (not isinstance(language, str) or not language or len(language) > 40
                or not isinstance(transcript_language, str) or not transcript_language or len(transcript_language) > 40
                or not isinstance(model_path, str) or len(model_path) > 2_000):
            raise ValueError()
        return value, None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return _default_config(), "Local media-processing config is invalid; optional processors stayed off."


def capabilities(workspace: Path) -> dict[str, Any]:
    config, error = _config(workspace)
    engines: dict[str, Any] = {}
    for role, command in _EXECUTABLES.items():
        configured = bool(config["transcription"]["enabled"] if role == "conversion" else config[role]["enabled"])
        engines[role] = {
            "engine": command,
            "enabled": configured,
            "available": shutil.which(command) is not None,
        }
    model = config["transcription"]["model_path"]
    model_ready = False
    if model:
        candidate = Path(model).expanduser()
        try:
            model_ready = candidate.is_absolute() and candidate.is_file() and not candidate.is_symlink()
        except OSError:
            model_ready = False
    engines["transcription"]["model_ready"] = model_ready
    return {"local_only": True, "config_error": error, "engines": engines}


def _run(arguments: list[str], *, timeout: int = PROCESS_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    )


def _bounded(value: str) -> tuple[str, bool]:
    cleaned = value.replace("\x00", "").strip()
    return cleaned[:MAX_EXTRACTED_CHARS], len(cleaned) > MAX_EXTRACTED_CHARS


def process(workspace: Path, *, suffix: str, content: bytes) -> dict[str, Any]:
    """Return honest local processing results for already signature-checked bytes."""
    config, config_error = _config(workspace)
    available = {role: shutil.which(command) for role, command in _EXECUTABLES.items()}
    metadata: dict[str, Any] = {"validation": "signature_only_not_decoded"}
    notes: list[str] = []
    extracted = ""

    if config_error:
        return {"status": "indexed", "note": config_error, "extracted_text": "", "metadata": metadata}

    with tempfile.TemporaryDirectory(prefix="growth-media-") as directory:
        source = Path(directory) / f"source{suffix}"
        source.write_bytes(content)

        if config["probe"]["enabled"] and available["probe"] and suffix in {".mp3", ".wav", ".mp4", ".mov", ".webm"}:
            try:
                result = _run([available["probe"], "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(source)], timeout=20)
                if result.returncode == 0:
                    value = json.loads(result.stdout)
                    metadata["media_probe"] = {
                        "format": value.get("format", {}),
                        "streams": value.get("streams", [])[:8],
                        "engine": "ffprobe",
                    }
                    metadata["validation"] = "decoded_by_ffprobe"
                    notes.append("Media metadata decoded locally with FFprobe.")
                else:
                    notes.append("FFprobe could not decode this file.")
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError):
                notes.append("FFprobe failed safely; original file is still preserved.")

        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            if config["ocr"]["enabled"] and available["ocr"]:
                try:
                    result = _run([available["ocr"], str(source), "stdout", "-l", config["ocr"]["language"]], timeout=45)
                    if result.returncode == 0:
                        extracted, truncated = _bounded(result.stdout)
                        metadata.update(processor="tesseract", preview_truncated=truncated)
                        notes.append("Image OCR ran locally with Tesseract; extracted text remains untrusted.")
                    else:
                        notes.append("Tesseract could not read this image.")
                except (OSError, subprocess.TimeoutExpired):
                    notes.append("Tesseract failed safely; original image is still preserved.")
            else:
                notes.append("OCR needs the open-source Tesseract tool.")

        if suffix in {".mp3", ".wav", ".mp4", ".mov", ".webm"}:
            transcript = config["transcription"]
            model = Path(transcript["model_path"]).expanduser() if transcript["model_path"] else None
            try:
                model_ready = bool(model and model.is_absolute() and model.is_file() and not model.is_symlink())
            except OSError:
                model_ready = False
            if transcript["enabled"] and available["transcription"] and available["conversion"] and model_ready:
                wave = Path(directory) / "converted.wav"
                output = Path(directory) / "transcript"
                try:
                    converted = _run([available["conversion"], "-nostdin", "-v", "error", "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(wave)], timeout=45)
                    if converted.returncode != 0:
                        notes.append("FFmpeg could not prepare audio for transcription.")
                    else:
                        arguments = [available["transcription"], "-m", str(model), "-f", str(wave), "-otxt", "-of", str(output), "-np"]
                        if transcript["language"] != "auto":
                            arguments.extend(["-l", transcript["language"]])
                        result = _run(arguments)
                        transcript_file = output.with_suffix(".txt")
                        if result.returncode == 0 and transcript_file.is_file():
                            extracted, truncated = _bounded(transcript_file.read_text(encoding="utf-8", errors="replace"))
                            metadata.update(processor="whisper.cpp", preview_truncated=truncated)
                            notes.append("Audio transcribed locally with whisper.cpp; transcript remains untrusted.")
                        else:
                            notes.append("whisper.cpp could not transcribe this file.")
                except (OSError, subprocess.TimeoutExpired):
                    notes.append("Local transcription failed safely; original file is still preserved.")
            else:
                notes.append("Transcription needs FFmpeg, whisper.cpp, and one configured local model.")

    status = "processed" if extracted else "indexed"
    if not notes:
        notes.append("Stored and fingerprinted locally. No compatible processor is enabled.")
    return {"status": status, "note": " ".join(notes), "extracted_text": extracted, "metadata": metadata}
