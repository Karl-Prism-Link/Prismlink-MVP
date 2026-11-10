from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

from t001_assertions import assess_t001


T001_TARGET_URI = "sip:02825523645@100.105.4.49:5060"


def require_tailscale_target(uri: str) -> str:
    if uri != T001_TARGET_URI:
        raise ValueError(f"T001 is pinned to Luxe Hair & Beauty at {T001_TARGET_URI}.")
    host = uri.rsplit("@", 1)[1].split(";", 1)[0].split(":", 1)[0]
    address = ipaddress.ip_address(host)
    if address.version != 4 or address not in ipaddress.ip_network("100.64.0.0/10"):
        raise ValueError("Refusing SIP target outside the Tailscale IPv4 range (100.64.0.0/10).")
    return uri


def make_input_wav(path: Path, duration_seconds: int = 30) -> None:
    espeak = shutil.which("espeak-ng")
    ffmpeg = shutil.which("ffmpeg")
    if not espeak or not ffmpeg:
        raise RuntimeError("Install espeak-ng and ffmpeg to synthesize the test caller's speech.")
    with tempfile.TemporaryDirectory(prefix="prism-qa-audio-") as tmp:
        source = Path(tmp) / "hello-source.wav"
        converted = Path(tmp) / "hello-8k.wav"
        subprocess.run([espeak, "-s", "145", "-w", str(source), "Hello."], check=True)
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", str(converted)],
            check=True,
        )
        with wave.open(str(converted), "rb") as audio:
            frames = audio.readframes(audio.getnframes())
            params = audio.getparams()
        silence_frames = max(0, duration_seconds * params.framerate - len(frames) // params.sampwidth)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(frames)
            output.writeframes(b"\x00\x00" * silence_frames)


def linphone_call(target_uri: str, caller_audio: Path, response_audio: Path, wait_seconds: int) -> None:
    try:
        from linphone import CallState, Factory
    except ImportError as exc:
        raise RuntimeError(
            "The Linphone Python SDK is missing. Install the official pylinphone/liblinphone "
            "SDK on the test runner; this project deliberately does not substitute a PSTN provider."
        ) from exc

    factory = Factory.get()
    core = factory.create_core(None, None, None)
    core.use_files = True
    core.play_file = str(caller_audio)
    core.record_file = str(response_audio)
    core.start()
    call = None
    connected_at = None
    deadline = time.monotonic() + 30
    failed_states = {
        CallState.CallStateError,
        CallState.CallStateEnd,
        CallState.CallStateReleased,
    }
    try:
        call = core.invite(target_uri)
        if call is None:
            raise RuntimeError("Linphone could not create the SIP call.")
        while time.monotonic() < deadline:
            core.iterate()
            state = call.state
            if state in failed_states:
                raise RuntimeError(f"Linphone call ended before media started: {state}")
            if state == CallState.CallStateStreamsRunning:
                connected_at = time.monotonic()
                break
            time.sleep(0.02)
        if connected_at is None:
            raise TimeoutError("No active audio stream within 30 seconds.")
        while time.monotonic() - connected_at < wait_seconds:
            core.iterate()
            if call.state in failed_states:
                break
            time.sleep(0.02)
    finally:
        if call is not None:
            try:
                call.terminate()
            except Exception:
                pass
        for _ in range(10):
            core.iterate()
            time.sleep(0.02)
        core.stop()


def transcribe(audio_path: Path, api_key: str) -> str:
    query = urllib.parse.urlencode(
        {"model": "nova-3-general", "language": "en-NZ", "smart_format": "true"}
    )
    request = urllib.request.Request(
        f"https://api.deepgram.com/v1/listen?{query}",
        data=audio_path.read_bytes(),
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/wav",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Speech transcription failed with HTTP {exc.code}.") from exc
    alternatives = body.get("results", {}).get("channels", [{}])[0].get("alternatives", [])
    return alternatives[0].get("transcript", "").strip() if alternatives else ""


def run(output_dir: Path, dry_run: bool = False) -> dict[str, object]:
    target = require_tailscale_target(os.environ.get("LINPHONE_TARGET_URI", T001_TARGET_URI))
    if dry_run:
        return {"test_id": "T001", "status": "READY", "target": target}

    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("Set DEEPGRAM_API_KEY in the runner's private environment.")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("T001-%Y%m%dT%H%M%SZ")
    caller_audio = output_dir / f"{run_id}-caller.wav"
    response_audio = output_dir / f"{run_id}-receptionist.wav"
    make_input_wav(caller_audio)
    linphone_call(target, caller_audio, response_audio, wait_seconds=12)
    if not response_audio.exists() or response_audio.stat().st_size < 1000:
        result = {
            "test_id": "T001",
            "run_id": run_id,
            "status": "FAIL",
            "reason": "No receptionist audio was captured.",
        }
    else:
        transcript = transcribe(response_audio, api_key)
        result = {"test_id": "T001", "run_id": run_id, **assess_t001(transcript)}
    result["target"] = target
    result["evidence"] = {
        "caller_audio": str(caller_audio),
        "receptionist_audio": str(response_audio),
    }
    result["executed_at_utc"] = datetime.now(timezone.utc).isoformat()
    result_path = output_dir / f"{run_id}-result.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run T001 as an automated Linphone SIP call.")
    parser.add_argument("--output-dir", type=Path, default=Path("qa-results/voice"))
    parser.add_argument("--dry-run", action="store_true", help="Validate private target without calling.")
    args = parser.parse_args()
    try:
        result = run(args.output_dir, dry_run=args.dry_run)
    except Exception as exc:
        print(json.dumps({"test_id": "T001", "status": "BLOCKED", "reason": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in {"PASS", "READY"} else 1


if __name__ == "__main__":
    sys.exit(main())
