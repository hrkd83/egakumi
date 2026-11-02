import "server-only";
import type { Locale } from "./config";

/** Graphの実アクセス、本人接続と組み込み案内に使う日本語文言です。 */
const ja = {
  connected: "Microsoft Graphは実APIに接続", real: "実API", mock: "サンプル", mockNotice: "Microsoft Graphは選択したAPIキーで実行します。ほかのツールは画面サンプルです。",
  description: "Microsoft Graph /me でサインインした本人のユーザー情報を取得します。API仕様と組み込み方を確認し、本人接続から属性の取得まで実際に試せます。",
  key: "実行に使うAPIキー", chooseKey: "APIキーを選択", refreshKeys: "一覧を更新", loadingKeys: "利用可能なAPIキーを確認しています。", noKeys: "このAPIを利用できる有効なキーがありません。", manageKeys: "APIキー管理へ",
  keyHelp: "現在の所属グループで利用できるキーを選択します。キーと本人接続セッションはPortalのサーバー内で保管します。",
  fixedRequest: "本人のユーザー情報を取得", fixedNote: "表示名・名・姓・UPN・オブジェクトID・役職・会社名・部署・従業員ID・勤務先所在地・メールアドレス・アカウントの有効状態を取得します。取得項目はGatewayの $select で固定しています。", profileId: "オブジェクトID", profileUpn: "ユーザープリンシパル名（UPN）", profileName: "表示名", unavailableField: "－",
  profileFields: { displayName: "表示名", givenName: "名", surname: "姓", userPrincipalName: "ユーザープリンシパル名（UPN）", id: "オブジェクトID", jobTitle: "役職", companyName: "会社名", department: "部署", employeeId: "従業員ID", officeLocation: "勤務先所在地", mail: "メールアドレス", accountEnabled: "アカウントが有効化されました" },
  accountEnabled: "有効", accountDisabled: "無効", attributesNote: "未設定・未返却・nullの項目は「－」で表示します。JSONの値はそのまま保持します。アカウントが無効の false は「無効」で表示し、欠損値とは区別します。",
  permissionsNote: "本人の /me を委任されたアクセス許可で読み取ります。返却される属性はMicrosoft Graphの権限・テナント設定・登録内容に依存します。表示用の属性値だけを認可や本人確認の根拠にしないでください。必要な権限は管理者が確認し、読み取りのために書き込み権限を追加しません。",
  permissionsReference: "Microsoft公式：ユーザーの取得と必要な権限", upstreamSelect: "GatewayからMicrosoft Graphへ送る取得項目",
  execute: "自分のユーザー情報を取得", running: "APIを確認しています…", login: "Microsoftで認証する", waiting: "Microsoft認証の完了を待っています。別タブで認証を完了すると、自動で本人のユーザー情報を取得します。",
  loginNote: "選択したキーでGraph APIを先に呼び出し、本人接続が必要な場合だけMicrosoft認証へ進みます。Portalと同じMicrosoftアカウントを使用してください。",
  check: "認証状況を確認", expired: "認証用URLの有効期限が切れました。「自分のユーザー情報を取得」からやり直してください。", timedOut: "認証状況の確認に失敗しました。接続を確認してから、認証状況をもう一度確認できます。",
  completed: "本人のユーザー情報を取得しました", sessionUpdatePending: "APIの取得は成功しました。セッション値の更新確定は次回のアクセス時に再試行します。再認証は不要です。",
  noResponse: "APIキーを選んで実行すると、実際のユーザー情報の応答をここに表示します。", requestId: "リクエストID", duration: "応答時間", response: "実行結果", guide: "組み込みガイド", schema: "リクエスト・応答仕様", copy: "コピー", copied: "コピーしました", copyFailed: "コピーできませんでした。コードを選択してコピーしてください。",
  guideIntro: "呼び出しアプリのバックエンドが、確認済み利用者・購読・本人接続セッションを紐付けて管理します。画面へ渡すのは認証用URL、完了状態、業務の応答だけです。",
  steps: ["バックエンドから購読キーを付けてGraph APIを呼ぶ。保存済み本人接続があれば、そのセッションも付ける。", "409 ConnectionRequired のときだけ、仮セッションと認証取引をバックエンドへ保存し、loginUrl を利用者へ表示する。", "バックエンドから認証状況を確認し、完了時の tenantId / ownerId とアプリのログイン本人が一致することを検証する。", "本人専用の接続セッションを付けてGraph APIを再実行する。日次の値更新はバックエンドで保存・確定する。"],
  lifetime: "既定では無操作7日・認証完了から最長30日。業務APIの正常な本人セッション検証が期限の延長対象です。完了確認や期限維持だけの通信では延長しません。",
  boundary: "以下は組み込み箇所を示すコード例です。認証済み本人の取得・購読の認可・暗号化した共有保存先は、呼び出しアプリで実装します。HTTPヘッダーの自己申告UPNだけで本人確認しないでください。",
  curlNote: "cURLはHTTP契約の確認用です。通常のアプリはバックエンドで認証・保存・更新を共通化します。秘密値をシェル履歴やログへ残さない環境で実行してください。",
  difyNote: "DifyのHTTPノードに個人セッションを固定登録せず、確認済みEntra本人へ紐づく中継BFF、または独自Toolで同じ契約を扱います。任意のuser入力・UPNを本人確認の代わりに使いません。",
  rotation: "日次更新候補は業務応答の X-Core-Connection-Update ヘッダー群で受け取ります。共有保存先へ先に保存し、/auth/connections/session/commit で確定します。成功済み業務を更新失敗で再実行しません。",
  languages: "実装言語", requestSchema: "Graphリクエスト", successSchema: "成功応答 · HTTP 200", authenticationSchema: "本人接続が必要な応答 · HTTP 409", secretNotice: "下記はGatewayのHTTP仕様です。pollSecret・仮セッション・更新候補はサーバー用で、ブラウザーへ転送しません。",
  requestHeaders: "購読キーと、保存済みの場合だけ本人接続セッションを付けます。", noRequestBody: "GETリクエストに本文はありません。",
  subscriptionHeader: "このAPIの利用を許可された購読キー。バックエンドだけで保管します。", sessionHeader: "認証済み本人に対応する接続セッション。初回は省略します。",
  connectionRequired: "本人接続が必要であることを示す固定コードです。", authenticationData: "バックエンドで保管する認証取引です。利用者画面へ渡すのはloginUrlだけです。",
  errors: {
    AuthenticationRequired: "Portalのサインインが必要です。ページを開き直してください。", AuthenticationUnavailable: "Portalの認証を確認できませんでした。時間をおいて再度お試しください。", RequestNotAllowed: "この操作は許可されていません。", InvalidRequest: "入力を確認してから実行してください。", GatewayNotConfigured: "Gatewayの接続先が設定されていません。",
    ApiKeyUnavailable: "選択したキーを利用できません。一覧を更新してください。", ApiKeyAccessUnavailable: "キーの利用権限を確認できませんでした。時間をおいて一覧を更新してください。", GatewayUnavailable: "Gatewayに接続できませんでした。時間をおいて再実行してください。", GatewayResponseInvalid: "Gatewayの応答を確認できませんでした。", GraphAccessDenied: "Microsoft Graphへのアクセスが許可されていません。", GraphReauthenticationRequired: "接続の再認証、または条件付きアクセスの確認が必要です。基盤管理者へ確認してください。",
    RateLimitExceeded: "呼び出し上限に達しました。時間をおいて再実行してください。", ConnectionIdentityMismatch: "Microsoft認証の本人がPortalのログイン本人と一致しません。同じアカウントで認証してください。", ConnectionStateUnavailable: "本人接続の保存状態を確認できませんでした。時間をおいて再実行してください。", SessionUpdateRequired: "本人接続の更新を完了できませんでした。時間をおいて再実行してください。",
  },
};

