import assert from "node:assert/strict";
import { test } from "node:test";
import { handlePortalAuth } from "../lib/auth-http.ts";

const origin = "https://portal.example.invalid";
const signIn = { provider: "microsoft", callbackURL: "/ja/models/text/openai", errorCallbackURL: "/ja/login?error=authentication_failed&next=%2Fja%2Fmodels%2Ftext%2Fopenai" };

/** 実際のHTTP境界を通し、拒否時に認証ライブラリへ到達しないことも確認します。 */
async function probe(path: string, headers: HeadersInit, body: unknown = {}, method = "POST") {
  let calls = 0;
  const response = await handlePortalAuth(new Request(`${origin}${path}`, {
    method, headers: { "Content-Type": "application/json", ...headers }, ...(method === "POST" ? { body: JSON.stringify(body) } : {}),
  }), { readOrigin: () => origin, handle: async () => { calls++; return Response.json({ success: true }); } });
  return { response, calls };
}

test("初回と既存Cookieの両方で、認証POSTの外部・欠落・null Originをライブラリ呼出し前に拒否する", async () => {
  for (const path of ["/api/auth/sign-in/social", "/api/auth/sign-out"]) {
    for (const cookie of [undefined, "__Secure-egakumi.portal.state=synthetic-only"]) {
      for (const suppliedOrigin of [undefined, "null", "https://untrusted.invalid", "https://sub.portal.example.invalid"]) {
        const headers: Record<string, string> = { "Sec-Fetch-Site": "same-origin" };
        if (cookie) headers.Cookie = cookie;
        if (suppliedOrigin !== undefined) headers.Origin = suppliedOrigin;
        const { response, calls } = await probe(path, headers, path.endsWith("social") ? signIn : {});
        assert.equal(response.status, 403); assert.equal(calls, 0);
        assert.deepEqual(await response.json(), { error: { code: "OriginNotAllowed" } });
        assert.equal(response.headers.get("cache-control"), "no-store");
      }
    }
  }
});

test("同じOriginでもcross-site・same-site・noneのFetch Metadataは認証POSTで拒否する", async () => {
  for (const fetchSite of ["cross-site", "same-site", "none", ""]) {
    const { response, calls } = await probe("/api/auth/sign-in/social", { Origin: origin, "Sec-Fetch-Site": fetchSite }, signIn);
    assert.equal(response.status, 403); assert.equal(calls, 0);
  }
});

test("通常クライアントの同一Originサインインとサインアウトを、Cookie有無に関係なく受け付ける", async () => {
  for (const cookie of [undefined, "__Secure-egakumi.portal.state=synthetic-only"]) {
    for (const path of ["/api/auth/sign-in/social", "/api/auth/sign-out"]) {
      for (const fetchSite of [undefined, "same-origin"]) {
        const headers: Record<string, string> = { Origin: origin };
        if (cookie) headers.Cookie = cookie;
        if (fetchSite) headers["Sec-Fetch-Site"] = fetchSite;
        const { response, calls } = await probe(path, headers, path.endsWith("social") ? signIn : {});
        assert.equal(response.status, 200); assert.equal(calls, 1);
      }
    }
  }
});

test("Hostと転送ヘッダーを偽装しても、設定したOriginを置き換えられない", async () => {
  const { response, calls } = await probe("/api/auth/sign-out", {
    Origin: "https://untrusted.invalid", Host: "untrusted.invalid", "X-Forwarded-Host": "untrusted.invalid", "X-Forwarded-Proto": "https",
  });
  assert.equal(response.status, 403); assert.equal(calls, 0);
});

test("GET callbackとget-sessionへPOSTのOrigin条件を波及させず、操作許可リストと戻り先検証は保つ", async () => {
  for (const path of ["/api/auth/callback/microsoft?state=synthetic-only", "/api/auth/get-session"]) {
    const { response, calls } = await probe(path, {}, undefined, "GET");
    assert.equal(response.status, 200); assert.equal(calls, 1);
  }
  const unknown = await probe("/api/auth/update-user", { Origin: origin });
  assert.equal(unknown.response.status, 404); assert.equal(unknown.calls, 0);
  const badReturn = await probe("/api/auth/sign-in/social", { Origin: origin }, { ...signIn, callbackURL: "https://untrusted.invalid" });
  assert.equal(badReturn.response.status, 400); assert.equal(badReturn.calls, 0);
});
