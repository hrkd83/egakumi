/** APIM Classicが本文をバッファーできる技術上限です。モデルの入力枠とは別です。 */
export const MAX_BUFFERED_BODY_BYTES = 500 * 1_048_576;
/** 対応するChromium/V8の単一文字列の技術上限です。入力文字数の独自枠は設けません。 */
export const MAX_RUNTIME_STRING_CHARACTERS = 0x1fffffe8;
/** JavaScript配列が表現できる要素数です。ストリームの累積件数制限には使いません。 */
export const MAX_RUNTIME_ARRAY_LENGTH = 0xffffffff;

/** 期限を明示した呼出しだけ時間制限を設け、通常は利用者の中止と通信経路の期限に従います。 */
export function modelDeadline(timeoutMs?: number): AbortSignal {
  return timeoutMs === undefined ? new AbortController().signal : AbortSignal.timeout(timeoutMs);
}
