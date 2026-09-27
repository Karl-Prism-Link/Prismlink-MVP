"""Per-call Pipecat transport for a raw-binary bidirectional mod_audio_stream build.

The listener is FreeSwitchAudioStreamTransport.serve(); never share a pipeline
between calls. Metadata/control JSON is an application convention, not ESL.
"""

import asyncio
import hmac
import json
import logging
from contextlib import suppress
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pipecat.frames.frames import InputAudioRawFrame, InputTransportMessageFrame
from pipecat.transports.base_transport import BaseTransport, TransportParams
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

try:
    from pipecat.transports.base_input import BaseInputTransport
    from pipecat.transports.base_output import BaseOutputTransport
except ImportError:  # Older Pipecat package layout.
    from pipecat.transports.base_input_transport import BaseInputTransport
    from pipecat.transports.base_output_transport import BaseOutputTransport
try:
    from pipecat.frames.frames import InterruptionFrame
except ImportError:
    from pipecat.frames.frames import StartInterruptionFrame as InterruptionFrame

log = logging.getLogger(__name__)


class PCM20ms:
    """Reassemble complete PCM samples across arbitrary WebSocket boundaries."""

    def __init__(self, rate):
        if rate not in (8000, 16000):
            raise ValueError("Sample rate must be 8000 or 16000")
        self.size = rate // 50 * 2
        self.pending = bytearray()

    def feed(self, data):
        self.pending.extend(data)
        while len(self.pending) >= self.size:
            chunk = bytes(self.pending[: self.size])
            del self.pending[: self.size]
            yield chunk


class _Input(BaseInputTransport):
    def __init__(self, owner, params):
        super().__init__(params)
        self.owner = owner
        self.reader = None

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        if self.reader is None:
            self.reader = self.create_task(self._receive())

    async def _receive(self):
        t = self.owner
        pcm = PCM20ms(t.sample_rate)
        try:
            await asyncio.wait_for(t.output_ready.wait(), t.start_timeout)
            t.ready.set()
            await t._call_event_handler("on_client_connected", t.session_id)
            while True:
                # Timeout means no WebSocket message, not caller silence.
                message = await asyncio.wait_for(t.websocket.recv(), t.receive_timeout)
                if isinstance(message, bytes):
                    for chunk in pcm.feed(message):
                        await self.push_audio_frame(
                            InputAudioRawFrame(
                                audio=chunk, sample_rate=t.sample_rate, num_channels=1
                            )
                        )
                else:
                    try:
                        control = json.loads(message)
                    except (ValueError, TypeError):
                        log.warning("%s: ignored non-JSON text", t.session_id)
                        continue
                    if not isinstance(control, dict):
                        continue
                    # Optional metadata may confirm identity/rate, never change them.
                    if "uuid" in control and control["uuid"] != t.session_id:
                        await t.close(1008, "UUID mismatch")
                        break
                    if "sample_rate" in control and control["sample_rate"] != t.sample_rate:
                        await t.close(1008, "Sample rate mismatch")
                        break
                    await self.push_frame(InputTransportMessageFrame(message=control))
                    await t._call_event_handler("on_control_message", control)
                    # Explicit convention supplied with uuid_audio_stream stop/send_text.
                    if control.get("event") == "stop":
                        await t.close()
                        break
        except ConnectionClosed:
            pass
        except TimeoutError:
            log.warning("%s: startup or receive timeout", t.session_id)
            await t.close(1011, "Audio stream timeout")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s: receive failure", t.session_id)
            await t.close(1011, "Receive failure")
        finally:
            if pcm.pending:
                log.debug("%s: discarded %d trailing PCM bytes", t.session_id, len(pcm.pending))
            t.disconnected.set()

    async def _stop_reader(self):
        if self.reader is not None:
            reader, self.reader = self.reader, None
            await self.cancel_task(reader)

    async def stop(self, frame):
        await self._stop_reader()
        await super().stop(frame)

    async def cancel(self, frame):
        await self._stop_reader()
        await super().cancel(frame)

    async def cleanup(self):
        await self._stop_reader()
        await super().cleanup()


class _Output(BaseOutputTransport):
    def __init__(self, owner, params):
        super().__init__(params)
        self.owner = owner
        self.next_send = 0.0

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        self.owner.output_ready.set()

    async def write_audio_frame(self, frame):
        t = self.owner
        # BaseOutputTransport resamples and chunks before invoking this method.
        if frame.num_channels != 1 or frame.sample_rate != t.sample_rate or len(frame.audio) % 2:
            log.error("%s: invalid output PCM format", t.session_id)
            await t.close(1011, "Output PCM format mismatch")
            return False
        try:
            size = t.sample_rate // 50 * 2
            for offset in range(0, len(frame.audio), size):
                chunk = frame.audio[offset : offset + size]
                loop = asyncio.get_running_loop()
                await asyncio.sleep(max(0, self.next_send - loop.time()))
                await asyncio.wait_for(t.websocket.send(chunk), t.send_timeout)
                # No catch-up burst after an event-loop or network stall.
                self.next_send = loop.time() + len(chunk) / (2 * t.sample_rate)
            return True
        except (TimeoutError, ConnectionClosed):
            await t.close(1011, "Audio send failed")
            return False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            self.next_send = 0.0
            # Base class cancels queued local audio. Remote buffering is build-specific.
            await self.owner._call_event_handler("on_interruption", self.owner.session_id)

    async def send_message(self, frame):
        # Do not send generic Pipecat/RTVI JSON as undocumented FS commands.
        log.debug("%s: output transport message ignored", self.owner.session_id)


