export const CONFIG = {
  PORT: 3002,
  MCP_URL: process.env.MCP_URL || "http://127.0.0.1:3001/mcp-server/http",

  GROQ_API_KEY: process.env.GROQ_API_KEY,
  DEEPGRAM_KEY: process.env.DEEPGRAM_API_KEY,

  FAST_MODEL: "openai/gpt-oss-20b",
  DEEP_MODEL: "openai/gpt-oss-120b"
};
