import "./load-env.js";
import express from "express";
import { createServer } from "http";
import { WebSocketServer } from "ws";
import { CONFIG } from "./config.js";
import { handleCallSession } from "./session.js";

const app = express();
const httpServer = createServer(app);

const wss = new WebSocketServer({ server: httpServer });

wss.on("connection", (ws) => {
  console.log("[pipecat] inbound call connected");
  handleCallSession(ws);
});

httpServer.listen(CONFIG.PORT, () => {
  console.log(`[pipecat] listening on ${CONFIG.PORT}`);
});
