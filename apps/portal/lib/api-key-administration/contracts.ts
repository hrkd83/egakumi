/** 管理画面へ返す、秘密を含まない登録済み所有グループの状態です。 */
export type AdministrationGroup = {
  id: string;
  displayName: string;
  directoryStatus: "active" | "deleted" | "ineligible" | "unknown";
  lastCheckedAt: string | null;
  lastErrorCode: string | null;
  activeSubscriptions: number;
  affectedSubscriptions: number;
};

/** 一括確認が未実行のグループも、各行のnullで区別できる管理用一覧です。 */
export type ApiKeyAdministrationOverview = {
  groups: AdministrationGroup[];
  nextCursor: string | null;
  summary: ApiKeyAdministrationSummary;
  checkedAt: string | null;
  syncErrors: number;
};

/** ページの件数と混同させない、同じテナントの登録台帳全体の集計です。 */
export type ApiKeyAdministrationSummary = {
  totalGroups: number;
  activeGroups: number;
  deletedGroups: number;
  ineligibleGroups: number;
  unknownGroups: number;
  activeSubscriptions: number;
  affectedSubscriptions: number;
};

/** 同じ表示名のグループや名称変更で重複・飛びを生まない、グループID順の継続指定です。 */
export type ApiKeyAdministrationPageRequest = { cursor?: string };

/** 定期照合の結果です。購読やキーを変更したという意味は含みません。 */
export type ApiKeyGroupReconciliation = { checkedGroups: number; unknownGroups: number; completedAt: string };
