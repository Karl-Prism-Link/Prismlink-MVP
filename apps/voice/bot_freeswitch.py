"""FreeSWITCH listener that runs one isolated PRISM LINK receptionist per call."""

import asyncio
import logging
import os

from dotenv import load_dotenv
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameProcessor

try:
    from apps.voice.bot import run_bot
    from services.voice.freeswitch_transport import FreeSwitchAudioStreamTransport
except ImportError:
    from bot import run_bot

    from freeswitch_transport import FreeSwitchAudioStreamTransport


class Echo(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio, sample_rate=frame.sample_rate, num_channels=1
                )
            )
        else:
            await self.push_frame(frame, direction)


async def run_echo_call(transport):
    """Keep a dependency-light echo path for audio transport verification."""
    pipeline = Pipeline([transport.input(), Echo(), transport.output()])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=transport.sample_rate,
            audio_out_sample_rate=transport.sample_rate,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def connected(t, call_uuid):
        logging.info("Call ready: %s", call_uuid)
        # Echo mode deliberately has no salon/database/provider dependencies.

    async def cancel_on_disconnect():
        await transport.disconnected.wait()
        await task.cancel()

    monitor = asyncio.create_task(cancel_on_disconnect())
    try:
        await PipelineRunner(handle_sigint=False).run(task)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        await transport.close()


async def run_call(transport):
    """Run the actual PRISM LINK voice pipeline for one FreeSWITCH connection."""
    from pipecat.runner.types import RunnerArguments

    metadata = transport.connection_metadata
    await run_bot(
        transport,
        RunnerArguments(),
        tenant_slug=metadata.get("tenant_slug"),
        external_call_id=f"freeswitch_{transport.session_id}",
        caller_number=metadata.get("caller_number"),
        called_number=metadata.get("called_number"),
    )


async def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    await FreeSwitchAudioStreamTransport.serve(
        run_call,
        host=os.getenv("FS_WS_HOST", "127.0.0.1"),
        port=int(os.getenv("FS_WS_PORT", "8765")),
        sample_rate=int(os.getenv("FS_SAMPLE_RATE", "16000")),
        token=os.getenv("FS_WS_TOKEN") or None,
        max_calls=int(os.getenv("FS_MAX_CALLS", "10")),
        receive_timeout=float(os.getenv("FS_RECEIVE_TIMEOUT", "60")),
        send_timeout=float(os.getenv("FS_SEND_TIMEOUT", "2")),
    )


if __name__ == "__main__":
    asyncio.run(main())
