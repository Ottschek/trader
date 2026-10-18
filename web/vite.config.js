import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In `npm run dev`, serve /api/health from a local fixture so the page can be built without Cloudflare.
const devFixture = {
  name: "dev-fixture",
  configureServer(server) {
    server.middlewares.use("/api/health", (_req, res) => {
      res.setHeader("Content-Type", "application/json");
      res.end(readFileSync(new URL("./dev/health.json", import.meta.url)));
    });
  },
};

export default defineConfig({ plugins: [react(), devFixture] });
