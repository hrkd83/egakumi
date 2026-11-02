import assert from "node:assert/strict";
import { test } from "node:test";
import { createCompany, loadCompanies, updateCompany } from "../components/company-administration/client.ts";

const company = { id: "11111111-1111-4111-8111-111111111111", name: "Example Corporation", isActive: true,
  createdAt: "2026-10-30T00:00:00.000Z", updatedAt: "2026-10-30T00:00:00.000Z" };

test("会社一覧は有効・停止状態と更新版を保持し、予期しない秘密フィールドを捨てる", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (input, init) => {
      assert.equal(input, "/api/company-administration"); assert.equal(init?.cache, "no-store");
      return Response.json({ companies: [{ ...company, primaryKey: "must-not-retain" }] });
    };
    assert.deepEqual(await loadCompanies(), [company]);
    globalThis.fetch = async () => Response.json({ companies: [company, company] });
    await assert.rejects(loadCompanies(), { code: "ServiceUnavailable" });
    globalThis.fetch = async () => Response.json({ companies: [{ ...company, updatedAt: undefined }] });
    await assert.rejects(loadCompanies(), { code: "ServiceUnavailable" });
  } finally { globalThis.fetch = original; }
});

test("会社登録は名前だけ、更新は元の版を送り、任意URLや削除操作を受け付けない", async () => {
  const original = globalThis.fetch, calls: { url: unknown; method: string | undefined; body: unknown }[] = [];
  try {
    globalThis.fetch = async (input, init) => {
      calls.push({ url: input, method: init?.method, body: JSON.parse(init?.body as string) });
      return Response.json({ company });
    };
    assert.deepEqual(await createCompany("  Example Corporation  "), company);
    await updateCompany(company.id, { name: company.name, isActive: false, expectedUpdatedAt: company.updatedAt });
    assert.deepEqual(calls, [
      { url: "/api/company-administration", method: "POST", body: { name: company.name } },
      { url: `/api/company-administration/${company.id}`, method: "PATCH", body: { name: company.name, isActive: false, expectedUpdatedAt: company.updatedAt } },
    ]);
    await assert.rejects(createCompany(" "), { code: "InvalidRequest" });
    await assert.rejects(updateCompany("../another-resource", { name: company.name, isActive: false, expectedUpdatedAt: company.updatedAt }), { code: "InvalidRequest" });
    assert.equal(calls.length, 2);
  } finally { globalThis.fetch = original; }
});

test("権限拒否・競合を空の成功一覧に変換せず、内部の診断本文も保持しない", async () => {
  const original = globalThis.fetch;
  try {
    for (const code of ["AccessDenied", "CompanyVersionConflict"]) {
      globalThis.fetch = async () => Response.json({ error: { code }, detail: "sensitive internal diagnostic" }, { status: code === "AccessDenied" ? 403 : 409 });
      await assert.rejects(loadCompanies(), (error) => {
        assert.equal((error as { code: string }).code, code);
        assert.equal(JSON.stringify(error).includes("diagnostic"), false);
        return true;
      });
    }
  } finally { globalThis.fetch = original; }
});
