import type { CreateApiKeyInput } from "../../lib/api-keys/contracts.ts";

/** 発行入力の再試行を識別する、秘密情報を含まない状態です。 */
export type ApiKeyIssueAttempt = { fingerprint: string; key: string };

/**
 * 同じ入力には同じ冪等キーを使い、入力を変えたときだけ別の発行要求にします。
 * @remarks 通信失敗後も呼び出し側がこの状態を保持し、再試行で購読を二重作成しないようにします。
 */
export function prepareApiKeyIssueAttempt(
  input: Omit<CreateApiKeyInput, "idempotencyKey">,
  previous: ApiKeyIssueAttempt | null,
  newKey: () => string = () => crypto.randomUUID(),
): ApiKeyIssueAttempt {
  const fingerprint = JSON.stringify({
    groupId: input.groupId, displayName: input.displayName.trim(), clientType: input.clientType,
    systemName: input.systemName?.trim() || undefined, teamName: input.teamName?.trim() || undefined,
    companyId: input.companyId, contactEmail: input.contactEmail.trim(),
  });
  return previous?.fingerprint === fingerprint ? previous : { fingerprint, key: newKey() };
}
