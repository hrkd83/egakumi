import "server-only";

/**
 * JSONの文字列値とプロパティ名へ反射された既知の資格情報を、再エスケープせず検出します。
 * @remarks raw本文も任意で検査します。反復走査と参照済み集合を使い、深い構造や循環参照でも再帰しません。
 */
export function containsModelCredential(value: unknown, credential: string, raw?: string): boolean {
  if (!credential) return false;
  if (raw?.includes(credential)) return true;
  const pending: unknown[] = [value];
  const seen = new WeakSet<object>();
  while (pending.length) {
    const current = pending.pop();
    if (typeof current === "string") {
      if (current.includes(credential)) return true;
    } else if (typeof current === "number" && Number.isFinite(current)) {
      if (JSON.stringify(current).includes(credential)) return true;
    } else if (current && typeof current === "object" && !seen.has(current)) {
      seen.add(current);
      for (const name of Object.keys(current)) {
        if (name.includes(credential)) return true;
        pending.push((current as Record<string, unknown>)[name]);
      }
    }
  }
  return false;
}
