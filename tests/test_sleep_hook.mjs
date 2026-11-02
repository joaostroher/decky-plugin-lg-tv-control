import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/sleep-hook.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { registerSleepHook, resolveSleepEvents } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const flush = () => new Promise(resolve => setImmediate(resolve));
const noTimer = () => () => {};

test("a present System API without the old event does not mask User events in either window", () => {
  const current = { RegisterForPrepareForSystemSuspendProgress() {} };
  const legacy = { RegisterForOnSuspendRequest() {} };
  assert.equal(resolveSleepEvents([{ SteamClient: { System: {}, User: current } }]), current);
  assert.equal(resolveSleepEvents([{ SteamClient: { System: {} } }, { SteamClient: { User: current } }]), current);
  assert.equal(resolveSleepEvents([{ SteamClient: { User: current } }, { SteamClient: { System: legacy } }]), legacy);
});

test("current Steam User API works when the System event is missing", async () => {
  let progress;
  let calls = 0;
  let removed = 0;
  let suspending = false;
  const reports = [];
  const dispose = registerSleepHook({ RegisterForPrepareForSystemSuspendProgress(listener) {
    progress = listener;
    listener({ state: 1 }); // A synchronous initial snapshot must not power off.
    return { unregister() { removed++; } };
  } }, async () => { calls++; }, async (...args) => { reports.push(args); }, noTimer, () => suspending);
  await flush();
  assert.deepEqual(reports[0], [true, "registered_user_progress"]);
  progress({ state: 1 }); // An asynchronously replayed idle snapshot is also ignored.
  progress({ state: 0 });
  progress({ state: 3 }); // Login progress is not a suspend request.
  assert.equal(calls, 0);
  suspending = true;
  progress({ state: 1 }); // No game running: preparation can finish without intermediate events.
  assert.equal(calls, 1);
  dispose();
  progress({ state: 1 });
  assert.equal(calls, 1);
  assert.equal(removed, 1);
});

test("User progress requires a preparation cycle when Steam's live suspend flag is unknown", async () => {
  let progress;
  let calls = 0;
  const dispose = registerSleepHook({ RegisterForPrepareForSystemSuspendProgress(listener) {
    progress = listener;
    return { unregister() {} };
  } }, async () => { calls++; }, async () => {}, noTimer);
  await flush();
  progress({ state: 1 });
  progress({ state: "1" });
  progress({ state: 99 });
  progress(null);
  assert.equal(calls, 0);
  progress({ state: 2 }); // Cloud sync in progress: wait for preparation to finish.
  progress({ state: 4 });
  assert.equal(calls, 0);
  progress({ state: 1 });
  assert.equal(calls, 1);
  progress({ state: 1 }); // A repeated completed snapshot does not start another cycle.
  assert.equal(calls, 1);
  progress({ state: 5 });
  progress({ state: 0 }); // Invalid/reset cancels the observed cycle.
  progress({ state: 1 });
  assert.equal(calls, 1);
  dispose();
});

test("a cancelled User preparation cannot trigger power-off", async () => {
  let progress;
  let suspending = true;
  let calls = 0;
  const dispose = registerSleepHook({ RegisterForPrepareForSystemSuspendProgress(listener) {
    progress = listener;
    return { unregister() {} };
  } }, async () => { calls++; }, async () => {}, noTimer, () => suspending);
  await flush();
  progress({ state: 5 });
  suspending = false;
  progress({ state: 1 });
  assert.equal(calls, 0);
  dispose();
});

test("the legacy System listener remains preferred when both APIs exist", async () => {
  let legacyRegistrations = 0;
  let progressRegistrations = 0;
  const dispose = registerSleepHook({
    RegisterForOnSuspendRequest() { legacyRegistrations++; return { unregister() {} }; },
    RegisterForPrepareForSystemSuspendProgress() { progressRegistrations++; return { unregister() {} }; },
  }, async () => {}, async () => {}, noTimer);
  await flush();
  assert.equal(legacyRegistrations, 1);
  assert.equal(progressRegistrations, 0);
  dispose();
});

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
