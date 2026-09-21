import Groq from "groq-sdk";
import { CONFIG } from "./config.js";

const groq = new Groq({ apiKey: CONFIG.GROQ_API_KEY });

function pickModel(text) {
  // Short utterances ? fast model
  if (text.length < 200) return CONFIG.FAST_MODEL;

  // Longer or complex ? deep reasoning
  return CONFIG.DEEP_MODEL;
}

export async function llmRespond(text) {
  const model = pickModel(text);

  const completion = await groq.chat.completions.create({
    model,
    messages: [
      {
        role: "system",
        content:
          "You are PRISM LINK's AI receptionist. Be warm, concise, helpful, and optimized for NZ callers."
      },
      {
        role: "user",
        content: text
      }
    ],
    temperature: 0.4,
    max_tokens: 200
  });

  return completion.choices[0].message.content;
}
