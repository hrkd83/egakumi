import "server-only";

import { createCipheriv, createDecipheriv, hkdfSync, randomBytes } from "node:crypto";
import { ConnectionSessionError, type PrivateState } from "./client.ts";

const VERSION = 1;
const IV_BYTES = 12;
const TAG_BYTES = 16;
/** 更新候補や認証取引を含めた保存状態の上限です。 */
export const MAX_CONNECTION_STATE_BYTES = 32 * 1024;

/** 暗号化鍵を用途別に分離し、Cookie署名鍵そのものを暗号化に流用しません。 */
function deriveKey(secret: string): Buffer {
  if (typeof secret !== "string" || secret.length < 32) throw new ConnectionSessionError("SessionStoreConfiguration", 503);
  return Buffer.from(hkdfSync("sha256", secret, "egakumi.portal.connection-state.v1", "aes-256-gcm/backend-session-state", 32));
}

/** SQLから読んだ状態も検査し、壊れた値や無制限に大きいJSONを利用しません。 */
function state(value: unknown): PrivateState {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ConnectionSessionError("SessionStateInvalid", 503);
  const item = value as PrivateState;
  if (!Number.isSafeInteger(item.revision) || item.revision < 1 || !["pending", "complete", "signed_out"].includes(item.status)
    || typeof item.current !== "string" || typeof item.sessionId !== "string" || !Number.isSafeInteger(item.generation) || item.generation < 0) {
    throw new ConnectionSessionError("SessionStateInvalid", 503);
  }
  return item;
}

/** 暗号文を利用者・購読・接続先の結び付けに固定し、別行への移植を拒否します。 */
function associatedData(binding: string): Buffer {
  if (!/^[a-f0-9]{64}$/.test(binding)) throw new ConnectionSessionError("SessionStateInvalid", 503);
  return Buffer.from(`egakumi.portal.connection-state:${VERSION}:${binding}`, "utf8");
}

/** 接続証明と更新候補を、版番号・ランダムIV・認証タグを含む一つの暗号文にします。 */
export function encryptConnectionState(binding: string, value: PrivateState, secret: string): Buffer {
  try {
    const plain = Buffer.from(JSON.stringify(state(value)), "utf8");
    if (plain.byteLength > MAX_CONNECTION_STATE_BYTES) throw new ConnectionSessionError("SessionStateInvalid", 503);
    const iv = randomBytes(IV_BYTES);
    const cipher = createCipheriv("aes-256-gcm", deriveKey(secret), iv);
    cipher.setAAD(associatedData(binding));
    const encrypted = Buffer.concat([cipher.update(plain), cipher.final()]);
    return Buffer.concat([Buffer.from([VERSION]), iv, cipher.getAuthTag(), encrypted]);
  } catch (error) {
    if (error instanceof ConnectionSessionError) throw error;
    throw new ConnectionSessionError("SessionStateInvalid", 503);
  }
}

/** 改ざん・異なる利用者への移植・未知の鍵版を固定エラーとして拒否します。 */
export function decryptConnectionState(binding: string, encrypted: Uint8Array, secret: string): PrivateState {
  try {
    const bytes = Buffer.from(encrypted);
    if (bytes.byteLength <= 1 + IV_BYTES + TAG_BYTES || bytes.byteLength > MAX_CONNECTION_STATE_BYTES + 1 + IV_BYTES + TAG_BYTES
      || bytes[0] !== VERSION) throw new ConnectionSessionError("SessionStateInvalid", 503);
    const decipher = createDecipheriv("aes-256-gcm", deriveKey(secret), bytes.subarray(1, 1 + IV_BYTES));
    decipher.setAAD(associatedData(binding));
    decipher.setAuthTag(bytes.subarray(1 + IV_BYTES, 1 + IV_BYTES + TAG_BYTES));
    const plain = Buffer.concat([decipher.update(bytes.subarray(1 + IV_BYTES + TAG_BYTES)), decipher.final()]);
    return state(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(plain)));
  } catch (error) {
    if (error instanceof ConnectionSessionError) throw error;
    throw new ConnectionSessionError("SessionStateInvalid", 503);
  }
}
