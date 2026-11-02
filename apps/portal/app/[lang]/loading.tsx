/**
 * 認証確認が終わるまでは、ログイン画面と同じ無地の背景だけを表示します。
 * @returns カタログの枠・メニュー・内容を含まない、Next.js標準の読み込み画面。
 * @remarks 未ログイン時も先に配信されるため、カタログのスケルトンはここに置きません。
 */
export default function Loading() {
  return <div className="min-h-screen bg-[#f5f8f6]" aria-busy="true" />;
}
