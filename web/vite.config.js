import { readFileSync, readdirSync } from "node:fs";
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
    server.middlewares.use("/api/experiments", (_req, res) => {
      res.setHeader("Content-Type", "application/json");
      res.end(readFileSync(new URL("./dev/experiments.json", import.meta.url)));
    });
    // /reports/<id> -> the matching local report card in ../reports
    server.middlewares.use("/reports", (req, res, next) => {
      const id = req.url.replace(/^\//, "").split("?")[0];
      const dir = new URL("../reports/", import.meta.url);
      const file = readdirSync(dir).find((f) => f.endsWith(`-${id}.html`));
      if (!file) return next();
      res.setHeader("Content-Type", "text/html; charset=utf-8");
      res.end(readFileSync(new URL(file, dir)));
    });
  },
};

export default defineConfig({ plugins: [react(), devFixture] });
