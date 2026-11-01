import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/sleep-hook.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { registerSleepHook } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("Steam request reaches backend without mounting the panel; unload removes listener", () => {
  let callback;
  let calls = 0;
  let removed = 0;
  const reports = [];
  const dispose = registerSleepHook({
    RegisterForOnSuspendRequest(listener) {
      callback = listener;
      return { unregister() { removed++; } };
    },
  }, async () => { calls++; }, async active => { reports.push(active); });
  assert.equal(calls, 0);
  callback();
  assert.equal(calls, 1);
  dispose();
  dispose();
  callback(); // A queued callback after unload must not send another request.
  assert.equal(calls, 1);
  assert.equal(removed, 1);
  assert.deepEqual(reports, [true, false]);
});

test("unsupported Steam event reports unavailable without attempting power-off", () => {
  let calls = 0;
  const reports = [];
  const dispose = registerSleepHook({}, async () => { calls++; }, async active => { reports.push(active); });
  assert.equal(calls, 0);
  assert.deepEqual(reports, [false]);
  dispose();
});

test("registration errors and backend disconnects are contained", async () => {
  const unavailable = async () => { throw new Error("Disconnected"); };
  registerSleepHook({ RegisterForOnSuspendRequest() { throw new Error("Unsupported"); } }, unavailable, unavailable)();
  let callback;
  const dispose = registerSleepHook({ RegisterForOnSuspendRequest(listener) {
    callback = listener;
    return { unregister() { throw new Error("Unloading"); } };
  } }, unavailable, unavailable);
  callback();
  dispose();
  await new Promise(resolve => setImmediate(resolve));
});
