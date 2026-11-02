import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { GraphApiKeyMembership } from "../lib/api-keys/graph.ts";
import { isEligibleApiKeyGroup, type ApiKeyGroupPolicy, type DirectoryGroup } from "../lib/api-keys/group-policy.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const objectId = "22222222-2222-4222-8222-222222222222";
const clientId = "33333333-3333-4333-8333-333333333333";
const platformGroupId = "44444444-4444-4444-8444-444444444444";
const groupId = "55555555-5555-4555-8555-555555555555";
const actor = { tenantId, objectId };
const settings = { microsoftTenantId: tenantId, microsoftClientId: clientId, microsoftClientSecret: "test-only-secret" };
const policy = { platformGroupId, groupNamePattern: "^ORG_" };
const group = (id = groupId, displayName = "ORG_営業", securityEnabled = true): DirectoryGroup => ({ id, displayName, securityEnabled });
const code = (value: string) => (error: unknown) => error instanceof ApiKeyError && error.code === value;

/** 実ネットワークへ接続せず、Graph要求のパス・ページング・認可を観測します。 */
function directory(handler: (url: URL, init?: RequestInit) => Promise<Response> | Response, groupPolicy: ApiKeyGroupPolicy = policy) {
  let tokenRequests = 0;
  const urls: URL[] = [];
  const graph = new GraphApiKeyMembership(settings, { readPolicy: async () => groupPolicy, now: () => Date.parse("2026-10-30T00:00:00Z"),
    fetch: async (input, init) => {
      if (String(input).startsWith("https://login.microsoftonline.com/")) {
        tokenRequests++;
        return Response.json({ token_type: "Bearer", access_token: "fixture-token", expires_in: 3600 });
      }
      assert.equal(init?.redirect, "error");
      assert.equal(init?.cache, "no-store");
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer fixture-token");
      const url = new URL(String(input));
      urls.push(url);
      return handler(url, init);
    } });
  return { graph, urls, tokenRequests: () => tokenRequests };
}

test("所有候補は環境の正規表現とsecurityEnabledの両方を満たす場合だけ許可する", () => {
  assert.equal(isEligibleApiKeyGroup(group(), policy), true);
  assert.equal(isEligibleApiKeyGroup(group(groupId, "org_営業"), policy), false);
  assert.equal(isEligibleApiKeyGroup(group(groupId, "ORG_営業", false), policy), false);
  assert.equal(isEligibleApiKeyGroup(group(platformGroupId, "管理者"), policy), false);
  const configured = { platformGroupId, groupNamePattern: "^TEAM_[A-Z]+_[0-9]{2}$" };
  assert.equal(isEligibleApiKeyGroup(group(groupId, "TEAM_FINANCE_01"), configured), true);
  assert.equal(isEligibleApiKeyGroup(group(), configured), false);
  assert.equal(isEligibleApiKeyGroup(group(groupId, "TEAM_FINANCE_01_extra"), configured), false);
  assert.equal(isEligibleApiKeyGroup(group(groupId, "TEAM_FINANCE_01", false), configured), false);
});

test("本人の入れ子所属を全ページ取得し、非ORGや非securityグループを返さない", async () => {
  const { graph, urls, tokenRequests } = directory((url, init) => {
    assert.equal(url.pathname, `/v1.0/users/${objectId}/transitiveMemberOf/microsoft.graph.group`);
    assert.equal(url.searchParams.get("$select"), "id,displayName,securityEnabled");
    assert.equal(url.searchParams.get("$count"), "true");
    assert.equal(new Headers(init?.headers).get("ConsistencyLevel"), "eventual");
    if (!url.searchParams.has("$skiptoken")) {
      const next = new URL(url); next.searchParams.set("$skiptoken", "page-two");
      return Response.json({ value: [group(), group(platformGroupId, "other")], "@odata.nextLink": next.href });
    }
    return Response.json({ value: [group(), group(clientId, "ORG_非security", false)] });
  });
  assert.deepEqual(await graph.listEligibleGroups(actor), [group()]);
  assert.equal(urls.length, 2);
  assert.equal(tokenRequests(), 1);
});

