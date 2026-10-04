import axios from "axios";
import { CONFIG } from "./config.js";

export async function sendMCPEvent(type, payload) {
  try {
    await axios.post(CONFIG.MCP_URL, {
      event: type,
      payload,
    });
  } catch (err) {
    console.error("[mcp] error:", err.message);
  }
}
