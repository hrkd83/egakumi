import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { test } from "node:test";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";
import { getPortalPublicOrigin } from "../lib/public-origin.ts";

/** Nodeの合成試験では、Nextとaliasを実モジュールのURLへ解決して同じproxy関数を呼びます。 */
async function loadProxy() {
  const folder = await mkdtemp(join(tmpdir(), "portal-proxy-origin-fixture-"));
  const source = await readFile(new URL("../proxy.ts", import.meta.url), "utf8");
  const resolved = source
    .replace('from "@formatjs/intl-localematcher"', `from ${JSON.stringify(import.meta.resolve("@formatjs/intl-localematcher"))}`)
    .replace('from "negotiator"', `from ${JSON.stringify(import.meta.resolve("negotiator"))}`)
    .replace('from "next/server"', `from ${JSON.stringify(import.meta.resolve("next/server.js"))}`)
    .replace('from "@/lib/i18n/config"', `from ${JSON.stringify(new URL("../lib/i18n/config.ts", import.meta.url).href)}`)
    .replace('from "@/lib/public-origin"', `from ${JSON.stringify(new URL("../lib/public-origin.ts", import.meta.url).href)}`);
  await writeFile(join(folder, "proxy.mjs"), transpileModule(resolved, {
    compilerOptions: { module: ModuleKind.ESNext, target: ScriptTarget.ES2022 },
  }).outputText);
  const loaded = await import(pathToFileURL(join(folder, "proxy.mjs")).href);
  return { proxy: loaded.proxy as (request: unknown) => Response, cleanup: () => rm(folder, { recursive: true, force: true }) };
}

test("HTTP内部Hostと転送ヘッダーにかかわらず、言語転送は公開HTTPSとクエリを保つ", async () => {
  const prior = { url: process.env.BETTER_AUTH_URL, mode: process.env.NODE_ENV };
  const proxyFixture = await loadProxy();
  const variables: Record<string, string | undefined> = process.env;
  try {
    process.env.BETTER_AUTH_URL = "https://portal.example.invalid";
    variables.NODE_ENV = "production";
    for (const internalOrigin of ["http://aca.internal.example.invalid", "http://127.0.0.1:3001", "https://different.invalid"]) {
      const request = {
        nextUrl: new URL(`${internalOrigin}/models?view=detail&return=%2Fja%2Ftools`),
        headers: new Headers({ "accept-language": "en-US,en;q=0.9", "x-forwarded-proto": "http", "x-forwarded-host": "different.invalid" }),
      };
      const response = proxyFixture.proxy(request);
      assert.equal(response.status, 307);
      assert.equal(response.headers.get("location"), "https://portal.example.invalid/en/models?view=detail&return=%2Fja%2Ftools");
    }
    const response = proxyFixture.proxy({ nextUrl: new URL("http://aca.internal.example.invalid/ja/models"), headers: new Headers() });
    assert.equal(response.headers.get("location"), null);
    assert.equal(response.headers.get("x-middleware-next"), "1");
  } finally {
    if (prior.url === undefined) delete process.env.BETTER_AUTH_URL; else process.env.BETTER_AUTH_URL = prior.url;
    if (prior.mode === undefined) delete variables.NODE_ENV; else variables.NODE_ENV = prior.mode;
    await proxyFixture.cleanup();
  }
});

test("公開origin設定は内部HTTP・資格情報・パスを拒否し、開発のloopbackだけHTTPを許す", () => {
  const prior = { url: process.env.BETTER_AUTH_URL, mode: process.env.NODE_ENV };
  const variables: Record<string, string | undefined> = process.env;
  try {
    variables.NODE_ENV = "production";
    for (const value of ["http://aca.internal.example.invalid", "http://localhost:3000", "https://user:pass@portal.example.invalid", "https://portal.example.invalid/path", "https://portal.example.invalid/?q=value", "invalid"]) {
      process.env.BETTER_AUTH_URL = value;
      assert.throws(() => getPortalPublicOrigin(), /BETTER_AUTH_URL/);
    }
    process.env.BETTER_AUTH_URL = "https://portal.example.invalid";
    assert.equal(getPortalPublicOrigin(), "https://portal.example.invalid");
    variables.NODE_ENV = "development";
    process.env.BETTER_AUTH_URL = "http://localhost:3000";
    assert.equal(getPortalPublicOrigin(), "http://localhost:3000");
  } finally {
    if (prior.url === undefined) delete process.env.BETTER_AUTH_URL; else process.env.BETTER_AUTH_URL = prior.url;
    if (prior.mode === undefined) delete variables.NODE_ENV; else variables.NODE_ENV = prior.mode;
  }
});