test("所属候補は100件の満ページの次にある101件目を失わず、定義した件数超過は明確に拒否する", async () => {
  const groups = Array.from({ length: 1_001 }, (_, index) => group(
    `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`, `ORG_部署${String(index).padStart(4, "0")}`));
  /** Graphの100件ページとnextLinkだけを模し、実装のページ合成・上限検査を実行します。 */
  const paged = (size: number) => directory((url) => {
    assert.equal(url.pathname, `/v1.0/users/${objectId}/transitiveMemberOf/microsoft.graph.group`);
    assert.equal(url.searchParams.get("$top"), "100");
    const offset = Number(url.searchParams.get("$skiptoken") ?? "0");
    const next = new URL(url);
    next.searchParams.set("$skiptoken", String(offset + 100));
    return Response.json({ value: groups.slice(offset, Math.min(offset + 100, size)),
      ...(offset + 100 < size ? { "@odata.nextLink": next.href } : {}) });
  });
  const ordinary = paged(101);
  const result = await ordinary.graph.listEligibleGroups(actor);
  assert.equal(result.length, 101);
  assert.deepEqual(new Set(result.map((value) => value.id)), new Set(groups.slice(0, 101).map((value) => value.id)));
  assert.equal(ordinary.urls.length, 2);
  const overflow = paged(1_001);
  await assert.rejects(overflow.graph.listEligibleGroups(actor), code("DirectoryResultTooLarge"));
  assert.equal(overflow.urls.length, 11);
});

test("移管候補の満ページが全て対象外でも、次の100件ページの適格グループへ進める", async () => {
  const omitted = Array.from({ length: 100 }, (_, index) => group(
    `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`, `OTHER_${index}`));
  const { graph, urls } = directory((url) => {
    assert.equal(url.pathname, "/v1.0/groups");
    assert.equal(url.searchParams.get("$top"), "100");
    if (!url.searchParams.has("$skiptoken")) {
      const next = new URL(url); next.searchParams.set("$skiptoken", "eligible-after-100");
      return Response.json({ value: omitted, "@odata.nextLink": next.href });
    }
    return Response.json({ value: [group()] });
  });
  const first = await graph.listEligibleGroupPage(actor);
  assert.deepEqual(first.groups, []);
  assert.ok(first.nextCursor);
  const second = await graph.listEligibleGroupPage(actor, { cursor: first.nextCursor });
  assert.deepEqual(second.groups, [group()]);
  assert.equal(second.nextCursor, null);
  assert.equal(urls.length, 2);
});

test("Graphの別ホスト・別ユーザー・条件変更・循環ページへトークンを転送しない", async () => {
  const mutations = [
    (url: URL) => new URL(url.href.replace("graph.microsoft.com", "outside.invalid")),
    (url: URL) => new URL(url.href.replace(objectId, clientId)),
    (url: URL) => { const next = new URL(url); next.searchParams.set("$top", "999"); return next; },
  ];
  for (const mutate of mutations) {
    const { graph, urls } = directory((url) => {
      const next = mutate(url); next.searchParams.set("$skiptoken", "page-two");
      return Response.json({ value: [group()], "@odata.nextLink": next.href });
    });
    await assert.rejects(graph.listEligibleGroups(actor), code("AuthorizationUnavailable"));
    assert.equal(urls.length, 1);
  }
  const { graph, urls } = directory((url) => {
    const next = new URL(url); next.searchParams.set("$skiptoken", "loop");
    return Response.json({ value: [group()], "@odata.nextLink": next.href });
  });
  await assert.rejects(graph.listEligibleGroups(actor), code("AuthorizationUnavailable"));
  assert.equal(urls.length, 2);
});

test("移管先候補の検索文字列へ接頭辞を足さず、戻り値は正規表現とsecurityEnabledで絞る", async () => {
  const { graph, urls } = directory((url) => {
    assert.equal(url.pathname, "/v1.0/groups");
    assert.equal(url.searchParams.get("$filter"), "securityEnabled eq true and startswith(displayName,'営''業')");
    assert.equal(url.searchParams.get("$top"), "100");
    return Response.json({ value: [group(), group(platformGroupId, "other"), group(clientId, "ORG_x", false)] });
  });
  assert.deepEqual(await graph.findEligibleGroups(actor, "営'業"), [group()]);
  assert.equal(urls.length, 1);
  assert.equal(urls.some((url) => url.pathname.includes("checkMemberGroups")), false);
  await assert.rejects(graph.findEligibleGroups(actor, "a"), code("InvalidRequest"));
});

