import path from "path";
import { fileURLToPath } from "url";
import dotenv from "dotenv";

// Resolve absolute path to the bot directory
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const envPath = path.join(__dirname, ".env");

// Load .env explicitly from the bot directory
dotenv.config({ path: envPath });

console.log("Environment loaded automatically.");
