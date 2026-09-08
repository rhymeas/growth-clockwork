import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { backendArguments, startDesk } from "./start-desk.mjs";

test("default start does not enable or create a broker", () => {
  assert.equal(backendArguments(undefined).includes("--broker-database"), false);
  assert.equal(backendArguments(undefined, undefined).includes("--broker-permissions"), false);
});

test("relative or missing broker path fails before spawning", () => {
  let calls = 0;
  for (const name of ["tasks.sqlite", "/nonexistent-growth-broker.sqlite"]) {
    assert.throws(() => startDesk(() => { calls++; }, name));
  }
  assert.equal(calls, 0);
});

test("ports are fixed and either child's exit stops its sibling", () => {
  const children = [];
  const calls = [];
  const oldExit = process.exitCode;
  try {
    startDesk((command, args) => {
      calls.push({ command, args });
      const child = new EventEmitter();
      child.signals = [];
      child.kill = signal => child.signals.push(signal);
      children.push(child);
      return child;
    }, undefined);
    assert.ok(calls[1].args.includes("--strictPort"));
    assert.ok(calls[1].args.includes("4173"));
    children[0].emit("exit", 0);
    assert.deepEqual(children[1].signals, ["SIGTERM"]);
    assert.equal(process.exitCode, 1);
    children[1].emit("exit", null);
    assert.deepEqual(children[1].signals, ["SIGTERM"]);
  } finally {
    process.exitCode = oldExit;
  }
});
