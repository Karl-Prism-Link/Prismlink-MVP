import asyncio
import unittest
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

try:
    from apps.voice.bot_freeswitch import run_echo_call
    from services.voice.freeswitch_transport import FreeSwitchAudioStreamTransport, PCM20ms
except ImportError:
    from bot_freeswitch import run_echo_call
    from freeswitch_transport import FreeSwitchAudioStreamTransport, PCM20ms


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def test_pcm_fragmentation(self):
        for rate in (8000, 16000):
            pcm = PCM20ms(rate)
            audio = bytes(range(256)) * 5
            chunks = []
            for i in range(0, len(audio), 7):
                chunks.extend(pcm.feed(audio[i : i + 7]))
            self.assertEqual(b"".join(chunks) + bytes(pcm.pending), audio)
            self.assertTrue(all(len(c) == rate // 50 * 2 for c in chunks))

    async def test_live_pipeline_and_isolation(self):
        stop = asyncio.Event()
        sessions = []

        async def tracked(t):
            sessions.append(t)
            await run_echo_call(t)

        server = asyncio.create_task(
            FreeSwitchAudioStreamTransport.serve(tracked, port=18765, token="test", stop_event=stop)
        )
        try:
            for _ in range(50):
                try:
                    probe = await connect("ws://127.0.0.1:18765/invalid")
                    await probe.close()
                    break
                except OSError:
                    await asyncio.sleep(0.02)
            url = "ws://127.0.0.1:18765/calls/"
            headers = {"Authorization": "Bearer test"}
            a, b = str(uuid4()), str(uuid4())
            first_url = url + a + "?called_number=%2B6490000000&caller_number=%2B6421000000"
            async with connect(first_url, additional_headers=headers) as first:
                async with connect(url + b, additional_headers=headers) as second:
                    # An odd boundary must preserve the actual signed sample bytes.
                    pcm_a, pcm_b = b"\x01\x00" * 320, b"\xff\xff" * 320
                    await first.send(pcm_a[:17])
                    await first.send(pcm_a[17:])
                    await second.send(pcm_b)
                    self.assertEqual(await asyncio.wait_for(first.recv(), 5), pcm_a)
                    self.assertEqual(await asyncio.wait_for(second.recv(), 5), pcm_b)
                    async with connect(url + a, additional_headers=headers) as duplicate:
                        with self.assertRaises(ConnectionClosed):
                            await duplicate.recv()
                        self.assertEqual(duplicate.close_code, 1013)
                    async with connect(url + str(uuid4())) as unauthorized:
                        with self.assertRaises(ConnectionClosed):
                            await unauthorized.recv()
                        self.assertEqual(unauthorized.close_code, 1008)
                    await first.send('{"event":"stop"}')
                    await asyncio.wait_for(first.wait_closed(), 5)
                    # Other call still works after the first stops.
                    await second.send(pcm_b)
                    self.assertEqual(await asyncio.wait_for(second.recv(), 5), pcm_b)
            self.assertEqual({s.session_id for s in sessions}, {a, b})
            first_session = next(session for session in sessions if session.session_id == a)
            self.assertEqual(
                first_session.connection_metadata,
                {"called_number": "+6490000000", "caller_number": "+6421000000"},
            )
        finally:
            stop.set()
            await asyncio.wait_for(server, 15)


if __name__ == "__main__":
    unittest.main()
