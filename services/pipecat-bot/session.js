import { startSTT } from "./stt.js";
import { startTTS } from "./tts.js";
import { llmRespond } from "./llm.js";
import { sendMCPEvent } from "./mcp.js";

export async function handleCallSession(ws) {
  const stt = startSTT();
  const tts = startTTS();

  ws.on("message", async (audioChunk) => {
    const text = await stt.feed(audioChunk);

    if (!text) return;

    console.log("[stt]", text);

    sendMCPEvent("user_said", { text });

    const reply = await llmRespond(text);

    console.log("[llm]", reply);

    const audioOut = await tts.speak(reply);

    ws.send(audioOut);
  });

  ws.on("close", () => {
    console.log("[pipecat] call ended");
  });
}
