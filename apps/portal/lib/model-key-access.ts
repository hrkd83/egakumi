import "server-only";

import { ApiKeyError, type ApiKeyActor, type ApiKeyOverview } from "./api-keys/contracts.ts";
import { modelKeyChoicesSchema } from "./model-key-choices.ts";
import { getExecutableModelTargets } from "./model-catalog.ts";
import { getExecutableToolTargets } from "./tool-execution-targets.ts";

/** 本人確認とキー候補の認可を、サーバー側の既存サービスへ委ねます。 */
export type ModelKeyChoiceDependencies = {
  readActor: () => Promise<ApiKeyActor | null>;
  readChoices: (actor: ApiKeyActor, apiId: string) => Promise<ApiKeyOverview>;
  readModels?: () => readonly { apiId: string }[] | Promise<readonly { apiId: string }[]>;
};

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };

/** 登録済みのモデル・ツールAPIだけを対象に、表示用の名前と台帳IDを返します。 */
export async function handleModelKeyChoices(request: Request, dependencies: ModelKeyChoiceDependencies): Promise<Response> {
  try {
    if (request.method !== "GET") return Response.json({ error: { code: "RequestNotAllowed" } }, { status: 405, headers: { ...responseHeaders, Allow: "GET" } });
    if (request.headers.get("sec-fetch-site") === "cross-site") return Response.json({ error: { code: "RequestNotAllowed" } }, { status: 403, headers: responseHeaders });
    const actor = await dependencies.readActor();
    if (!actor) return Response.json({ error: { code: "AuthenticationRequired" } }, { status: 401, headers: responseHeaders });
    const parameters = new URL(request.url).searchParams;
    const apiId = parameters.get("apiId");
    if (!apiId || parameters.getAll("apiId").length !== 1
      || !(await (dependencies.readModels ?? (() => [...getExecutableModelTargets(), ...getExecutableToolTargets()]))()).some((model) => model.apiId === apiId)) {
      return Response.json({ error: { code: "InvalidRequest" } }, { status: 400, headers: responseHeaders });
    }
    const overview = await dependencies.readChoices(actor, apiId);
    const groups = new Map(overview.groups.map((group) => [group.id.toLowerCase(), group.displayName]));
    const keys = overview.keys.filter((key) => key.state === "active" && groups.has(key.groupId.toLowerCase()))
      .map((key) => ({ id: key.id, displayName: key.displayName, companyName: key.companyName, groupName: groups.get(key.groupId.toLowerCase())! }));
    // 台帳や上流が持つ他のフィールドは公開しません。返す形も送信前に検証します。
    return Response.json(modelKeyChoicesSchema.parse({ keys }), { headers: responseHeaders });
  } catch (error) {
    const denied = error instanceof ApiKeyError && error.status < 500;
    return Response.json({ error: { code: denied ? "ApiKeyUnavailable" : "ApiKeyAccessUnavailable" } }, { status: denied ? 403 : 503, headers: responseHeaders });
  }
}
