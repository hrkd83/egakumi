import { connection } from "next/server";

/** プロセスの起動を確認します。認証情報やSQLの状態は返しません。 */
export async function GET() {
  // 事前生成やキャッシュではなく、到達したプロセスから毎回応答します。
  await connection();
  return Response.json(
    { status: "ok", service: "egakumi-portal" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