/** 日本語と同じ項目を持つ、Graph機能の表示文言です。 */
export type GraphToolDictionary = typeof ja;

/** Graph機能を英語で案内する、同じ公開情報の辞書です。 */
const en: GraphToolDictionary = {
  connected: "Microsoft Graph uses the live API", real: "Live API", mock: "Sample", mockNotice: "Microsoft Graph runs with your selected API key. Other tools are interface samples.",
  description: "Retrieve the signed-in user's information with Microsoft Graph /me. Explore the API contract and test the complete flow from connecting your account to reading user attributes.",
  key: "API key for this request", chooseKey: "Select an API key", refreshKeys: "Refresh list", loadingKeys: "Checking available API keys.", noKeys: "No active key is available for this API.", manageKeys: "Manage API keys",
  keyHelp: "Choose a key owned by one of your current groups. Key values and connection sessions remain on the Portal server.",
  fixedRequest: "Read your user information", fixedNote: "Retrieves display name, given name, surname, UPN, object ID, job title, company, department, employee ID, office location, email address, and account enabled state. Fields are fixed by the Gateway's $select.", profileId: "Object ID", profileUpn: "User principal name (UPN)", profileName: "Display name", unavailableField: "－",
  profileFields: { displayName: "Display name", givenName: "Given name", surname: "Surname", userPrincipalName: "User principal name (UPN)", id: "Object ID", jobTitle: "Job title", companyName: "Company name", department: "Department", employeeId: "Employee ID", officeLocation: "Office location", mail: "Email address", accountEnabled: "Account enabled" },
  accountEnabled: "Enabled", accountDisabled: "Disabled", attributesNote: "Unset, omitted, and null attributes display as －. The JSON preserves the original values. A false accountEnabled value displays as Disabled, separately from missing data.",
  permissionsNote: "Reads the signed-in user's /me with delegated permissions. Returned attributes depend on Microsoft Graph permissions, tenant configuration, and directory data. Do not use display attributes alone for authorization or identity verification. Administrators should verify required permissions without adding write permissions for a read-only task.",
  permissionsReference: "Microsoft documentation: get a user and required permissions", upstreamSelect: "Fields the Gateway requests from Microsoft Graph",
  execute: "Retrieve my user information", running: "Checking the API…", login: "Authenticate with Microsoft", waiting: "Waiting for Microsoft authentication. Once you finish in the other tab, your user information will load automatically.",
  loginNote: "The selected key calls Graph first. Microsoft authentication is requested only when a personal connection is needed. Use the same Microsoft account as your Portal sign-in.",
  check: "Check authentication", expired: "The login URL expired. Start again with Retrieve my user information.", timedOut: "Authentication status could not be checked. Check your connection and try checking again.",
  completed: "User information retrieved", sessionUpdatePending: "The API request succeeded. Session update confirmation will be retried on your next request. You do not need to authenticate again.",
  noResponse: "Select an API key and run the request to see the actual user information response here.", requestId: "Request ID", duration: "Duration", response: "Result", guide: "Integration guide", schema: "Request and response", copy: "Copy", copied: "Copied", copyFailed: "Copy failed. Select the code and copy it manually.",
  guideIntro: "Your application's backend binds the verified user, subscription, and personal connection session. Only the login URL, completion status, and business response are sent to the UI.",
  steps: ["Call Graph from your backend with a subscription key and, when available, the user's saved connection session.", "Only on 409 ConnectionRequired, save the provisional session and authentication transaction on your backend, then display loginUrl.", "Check status on the backend and compare the completed tenantId / ownerId with your verified application user.", "Retry Graph with that user's connection session. Store and confirm daily value updates on the backend."],
  lifetime: "Defaults are 7 days of inactivity and a maximum of 30 days after authentication. Successful session validation for business requests can extend inactivity expiry. Status polling and keepalive traffic cannot.",
  boundary: "These examples show integration points. Implement verified identity, subscription authorization, and encrypted shared storage in your application. A caller-supplied UPN header is not proof of identity.",
  curlNote: "cURL illustrates the HTTP contract. Applications should centralize authentication, storage, and rotation on their backend. Avoid exposing secrets through shell history or logs.",
  difyNote: "Do not hardcode personal sessions in Dify HTTP nodes. Use a BFF or custom Tool bound to a verified Entra user. Arbitrary user inputs and UPN strings do not verify identity.",
  rotation: "Daily update candidates arrive in the X-Core-Connection-Update response headers. Save them first in shared storage, then confirm through /auth/connections/session/commit. Do not rerun successful business requests when update confirmation fails.",
  languages: "Implementation language", requestSchema: "Graph request", successSchema: "Success · HTTP 200", authenticationSchema: "Connection required · HTTP 409", secretNotice: "These are Gateway HTTP contracts. pollSecret, provisional sessions, and rotation candidates are server-only and must not be forwarded to browsers.",
  requestHeaders: "Include the authorized subscription key and the personal connection session when one is already saved.", noRequestBody: "The GET request has no body.",
  subscriptionHeader: "A subscription key authorized for this API. Store it only on your backend.", sessionHeader: "The connection session bound to the verified user. Omit it on the first request.",
  connectionRequired: "A fixed error code indicating that a personal connection is required.", authenticationData: "An authentication transaction retained by the backend. Only loginUrl is passed to the user interface.",
  errors: {
    AuthenticationRequired: "Sign in to the Portal again.", AuthenticationUnavailable: "Portal authentication could not be checked. Try again later.", RequestNotAllowed: "This request is not allowed.", InvalidRequest: "Check the input and try again.", GatewayNotConfigured: "The Gateway origin is not configured.",
    ApiKeyUnavailable: "This API key is unavailable. Refresh the list.", ApiKeyAccessUnavailable: "API key access could not be checked. Refresh the list later.", GatewayUnavailable: "The Gateway could not be reached. Try again later.", GatewayResponseInvalid: "The Gateway returned an unexpected response.", GraphAccessDenied: "Access to Microsoft Graph was denied.", GraphReauthenticationRequired: "Reauthentication or a conditional access check is required. Contact your platform administrator.",
    RateLimitExceeded: "The request limit was reached. Try again later.", ConnectionIdentityMismatch: "The Microsoft account does not match your Portal account. Authenticate with the same account.", ConnectionStateUnavailable: "The saved connection state could not be checked. Try again later.", SessionUpdateRequired: "The connection update could not be completed. Try again later.",
  },
};

/** URLで検証済みの言語に対応する辞書を、サーバーで選択します。 */
export function getGraphToolDictionary(locale: Locale): GraphToolDictionary { return locale === "en" ? en : ja; }
