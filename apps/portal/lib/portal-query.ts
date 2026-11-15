/**
 * 認証済みのAPI要求で、入口が明示したquery名だけを各1件受け付けます。
 * @param request - 本人認証を通過した要求。
 * @param allowedNames - 既存のHTTP契約で許可された名前。省略時は引数を受け付けません。
 * @returns 未許可名・空名・重複がなければtrue。query禁止の入口は空の区切りも拒否し、URLの解析失敗はfalseです。
 * @remarks 値の検査は各入口の既存schemaに任せます。本文とqueryを変更せず、名前や値を記録しません。
 * ページ、認証コールバック、Next.js内部要求には適用しません。
 */
export function isAllowedPortalQuery(request: Request, allowedNames: readonly string[] = []): boolean {
  try {
    const url = new URL(request.url);
    if (allowedNames.length === 0) return url.search.length === 0;
    const parameters = url.searchParams;
    for (const name of parameters.keys()) {
      if (!name || !allowedNames.includes(name) || parameters.getAll(name).length !== 1) return false;
    }
    return true;
  } catch { return false; }
}
