import "server-only";
import type { Locale } from "./config";

const japanese = {
  title: "購読の同期", description: "Azure SQLの台帳を基準にAPIMの購読を一致させます。SQL未登録の購読は削除し、不足する購読は新しいキーで再作成します。",
  refresh: "一覧を更新", loading: "SQLとAPIMを照合しています…", search: "用途・会社・所有グループ・購読IDで検索",
  all: "すべて", differences: "差分のみ", selected: "選択", preview: "同期内容を確認", limit: "1回に10件まで選択できます。大量の場合は反映後に続けて同期してください。",
  source: "参照しているSQL", target: "反映先APIM", local: "ローカルSQL・反映不可", azure: "Azure SQL",
  readonly: "この環境は読み取り専用です。同期はAzureへ配備したPortalから実行してください。ローカルSQLの内容はAzureへ反映しません。",
  name: "用途／購読名", company: "会社・所有グループ", sql: "SQLの状態", apim: "APIMの状態", action: "同期時の処理", missing: "なし", noCompany: "会社未登録",
  actions: { none: "一致", update: "更新", recreate: "再作成（新しいキー）", delete: "削除", "delete-unregistered": "削除（SQL未登録）", blocked: "対象外／処理途中" },
  reactivate: "有効に戻ります", empty: "該当する購読はありません。", previous: "前へ", next: "次へ",
  review: "APIMへ反映する内容", reviewHelp: "SQLの値でAPIMを更新します。削除したキーは利用できなくなり、再作成したキーは利用アプリ側で差し替えが必要です。masterは対象外です。",
  expires: "確認内容の有効期限", confirm: "作成・更新・削除と再有効化の内容を確認しました。", apply: "同期を実行", cancel: "キャンセル", applying: "反映しています…",
  result: "実行結果", succeeded: "完了", pending: "削除完了待ち", failed: "失敗", history: "同期の履歴（最新100イベント）",
  historyHelp: "開始だけ残っている操作は、通信中断などで結果が未確認です。一覧を更新して差分を確認してください。",
  phases: { attempted: "開始", succeeded: "完了", pending: "削除待ち", failed: "失敗" },
  noHistory: "同期の履歴はありません。", actorHelp: "基盤管理グループの現在の所属を毎回確認します。キー値は取得・表示しません。",
  error: "照合または同期に失敗しました。権限・接続状況とSQL初期化の実行状況を確認し、一覧を更新してください。",
  conflict: "確認後に状態が変更されたか、確認の期限が切れています。一覧を更新して、同期内容を確認し直してください。",
  unknown: "—", matching: "一致", changes: "差分", total: "購読数", acknowledge: "確認してください", completed: "処理結果を確認してください。一覧を再照会しました。",
};
/** 同期画面の文言は、言語付きサーバールートから必要な分だけ渡します。 */
export type SubscriptionSyncText = typeof japanese;
const english: SubscriptionSyncText = {
  title: "Subscription sync", description: "Reconcile APIM subscriptions against the Azure SQL registry. Delete unregistered subscriptions and recreate missing subscriptions with new keys.",
  refresh: "Refresh inventory", loading: "Comparing SQL and APIM…", search: "Search purpose, company, owner group or subscription ID",
  all: "All", differences: "Differences only", selected: "Select", preview: "Review sync", limit: "Select up to 10 subscriptions per batch. Refresh after applying to continue with more subscriptions.",
  source: "SQL data source", target: "Target APIM", local: "Local SQL · read only", azure: "Azure SQL",
  readonly: "This environment is read only. Run sync from the Azure-deployed Portal. Local SQL data cannot be applied to Azure.",
  name: "Purpose / subscription name", company: "Company / owner group", sql: "SQL state", apim: "APIM state", action: "Sync action", missing: "Missing", noCompany: "No company",
  actions: { none: "Matched", update: "Update", recreate: "Recreate (new keys)", delete: "Delete", "delete-unregistered": "Delete (not in SQL)", blocked: "Excluded / in progress" },
  reactivate: "Will become active", empty: "No matching subscriptions.", previous: "Previous", next: "Next",
  review: "Review APIM changes", reviewHelp: "Apply SQL values to APIM. Deleted keys stop working. Recreated keys must be replaced in consuming applications. The master subscription is excluded.",
  expires: "Preview expires", confirm: "I have reviewed creation, updates, deletion and reactivation.", apply: "Run sync", cancel: "Cancel", applying: "Applying…",
  result: "Results", succeeded: "Succeeded", pending: "Deletion pending", failed: "Failed", history: "Sync history (latest 100 events)",
  historyHelp: "A start event without a result may indicate an interrupted operation. Refresh the inventory to check the current differences.",
  phases: { attempted: "Started", succeeded: "Succeeded", pending: "Pending deletion", failed: "Failed" },
  noHistory: "No sync history.", actorHelp: "Current platform administrator membership is checked on every request. Key values are never fetched or displayed.",
  error: "Comparison or sync failed. Check permissions, connectivity and SQL migration status, then refresh.",
  conflict: "The state changed after review, or the preview expired. Refresh and review the sync again.",
  unknown: "—", matching: "Matched", changes: "Differences", total: "Subscriptions", acknowledge: "Review required", completed: "Check the results below. The inventory has been refreshed.",
};
/** 日本語・英語で同じ確認項目を表示します。 */
export function getSubscriptionSyncText(locale: Locale): SubscriptionSyncText { return locale === "en" ? english : japanese; }
