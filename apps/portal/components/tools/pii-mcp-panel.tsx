"use client";
import { PiiRedactionPanel } from "./pii-redaction-panel";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** RESTと共通の文章入力を、MCPのガイド・契約・実行導線で提供します。 */
export function PiiMcpPanel(props: { dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  return <PiiRedactionPanel {...props} protocol="mcp" />;
}
