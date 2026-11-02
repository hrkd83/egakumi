import type { ManagedApiKeySubscription } from "../api-keys/apim.ts";
import type { CanonicalSubscription, SubscriptionDifference } from "./contracts.ts";

/** APIM表示名は通常購読の会社名、構築用購読は資材の用途名を使います。 */
export function desiredSubscription(row: CanonicalSubscription): SubscriptionDifference["desired"] {
  if (!["active", "suspended"].includes(row.state)) return null;
  const name = row.creationSource === "deployment" ? row.purpose : row.companyName;
  if (!name?.trim()) return null;
  let displayName = name.slice(0, 100);
  if (/[\uD800-\uDBFF]$/.test(displayName)) displayName = displayName.slice(0, -1);
  return { displayName, productId: row.productId, state: row.state as "active" | "suspended" };
}

/** 完全取得した二つの一覧を比較し、APIMのみの購読もSQLに合わせて削除対象にします。 */
export function subscriptionDifferences(sqlRows: CanonicalSubscription[], apimRows: ManagedApiKeySubscription[], scopeFor: (id: string) => string): SubscriptionDifference[] {
  const observed = new Map(apimRows.map((row) => [row.id.toLowerCase(), row]));
  const rows: SubscriptionDifference[] = sqlRows.map((sql) => {
    const current = observed.get(sql.apimSubscriptionId.toLowerCase()) ?? null;
    observed.delete(sql.apimSubscriptionId.toLowerCase());
    const apim = current ? { id: current.id, displayName: current.displayName, scope: current.scope, state: current.state } : null;
    const desired = desiredSubscription(sql);
    const protectedId = sql.apimSubscriptionId.toLowerCase() === "master";
    let action: SubscriptionDifference["action"] = "blocked";
    if (!protectedId) {
      if (sql.state === "deleted") action = current ? "delete" : "none";
      else if (desired) action = !current ? "recreate"
        : current.displayName !== desired.displayName || current.scope.toLowerCase() !== scopeFor(desired.productId).toLowerCase() || current.state !== desired.state ? "update" : "none";
    }
    return { id: sql.id, sql, apim, desired, action,
      reactivates: action === "update" && desired?.state === "active" && current?.state !== "active" };
  });
  for (const row of observed.values()) rows.push({ id: `apim:${row.id}`, sql: null,
    apim: { id: row.id, displayName: row.displayName, scope: row.scope, state: row.state }, desired: null,
    action: row.id.toLowerCase() === "master" ? "blocked" : "delete-unregistered", reactivates: false });
  return rows;
}
