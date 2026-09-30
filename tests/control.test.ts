import { test } from "node:test";
import assert from "node:assert/strict";
import { MockControlApi } from "../src/lib/api/MockControlApi";
import { runtimeConfig } from "../src/lib/env/config";
import { CreateMissionRequest, EventSchema } from "../src/contracts";
import { createHttpControlApi } from "../src/lib/api/HttpControlApi";
import { agentInMission } from "../src/lib/demo/fixtures";

test("blank configuration selects demo; live selects an empty HTTP store", () => {
  assert.deepEqual(runtimeConfig({ CORTEX_RUNTIME_MODE: "" }), {
    mode: "demo",
    available: true,
    error: null,
  });
  assert.equal(
    runtimeConfig({
      CORTEX_RUNTIME_MODE: "live",
      GEMINI_API_KEY: "test-only",
    }).available,
    true,
  );
  assert.equal(
    runtimeConfig({ CORTEX_RUNTIME_MODE: "typo" }).available,
    false,
  );
  assert.equal(createHttpControlApi().getSnapshot().missions.length, 0);
  assert.ok(createHttpControlApi().getSnapshot().live);
});
test("validate blank and oversized objectives", () => {
  assert.equal(
    CreateMissionRequest.safeParse({ objective: "  ", idempotencyKey: "a" })
      .success,
    false,
  );
  assert.equal(
    CreateMissionRequest.safeParse({
      objective: "a".repeat(2001),
      idempotencyKey: "a",
    }).success,
    false,
  );
});
test("preserves leading character verbatim without dropping the first letter", () => {
  const objective = "Build a premium website";
  const parsed = CreateMissionRequest.safeParse({
    objective,
    idempotencyKey: "test-key",
  });
  assert.equal(parsed.success, true);
  if (parsed.success) {
    assert.equal(parsed.data.objective, objective);
    assert.equal(parsed.data.objective[0], "B");
    assert.notEqual(parsed.data.objective, "uild a premium website");
  }
});
test("concurrent idempotent submissions produce one mission; changed payload rejected", async () => {
  const api = new MockControlApi();
  const [a, b] = await Promise.all([
    api.createMission({
      objective: "Study public data",
      idempotencyKey: "same",
    }),
    api.createMission({
      objective: "Study public data",
      idempotencyKey: "same",
    }),
  ]);
  assert.equal(a.id, b.id);
  assert.equal(api.getSnapshot().missions.length, 2);
  await assert.rejects(
    api.createMission({
      objective: "Different objective",
      idempotencyKey: "same",
    }),
    { code: "INVALID_REQUEST" },
  );
  assert.equal(
    api
      .getSnapshot()
      .tasks.filter((t) => t.missionId === a.id && t.status === "completed")
      .length,
    0,
  );
});
test("pause/resume preserves review and never completes waiting tasks", async () => {
  const api = new MockControlApi();
  await api.commandMission("demo-001", "pause");
  assert.equal(api.getSnapshot().missions[0].status, "paused");
  assert.equal(
    agentInMission(
      api.getSnapshot().agents.find((a) => a.id === "sage")!,
      api.getSnapshot(),
      "demo-001",
    ).runtimeStatus,
    "paused",
  );
  const n = api.getSnapshot().events.length;
  await api.commandMission("demo-001", "pause");
  assert.equal(api.getSnapshot().events.length, n);
  await api.commandMission("demo-001", "resume");
  assert.equal(api.getSnapshot().missions[0].status, "waiting_approval");
  assert.equal(api.getSnapshot().tasks[2].status, "waiting_approval");
  assert.equal(
    new Set(api.getSnapshot().events.map((e) => e.sequence)).size,
    api.getSnapshot().events.length,
  );
});
test("safe persistence round trip and invalid data fallback", async () => {
  const api = new MockControlApi();
  const mission = await api.createMission({
    objective: "A persisted objective",
    idempotencyKey: "persist",
  });
  const restored = new MockControlApi();
  restored.hydrate(JSON.stringify(api.getSnapshot()));
  assert.equal(restored.getSnapshot().selectedMissionId, mission.id);
  const fresh = new MockControlApi();
  fresh.hydrate("{broken");
  assert.equal(fresh.getSnapshot().missions.length, 1);
  fresh.hydrate(JSON.stringify({ ...api.getSnapshot(), schemaVersion: 50 }));
  assert.equal(fresh.getSnapshot().missions.length, 1);
});
test("identity fixtures never imply broad authorization or live evidence", () => {
  const api = new MockControlApi();
  const scout = api.getSnapshot().agents.find((a) => a.id === "scout")!;
  assert.equal(scout.identityStatus, "demo_verified");
  assert.deepEqual(scout.allowedScopes, ["dataset.read:public-demo"]);
  assert.equal(scout.publicEndpoint, undefined);
  assert.equal(
    EventSchema.safeParse({
      ...api.getSnapshot().events[0],
      type: "unknown.event",
    }).success,
    false,
  );
});
