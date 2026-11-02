import "server-only";
import { betterAuth } from "better-auth";
import { getAuthDatabase } from "@/lib/auth-database";
import { getAuthEnvironment } from "@/lib/auth-environment";
import { createPortalAuthOptions } from "@/lib/auth-options";

/** 構成の読み込みをリクエスト時まで遅らせる、サーバー専用の認証インスタンスです。 */
let auth: ReturnType<typeof initializeAuth> | undefined;

/** 定義したオプションの型を維持したままBetter Authを生成します。 */
function initializeAuth() {
  return betterAuth(createPortalAuthOptions(getAuthEnvironment(), getAuthDatabase()));
}

/**
 * サーバー内部で共有する認証処理を、最初のリクエストで初期化します。
 * @returns SQLを使用するBetter AuthのAPIとリクエストハンドラー。
 */
export function getAuth() {
  auth ??= initializeAuth();
  return auth;
}
