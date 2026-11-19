import "server-only";

/**
 * 転送ヘッダーや内部Hostを使わず、配備設定の公開originを検証して返します。
 * @remarks 開発時だけloopbackのHTTPを許可します。パスや資格情報は受け付けません。
 * @returns 公開HTTPS origin、または開発専用のloopback HTTP origin。
 */
export function getPortalPublicOrigin(): string {
  let url: URL;
  try {
    url = new URL(process.env.BETTER_AUTH_URL ?? "");
  } catch {
    throw new Error("BETTER_AUTH_URL must be a configured Portal origin");
  }
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash
      || (url.protocol !== "https:" && !(url.protocol === "http:" && local && process.env.NODE_ENV !== "production"))) {
    throw new Error("BETTER_AUTH_URL must be an HTTPS origin, or a local HTTP origin during development");
  }
  return url.origin;
}
