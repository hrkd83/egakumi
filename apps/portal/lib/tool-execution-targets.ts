/** 購読の利用権限を照会できる実装済みTool APIです。任意のAPI識別子は受け付けません。 */
export function getExecutableToolTargets(): { apiId: string }[] {
  return [{ apiId: "pii-v1" }, { apiId: "pii-v1-mcp" }, { apiId: "document-analysis-v1" }];
}
