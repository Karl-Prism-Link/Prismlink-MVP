import axios from "axios";
import { CONFIG } from "./config.js";

export function startSTT() {
  let buffer = [];

  return {
    async feed(chunk) {
      buffer.push(chunk);

      const audio = Buffer.concat(buffer);

      try {
        const res = await axios.post(
          "https://api.deepgram.com/v1/listen",
          audio,
          {
            headers: {
              "Authorization": `Token ${CONFIG.DEEPGRAM_KEY}`,
              "Content-Type": "audio/wav"
            },
            params: {
              model: "nova-2",        // Nova-2 backend
              tier: "enhanced",       // Required for Flux
              version: "flux",        // Enable Flux streaming mode
              smart_format: true
            }
          }
        );

        const text =
          res.data?.results?.channels?.[0]?.alternatives?.[0]?.transcript;

        return text || null;
      } catch (err) {
        console.error("[stt] error:", err.message);
        return null;
      }
    }
  };
}