test("接頭辞以外の正規表現を使い、空の候補ページでも続きから一致するグループを取得できる", async () => {
  const configured = { platformGroupId, groupNamePattern: "^TEAM_[A-Z]+_[0-9]{2}$" };
  const eligible = group(groupId, "TEAM_FINANCE_01");
  const { graph, urls } = directory((url) => {
    assert.equal(url.searchParams.get("$filter"), "securityEnabled eq true");
    assert.equal(url.searchParams.has("$search"), false);
    if (!url.searchParams.has("$skiptoken")) {
      const next = new URL(url); next.searchParams.set("$skiptoken", "page-two");
      return Response.json({ value: [group(), group(platformGroupId, "TEAM_FINANCE_01_extra")], "@odata.nextLink": next.href });
    }
    return Response.json({ value: [eligible, group(clientId, "TEAM_FINANCE_02", false)] });
  }, configured);
  const first = await graph.listEligibleGroupPage(actor);
  assert.deepEqual(first.groups, []);
  assert.ok(first.nextCursor);
  const second = await graph.listEligibleGroupPage(actor, { cursor: first.nextCursor });
  assert.deepEqual(second.groups, [eligible]);
  assert.equal(second.nextCursor, null);
  assert.equal(urls.length, 2);
});

test("本人所属と登録済みIDの観測にも環境の正規表現を適用する", async () => {
  const configured = { platformGroupId, groupNamePattern: "^TEAM_[A-Z]+_[0-9]{2}$" };
  const eligible = group(groupId, "TEAM_FINANCE_01");
  const { graph } = directory((url) => url.pathname.includes("transitiveMemberOf")
    ? Response.json({ value: [eligible, group(platformGroupId), group(clientId, "TEAM_FINANCE_02", false)] })
    : Response.json(url.pathname.endsWith(groupId) ? eligible : group(platformGroupId)), configured);
  assert.deepEqual(await graph.listEligibleGroups(actor), [eligible]);
  const observed = await graph.checkGroups(actor, [groupId, platformGroupId]);
  assert.equal(observed.get(groupId)?.status, "active");
  assert.equal(observed.get(platformGroupId)?.status, "ineligible");
});

test("配備済みパターンの変更後は、以前に取得した同じグループでも現在の規則で再判定する", async () => {
  const configured = { platformGroupId, groupNamePattern: "^ORG_" };
  const { graph } = directory((url) => url.pathname.includes("transitiveMemberOf")
    ? Response.json({ value: [group()] }) : Response.json(group()), configured);
  assert.deepEqual(await graph.listEligibleGroups(actor), [group()]);
  configured.groupNamePattern = "^TEAM_";
  assert.deepEqual(await graph.listEligibleGroups(actor), []);
  assert.equal((await graph.observeGroups([groupId])).get(groupId)?.status, "ineligible");
});

test("不正または非対応の正規表現はGraphを呼ぶ前に構成エラーとし、全許可へ置き換えない", async () => {
  for (const groupNamePattern of ["[", "(?=TEAM_)"]) {
    const { graph, urls, tokenRequests } = directory(() => { throw new Error("must not call"); }, { platformGroupId, groupNamePattern });
    await assert.rejects(graph.listEligibleGroups(actor), code("ConfigurationUnavailable"));
    await assert.rejects(graph.listEligibleGroupPage(actor), code("ConfigurationUnavailable"));
    await assert.rejects(graph.observeGroups([groupId]), code("ConfigurationUnavailable"));
    assert.equal(urls.length, 0);
    assert.equal(tokenRequests(), 0);
  }
});

test("移管先のcursorに任意のURLや検索条件を埋め込めない", async () => {
  const { graph, urls } = directory((url) => {
    const next = new URL(url); next.searchParams.set("$skiptoken", "two");
    return Response.json({ value: [group()], "@odata.nextLink": url.searchParams.has("$skiptoken") ? undefined : next.href });
  });
  const first = await graph.listEligibleGroupPage(actor, { search: "営業" });
  assert.ok(first.nextCursor);
  assert.deepEqual((await graph.listEligibleGroupPage(actor, { search: "営業", cursor: first.nextCursor })).groups, [group()]);
  await assert.rejects(graph.listEligibleGroupPage(actor, { search: "other", cursor: first.nextCursor }), code("InvalidRequest"));
  await assert.rejects(graph.listEligibleGroupPage(actor, { cursor: Buffer.from("https://outside.invalid/").toString("base64url") }), code("InvalidRequest"));
  await assert.rejects(graph.listEligibleGroupPage({ ...actor, tenantId: clientId }, { search: "営業", cursor: first.nextCursor }), code("AccessDenied"));
  assert.equal(urls.length, 2);
});

