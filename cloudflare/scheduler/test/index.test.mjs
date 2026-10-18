// Run: node --test cloudflare/scheduler/test
import { test } from "node:test";
import assert from "node:assert/strict";
import { workflowFor } from "../src/index.js";

const env = { TIMEZONE: "Europe/Vienna", SUMMARY_TIMES: "08:00,22:30" };
const at = (iso) => new Date(iso);

test("collector cron always starts the collector", () => {
  assert.equal(workflowFor("5 * * * *", at("2026-07-01T13:05:00Z"), env), "collect.yml");
});

test("summary fires at 08:00 and 22:30 Vienna in summer (UTC+2)", () => {
  assert.equal(workflowFor("0 * * * *", at("2026-07-01T06:00:00Z"), env), "summary.yml");
  assert.equal(workflowFor("30 * * * *", at("2026-07-01T20:30:00Z"), env), "summary.yml");
  assert.equal(workflowFor("0 * * * *", at("2026-07-01T07:00:00Z"), env), null);
});

test("summary fires at 08:00 and 22:30 Vienna in winter (UTC+1)", () => {
  assert.equal(workflowFor("0 * * * *", at("2026-01-15T07:00:00Z"), env), "summary.yml");
  assert.equal(workflowFor("30 * * * *", at("2026-01-15T21:30:00Z"), env), "summary.yml");
  assert.equal(workflowFor("30 * * * *", at("2026-01-15T20:30:00Z"), env), null);
});
