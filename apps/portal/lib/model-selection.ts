/** 提供元別に記憶するモデルの識別子です。キーや会話内容は含みません。 */
export type ModelSelection = { id: string; operationId?: string };

/** 分類と提供元で選択を分離し、別の提供元へ前回のモデルを持ち込みません。 */
export function modelSelectionScope(category: string, providerId: string): string {
  return `${category}/${providerId}`;
}

/**
 * 明示選択が公開中なら保持し、初回・廃止済みなら正本カタログの先頭を選びます。
 * @remarks カタログは最新世代を先頭に置き、同世代の派生モデルは管理者の登録順を使います。
 * モデル名の文字列比較では世代や品質を推測しません。
 */
export function resolveInitialModel<T extends { id: string }>(models: readonly T[], selection?: ModelSelection): T | undefined {
  return models.find((model) => model.id === selection?.id) ?? models[0];
}
