import axios from "axios";
import { CONFIG } from "./config.js";

export function startTTS() {
  return {
    async speak(text) {
      try {
        const response = await axios.post(
          "https://api.deepgram.com/v1/speak",
          {
            text,
            model: "nova-2",     // Nova-2 TTS
            voice: "aura"        // Best NZ-friendly voice
          },
          {
            headers: {
              "Authorization": `Token ${CONFIG.DEEPGRAM_KEY}`,
              "Content-Type": "application/json"
            },
            responseType: "arraybuffer"
          }
        );

        return Buffer.from(response.data);
      } catch (err) {
        console.error("[tts] error:", err.message);
        return null;
      }
    }
  };
}
