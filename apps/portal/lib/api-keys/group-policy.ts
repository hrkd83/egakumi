import { z } from "zod";
import { RE2 } from "re2-wasm";

/** 単一テナント内でキー所有に利用する、配備時に確定したグループ規則です。 */
export type ApiKeyGroupPolicy = { platformGroupId: string; groupNamePattern: string };

const compiledPatterns = new Map<string, RE2>();

/** 正規表現の評価時間が入力に対して急増しないRE2を使い、少数の設定だけを再利用します。 */
function compileGroupNamePattern(pattern: string): RE2 {
  if (!pattern.trim() || pattern.length > 512 || /[\u0000-\u001f\u007f]/.test(pattern)) {
    throw new Error("Invalid group name pattern");
  }
  const cached = compiledPatterns.get(pattern);
  if (cached) return cached;
  const compiled = new RE2(pattern, "u");
  if (compiledPatterns.size >= 32) compiledPatterns.delete(compiledPatterns.keys().next().value!);
  compiledPatterns.set(pattern, compiled);
  return compiled;
}

/**
 * 空欄や未対応構文を許可せず、配備ジョブとアプリで同じRE2互換の正規表現を検証します。
 * @remarks 先頭一致は `^`、完全一致は `^...$` で指定します。通常の既定値へフォールバックしません。
 */
export const groupNamePatternSchema = z.string().min(1).max(512).refine((pattern) => {
  try { compileGroupNamePattern(pattern); return true; } catch { return false; }
}, { message: "有効なRE2互換のグループ名正規表現を指定してください。" });

/** Graphから直接確認できた、グループの必要最小限の属性です。 */
export type DirectoryGroup = { id: string; displayName: string; securityEnabled: boolean };

/** 所属グループと異なり、検索先候補には利用者の所属を要求しません。 */
export type DirectoryGroupPage = { groups: DirectoryGroup[]; nextCursor: string | null };

/** 通信障害を削除と区別して保持する、グループの現在の観測結果です。 */
export type DirectoryGroupObservation = {
  id: string;
  displayName: string | null;
  securityEnabled: boolean | null;
  status: "active" | "deleted" | "ineligible" | "unknown";
  checkedAt: string;
  errorCode: string | null;
};

/** セキュリティグループであることと、環境ごとの表示名の正規表現を両方満たす場合だけ許可します。 */
export function isEligibleApiKeyGroup(group: DirectoryGroup, policy: Pick<ApiKeyGroupPolicy, "groupNamePattern">): boolean {
  return group.securityEnabled && compileGroupNamePattern(policy.groupNamePattern).test(group.displayName);
}

/** 管理グループIDや対象規則の破損を、権限の緩い既定値へ置き換えません。 */
export const apiKeyGroupPolicySchema = z.object({
  platformGroupId: z.uuid().transform((id) => id.toLowerCase()),
  groupNamePattern: groupNamePatternSchema,
}).strict();