class FreeSwitchAudioStreamTransport(BaseTransport):
    """One UUID, one socket, one pipeline. Use serve() to accept concurrent calls.

    Events: on_client_connected(uuid), on_client_disconnected(uuid),
    on_control_message(dict), on_interruption(uuid). Pipecat prepends transport.
    session_id is the application's UUID key; PipelineTask's internal id is untouched.
    """

    def __init__(
        self,
        websocket,
        session_id,
        sample_rate=16000,
        *,
        connection_metadata=None,
        receive_timeout=60.0,
        send_timeout=2.0,
        start_timeout=30.0,
    ):
        super().__init__()
        self.session_id = str(UUID(session_id))
        PCM20ms(sample_rate)  # Validate before creating processors.
        self.sample_rate = sample_rate
        self.websocket = websocket
        self.connection_metadata = dict(connection_metadata or {})
        self.receive_timeout = receive_timeout
        self.send_timeout = send_timeout
        self.start_timeout = start_timeout
        self.ready = asyncio.Event()
        self.output_ready = asyncio.Event()
        self.disconnected = asyncio.Event()
        params = TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=sample_rate,
            audio_out_sample_rate=sample_rate,
            audio_in_channels=1,
            audio_out_channels=1,
            audio_out_10ms_chunks=2,
            audio_in_passthrough=True,
        )
        self._input = _Input(self, params)
        self._output = _Output(self, params)
        for event in (
            "on_client_connected",
            "on_client_disconnected",
            "on_control_message",
            "on_interruption",
        ):
            self._register_event_handler(event)

    def input(self):
        return self._input

    def output(self):
        return self._output

    async def close(self, code=1000, reason="Session ended"):
        self.disconnected.set()
        with suppress(ConnectionClosed, asyncio.TimeoutError):
            await asyncio.wait_for(self.websocket.close(code=code, reason=reason), 3)

    @classmethod
    async def serve(
        cls,
        run_call,
        *,
        host="127.0.0.1",
        port=8765,
        sample_rate=16000,
        token=None,
        max_calls=10,
        stop_event=None,
        ssl=None,
        **transport_options,
    ):
        """run_call(transport) must run and clean up a fresh pipeline until completion.

        Path: /calls/<FreeSWITCH UUID>. One shared listener; no pipeline mixing.
        Set ssl to an SSLContext for direct WSS, or terminate TLS at a proxy.
        """
        PCM20ms(sample_rate)
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        if host not in ("127.0.0.1", "::1", "localhost") and not token:
            raise ValueError("A token is required when binding beyond loopback")
        active = set()

        async def handle(ws):
            request_url = urlsplit(ws.request.path)
            parts = request_url.path.strip("/").split("/")
            try:
                if len(parts) != 2 or parts[0] != "calls":
                    raise ValueError()
                call_id = str(UUID(parts[1]))
            except ValueError:
                await ws.close(1008, "Expected /calls/<uuid>")
                return
            headers = ws.request.headers.get_all("Authorization")
            if token and (
                len(headers) != 1 or not hmac.compare_digest(headers[0], "Bearer " + token)
            ):
                await ws.close(1008, "Unauthorized")
                return
            if call_id in active or len(active) >= max_calls:
                await ws.close(1013, "Duplicate UUID or capacity reached")
                return
            active.add(call_id)  # No await between capacity check and reservation.
            query = parse_qs(request_url.query, keep_blank_values=False)
            metadata = {
                key: query[key][-1]
                for key in ("tenant_slug", "caller_number", "called_number")
                if len(query.get(key, ())) == 1 and len(query[key][-1]) <= 160
            }
            t = cls(
                ws,
                call_id,
                sample_rate,
                connection_metadata=metadata,
                **transport_options,
            )
            bot = asyncio.create_task(run_call(t), name="call-" + call_id)
            closed = asyncio.create_task(ws.wait_closed())
            try:
                await asyncio.wait((bot, closed), return_when=asyncio.FIRST_COMPLETED)
                if not bot.done():
                    # run_call must observe disconnected and cancel its PipelineTask.
                    t.disconnected.set()
                    try:
                        await asyncio.wait_for(asyncio.shield(bot), 10)
                    except TimeoutError:
                        bot.cancel()
                await bot
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s: pipeline failed", call_id)
            finally:
                bot.cancel()
                closed.cancel()
                await asyncio.gather(bot, closed, return_exceptions=True)
                await t.close()
                active.discard(call_id)
                await t._call_event_handler("on_client_disconnected", call_id)

        async with serve(
            handle,
            host,
            port,
            ssl=ssl,
            compression=None,
            max_size=65536,
            max_queue=16,
            write_limit=32768,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=3,
        ):
            log.info("FreeSWITCH listener on %s:%d", host, port)
            await (stop_event or asyncio.Event()).wait()
