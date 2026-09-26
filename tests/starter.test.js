import test from "node:test";
import assert from "node:assert/strict";
import { listRoutes } from "../src/routes/index.js";

test("starter routes are visible", () => {
  assert.deepEqual(listRoutes(), ["GET /health", "GET /teams"]);
});