test("候補一覧の継続先でもsecurity条件・選択列・Graphエンドポイントを変更できない", async () => {
  const mutations = [
    (next: URL) => { next.hostname = "outside.invalid"; },
    (next: URL) => { next.pathname = `/v1.0/users/${objectId}/memberOf`; },
    (next: URL) => { next.searchParams.set("$filter", "securityEnabled eq false"); },
    (next: URL) => { next.searchParams.set("$select", "id,description"); },
    (next: URL) => { next.searchParams.append("$skiptoken", "different"); },
  ];
  for (const mutate of mutations) {
    const { graph, urls } = directory((url) => {
      const next = new URL(url); next.searchParams.set("$skiptoken", "two"); mutate(next);
      return Response.json({ value: [group()], "@odata.nextLink": next.href });
    });
    await assert.rejects(graph.listEligibleGroupPage(actor), code("AuthorizationUnavailable"));
    assert.equal(urls.length, 1);
  }
});

test("明示GETの404だけを削除とし、403・429・通信失敗・不正応答はunknownで保持する", async () => {
  for (const [kind, expected, error] of [
    ["deleted", "deleted", null], ["denied", "unknown", "DirectoryAccessDenied"], ["busy", "unknown", "DirectoryRateLimited"],
    ["network", "unknown", "DirectoryUnavailable"], ["invalid", "unknown", "DirectoryResponseInvalid"],
    ["renamed", "ineligible", null], ["active", "active", null],
  ] as const) {
    const { graph } = directory(() => {
      if (kind === "network") throw new Error("private error text");
      if (kind === "invalid") return Response.json(group(clientId));
      if (kind === "renamed") return Response.json(group(groupId, "renamed"));
      if (kind === "active") return Response.json(group());
      return new Response("private upstream text", { status: kind === "deleted" ? 404 : kind === "denied" ? 403 : 429 });
    });
    const observation = (await graph.checkGroups(actor, [groupId])).get(groupId)!;
    assert.equal(observation.status, expected);
    assert.equal(observation.errorCode, error);
    assert.equal(observation.checkedAt, "2026-10-30T00:00:00.000Z");
    assert.equal(JSON.stringify(observation).includes("private"), false);
    if (expected === "unknown" || expected === "deleted") assert.equal(observation.displayName, null);
  }
});

test("登録済みIDの照合は最大4並行で行い、別テナントは一件も照会しない", async () => {
  let concurrent = 0; let maximum = 0;
  const { graph, urls } = directory(async (url) => {
    maximum = Math.max(maximum, ++concurrent);
    await new Promise((resolve) => setTimeout(resolve, 2));
    concurrent--;
    return Response.json(group(url.pathname.split("/").at(-1)!));
  });
  const ids = Array.from({ length: 17 }, (_, index) => `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`);
  assert.equal((await graph.checkGroups(actor, ids)).size, 17);
  assert.ok(maximum <= 4);
  await assert.rejects(graph.listEligibleGroups({ ...actor, tenantId: clientId }), code("AccessDenied"));
  assert.equal(urls.length, 17);
});

test("管理者判定は設定済みIDの現在所属を毎回照会し、障害を非管理者へ変換しない", async () => {
  let member = true; let fail = false;
  const { graph, urls, tokenRequests } = directory((url, init) => {
    assert.equal(url.pathname, `/v1.0/users/${objectId}/checkMemberGroups`);
    assert.deepEqual(JSON.parse(String(init?.body)), { groupIds: [platformGroupId] });
    return fail ? new Response(null, { status: 403 }) : Response.json({ value: member ? [platformGroupId] : [] });
  });
  assert.equal(await graph.isPlatformAdmin(actor), true);
  member = false;
  assert.equal(await graph.isPlatformAdmin(actor), false);
  fail = true;
  await assert.rejects(graph.isPlatformAdmin(actor), code("AuthorizationUnavailable"));
  assert.equal(urls.length, 3);
  assert.equal(tokenRequests(), 1);
});
