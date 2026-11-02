import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/sleep-hook.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { registerSleepHook } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const flush = () => new Promise(resolve => setImmediate(resolve));
const noTimer = () => () => {};

test("Steam request reaches backend without mounting the panel; unload removes listener", async () => {
  let callback;
  let calls = 0;
  let removed = 0;
  const reports = [];
  const dispose = registerSleepHook({
    RegisterForOnSuspendRequest(listener) {
      callback = listener;
      return { unregister() { removed++; } };
    },
  }, async () => { calls++; }, async active => { reports.push(active); }, noTimer);
  await flush();
  assert.equal(calls, 0);
  callback();
  assert.equal(calls, 1);
  dispose();
  dispose();
  callback();
  assert.equal(calls, 1);
  assert.equal(removed, 1);
  assert.deepEqual(reports, [true, true, false]);
});

test("late Steam bridge is discovered without reloading; reporting retries do not duplicate listeners", async () => {
  let system;
  let retry;
  let registrations = 0;
  const reports = [];
  let backendReady = false;
  const dispose = registerSleepHook(() => system, async () => {}, async (active, reason) => {
    if (!backendReady) throw new Error("Starting");
    reports.push([active, reason]);
  }, tick => { retry = tick; return () => {}; });
  await flush();
  backendReady = true;
  system = {};
  retry();
  await flush();
  assert.deepEqual(reports.at(-1), [false, "missing_event"]);
  system.RegisterForOnSuspendRequest = () => { registrations++; return { unregister() {} }; };
  retry();
  await flush();
  retry();
  await flush();
  assert.equal(registrations, 1);
  assert.deepEqual(reports.at(-1), [true, "registered"]);
  dispose();
});

test("unsupported Steam client reports the exact reason", async () => {
  const reports = [];
  const dispose = registerSleepHook(() => undefined, async () => assert.fail(), async (...args) => { reports.push(args); }, noTimer);
  await flush();
  assert.deepEqual(reports[0], [false, "missing_client"]);
  dispose();
});

test("registration errors are retried and distinguished from missing API", async () => {
  let retry;
  let attempts = 0;
  const reports = [];
  const dispose = registerSleepHook({ RegisterForOnSuspendRequest() {
    if (++attempts === 1) throw new Error("Not ready");
    return { unregister() {} };
  } }, async () => {}, async (...args) => { reports.push(args); }, tick => { retry = tick; return () => {}; });
  await flush();
  assert.deepEqual(reports.at(-1), [false, "register_failed"]);
  retry();
  await flush();
  assert.deepEqual(reports.at(-1), [true, "registered"]);
  dispose();
});

test("function and asynchronous registration handles are supported", async () => {
  let removed = 0;
  const reports = [];
  const dispose = registerSleepHook({ async RegisterForOnSuspendRequest() {
    return () => { removed++; };
  } }, async () => {}, async active => { reports.push(active); }, noTimer);
  await flush();
  assert.equal(reports[0], true);
  dispose();
  assert.equal(removed, 1);
});

test("unload during asynchronous registration cleans up without reporting active", async () => {
  let finish;
  let removed = 0;
  const reports = [];
  const dispose = registerSleepHook({ RegisterForOnSuspendRequest() {
    return new Promise(resolve => { finish = resolve; });
  } }, async () => {}, async active => { reports.push(active); }, noTimer);
  dispose();
  finish({ unregister() { removed++; } });
  await flush();
  assert.equal(removed, 1);
  assert.deepEqual(reports, [false]);
});

test("void registration does not accumulate callbacks; a delivered event confirms it", async () => {
  let callback;
  let retry;
  let registrations = 0;
  let calls = 0;
  const reports = [];
  const dispose = registerSleepHook({ RegisterForOnSuspendRequest(listener) {
    registrations++;
    callback = listener;
  } }, async () => { calls++; }, async (...args) => { reports.push(args); }, tick => { retry = tick; return () => {}; });
  await flush();
  retry();
  await flush();
  assert.equal(registrations, 1);
  assert.deepEqual(reports.at(-1), [false, "invalid_handle"]);
  callback();
  assert.equal(calls, 1);
  assert.deepEqual(reports.at(-1), [true, "registered"]);
  dispose();
  callback();
  assert.equal(calls, 1);
});

test("backend disconnects and cleanup errors do not cause unhandled rejections", async () => {
  const unavailable = async () => { throw new Error("Disconnected"); };
  let callback;
  const dispose = registerSleepHook({ RegisterForOnSuspendRequest(listener) {
    callback = listener;
    return { unregister() { throw new Error("Unloading"); } };
  } }, unavailable, unavailable, noTimer);
  await flush();
  callback();
  dispose();
  await flush();
});
