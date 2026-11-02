import "server-only";
import type { Locale } from "./config";

const japanese = {
  title: "会社情報の管理", eyebrow: "PLATFORM ADMINISTRATION",
  description: "APIキーの利用元として選択する会社を管理します。会社は統計・棚卸のための情報で、キーの所有権はEntraグループで管理します。",
  adminOnly: "基盤管理者のみ", add: "会社を登録", refresh: "更新", search: "会社名で検索",
  loading: "会社情報を読み込んでいます…", empty: "会社がまだ登録されていません",
  emptyHelp: "利用する会社を登録すると、APIキー発行時の選択肢に表示されます。",
  noMatches: "条件に一致する会社はありません。", name: "会社名", status: "状態", updatedAt: "最終更新", actions: "操作",
  active: "選択可能", inactive: "新規選択を停止中", edit: "編集", allStatuses: "すべての状態",
  activeOnly: "選択可能な会社", inactiveOnly: "新規選択を停止中の会社", count: "登録会社", activeCount: "選択可能",
  createTitle: "会社を登録", editTitle: "会社情報を編集", namePlaceholder: "例：株式会社サンプル",
  nameHelp: "正式名称や、社内で識別しやすい会社名を160文字以内で登録します。",
  availability: "新しいAPIキーの会社選択", available: "選択できる", unavailable: "選択できない",
  archiveHelp: "選択を停止しても、既存のAPIキーと会社の関連付けは保持されます。キーの停止・削除は行いません。",
  cancel: "キャンセル", save: "保存", saving: "保存しています…", close: "閉じる",
  saved: "会社情報を保存しました。", invalid: "会社名を1〜160文字で入力してください。",
  error: "会社情報を処理できませんでした。時間をおいて、もう一度お試しください。",
  forbidden: "現在の基盤管理グループ所属を確認できません。権限と接続状況を確認してください。",
  duplicate: "同じ名前の会社が既に登録されています。一覧を更新して確認してください。",
  conflict: "別の操作で会社情報が更新されています。入力内容を控えてから一覧を更新し、最新の情報でやり直してください。",
};

/** 会社管理画面にサーバーから渡す、言語共通の文言です。 */
export type CompanyAdministrationText = typeof japanese;

const english: CompanyAdministrationText = {
  title: "Company directory", eyebrow: "PLATFORM ADMINISTRATION",
  description: "Manage companies selected when issuing API keys. Companies support reporting and reviews; Entra groups retain ownership of keys.",
  adminOnly: "Platform administrators only", add: "Register company", refresh: "Refresh", search: "Search company names",
  loading: "Loading companies…", empty: "No companies registered",
  emptyHelp: "Register a company to make it available when issuing API keys.",
  noMatches: "No matching companies.", name: "Company name", status: "Status", updatedAt: "Last updated", actions: "Actions",
  active: "Available", inactive: "Unavailable for new keys", edit: "Edit", allStatuses: "All statuses",
  activeOnly: "Available companies", inactiveOnly: "Unavailable companies", count: "Registered companies", activeCount: "Available",
  createTitle: "Register company", editTitle: "Edit company", namePlaceholder: "Example: Example Corporation",
  nameHelp: "Use a recognizable company name, up to 160 characters.",
  availability: "Company selection for new API keys", available: "Available", unavailable: "Unavailable",
  archiveHelp: "Making a company unavailable preserves its existing key associations. Existing keys are not suspended or deleted.",
  cancel: "Cancel", save: "Save", saving: "Saving…", close: "Close", saved: "Company information saved.",
  invalid: "Enter a company name between 1 and 160 characters.",
  error: "Company information could not be processed. Please try again later.",
  forbidden: "Your current platform administrator membership could not be confirmed. Check your access and connectivity.",
  duplicate: "A company with this name is already registered. Refresh the directory to check it.",
  conflict: "This company was updated by another operation. Keep a copy of your input, refresh the directory, and try again with the latest record.",
};

/** Next.jsの言語付きルートで検証済みの言語に対応する文言だけを渡します。 */
export function getCompanyAdministrationText(locale: Locale): CompanyAdministrationText {
  return locale === "en" ? english : japanese;
}
