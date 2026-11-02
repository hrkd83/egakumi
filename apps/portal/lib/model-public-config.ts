import "server-only";

/** コード例に表示してよい公開Gatewayのオリジンだけを返します。秘密情報は読みません。 */
export function getPublicModelGatewayOrigin(environment: Readonly<Record<string, string | undefined>> = process.env): string | null {
  try {
    const url = new URL(environment.PORTAL_GATEWAY_URL ?? "");
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    return url.origin;
  } catch {
    return null;
  }
}
