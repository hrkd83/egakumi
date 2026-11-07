import assert from "node:assert/strict";
import { formatWithOptions } from "node:util";
import { randomUUID } from "node:crypto";
import { createLogger } from "@better-auth/core/env";
import { test } from "node:test";
import { betterAuth } from "better-auth";
import { memoryAdapter } from "better-auth/adapters/memory";
import { createPortalAuthOptions } from "../lib/auth-options.ts";
import { handlePortalAuth } from "../lib/auth-http.ts";

const environment = {
  baseURL: "https://portal.example.invalid", secret: "independent-logger-test-secret-32-characters",
  microsoftTenantId: randomUUID(),
  microsoftClientId: randomUUID(), microsoftClientSecret: "synthetic-test-only",
};

test("不正stateの認証callbackは固定イベントだけを記録しコード交換へ進まない", async () => {
  const database: Record<string, Record<string, unknown>[]> = {
    portalUser: [], portalAccount: [], portalSession: [], portalVerification: [],
  };
  const options = createPortalAuthOptions(environment, memoryAdapter(database));
  const originalError = console.error, originalWarn = console.warn, originalFetch = globalThis.fetch;
  const logged: unknown[][] = []; let exchanges = 0, network = 0;
  const state = "independent-unregistered-test-state", code = "independent-code-" + "x".repeat(2048);
  try {
    console.error = (...values: unknown[]) => { logged.push(values); };
    console.warn = (...values: unknown[]) => { logged.push(values); };
    globalThis.fetch = async () => { network++; throw new Error("Unexpected network"); };
    const auth = betterAuth(options);
    const provider = (await auth.$context).socialProviders.find((entry) => entry.id === "microsoft");
    assert.ok(provider);
    provider.validateAuthorizationCode = async () => { exchanges++; throw new Error("Unexpected exchange"); };
    const request = new Request(`${environment.baseURL}/api/auth/callback/microsoft?${new URLSearchParams({ code, state })}`);
    const response = await handlePortalAuth(request, { readOrigin: () => environment.baseURL, handle: (value) => auth.handler(value) });
    assert.equal(response.status, 302);
    assert.equal(new URL(response.headers.get("location")!).pathname, "/ja/login");
    assert.equal(exchanges, 0); assert.equal(network, 0);
    assert.equal(database.portalUser.length, 0); assert.equal(database.portalSession.length, 0);
    assert.deepEqual(logged, [["[portal-auth] state-rejected"]]);
    const rendered = logged.map((values) => formatWithOptions({ colors: false }, ...values)).join("\n");
    assert.equal(rendered.includes(state), false); assert.equal(rendered.includes(code), false);
  } finally { console.error = originalError; console.warn = originalWarn; globalThis.fetch = originalFetch; }
});

test("可変のログmessageと入れ子の例外detailsを渡さず既知イベントとseverityを残す", () => {
  const logger = createPortalAuthOptions(environment, undefined).logger;
  const originalError = console.error, originalWarn = console.warn;
  const errors: unknown[][] = [], warnings: unknown[][] = [];
  const values = { state: "synthetic-state-never-log", code: "synthetic-code-never-log", cookie: "synthetic-cookie-never-log", token: "synthetic-token-never-log" };
  try {
    console.error = (...data: unknown[]) => { errors.push(data); };
    console.warn = (...data: unknown[]) => { warnings.push(data); };
    logger.log("error", `unexpected variable ${values.code}`, { cause: { details: values } });
    logger.log("warn", "Failed to parse state", Object.assign(new Error(values.token), { details: values }));
    assert.deepEqual(errors, [["[portal-auth] library-event"]]);
    assert.deepEqual(warnings, [["[portal-auth] state-rejected"]]);
    const rendered = [...errors, ...warnings].map((data) => formatWithOptions({ colors: false }, ...data)).join("\n");
    for (const value of Object.values(values)) assert.equal(rendered.includes(value), false);
  } finally { console.error = originalError; console.warn = originalWarn; }
});


test("ライブラリ既定のwarn閾値と静的dispatchはseverityを変えない", () => {
  const logger = createPortalAuthOptions(environment, undefined).logger;
  const methods = ["debug", "info", "warn", "error"] as const;
  const original = { debug: console.debug, info: console.info, warn: console.warn, error: console.error };
  const calls: { method: string; data: unknown[] }[] = [];
  try {
    for (const method of methods) console[method] = (...data: unknown[]) => { calls.push({ method, data }); };
    const library = createLogger(logger);
    library.debug("synthetic-variable"); library.info("synthetic-variable"); library.success("synthetic-variable");
    assert.equal(calls.length, 0, "既定のwarn未満はライブラリで抑止する");
    library.warn("State not found", { details: "synthetic-secret" });
    library.error("Failed to parse state", { details: "synthetic-secret" });
    assert.deepEqual(calls, [
      { method: "warn", data: ["[portal-auth] state-missing"] },
      { method: "error", data: ["[portal-auth] state-rejected"] },
    ]);
    calls.length = 0;
    for (const method of methods) logger.log(method, "synthetic-variable", { details: "synthetic-secret" });
    assert.deepEqual(calls.map(({ method }) => method), methods, "callbackへ直接届くlevelも昇格させない");
    assert.ok(calls.every(({ data }) => data.length === 1 && data[0] === "[portal-auth] library-event"));
  } finally { for (const method of methods) console[method] = original[method]; }
});
