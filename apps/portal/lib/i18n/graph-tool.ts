import "server-only";
import type { Locale } from "./config";

/** Graphの実アクセス、本人接続と組み込み案内に使う日本語文言です。 */
const ja = {
  connected: "Microsoft Graphは実APIに接続", real: "実API", mock: "サンプル", mockNotice: "Microsoft GraphはGateway経由で本人の情報を取得します。",
  description: "Microsoft Graph /me でサインインした本人のユーザー情報を取得します。API仕様と組み込み方を確認し、本人接続から属性の取得まで実際に試せます。",
  fixedRequest: "本人のユーザー情報を取得", fixedNote: "表示名・名・姓・UPN・オブジェクトID・役職・会社名・部署・従業員ID・勤務先所在地・メールアドレス・アカウントの有効状態を取得します。取得項目はGatewayの $select で固定しています。", profileId: "オブジェクトID", profileUpn: "ユーザープリンシパル名（UPN）", profileName: "表示名", unavailableField: "－",
  profileFields: { displayName: "表示名", givenName: "名", surname: "姓", userPrincipalName: "ユーザープリンシパル名（UPN）", id: "オブジェクトID", jobTitle: "役職", companyName: "会社名", department: "部署", employeeId: "従業員ID", officeLocation: "勤務先所在地", mail: "メールアドレス", accountEnabled: "アカウントが有効化されました" },
  accountEnabled: "有効", accountDisabled: "無効", attributesNote: "未設定・未返却・nullの項目は「－」で表示します。JSONの値はそのまま保持します。アカウントが無効の false は「無効」で表示し、欠損値とは区別します。",
  permissionsNote: "本人の /me を委任されたアクセス許可で読み取ります。返却される属性はMicrosoft Graphの権限・テナント設定・登録内容に依存します。表示用の属性値だけを認可や本人確認の根拠にしないでください。必要な権限は管理者が確認し、読み取りのために書き込み権限を追加しません。",
  permissionsReference: "Microsoft公式：ユーザーの取得と必要な権限", upstreamSelect: "GatewayからMicrosoft Graphへ送る取得項目",
  execute: "自分のユーザー情報を取得", running: "APIを確認しています…",
  loginNote: "Gatewayは本人の委任権限を使ってMicrosoft Graphに接続します。",
  completed: "本人のユーザー情報を取得しました",
  noResponse: "本人のユーザー情報を実行結果に表示します。", requestId: "リクエストID", duration: "応答時間", response: "実行結果", guide: "組み込みガイド", schema: "リクエスト・応答仕様", copy: "コピー", copied: "コピーしました", copyFailed: "コピーできませんでした。コードを選択してコピーしてください。",
  guideIntro: "アプリでサインインした本人のアクセストークンを使ってGatewayを呼び出します。",
  steps: ["本人のGateway.Invokeアクセストークンを取得する。", "GatewayのGraph APIを呼び出す。", "追加認証が必要な場合は、同じ本人で再認証する。"],
  boundary: "呼び出しアプリのバックエンドでサインイン中の本人とトークンを照合します。",
  curlNote: "cURLは取得済みのアクセストークンで疎通を確認する例です。トークンをシェル履歴やログに残さないでください。",
  difyNote: "Difyでは、本人に紐付いたトークンを取得する独自Toolまたは認証済みの中継を使います。",
  languages: "実装言語", requestSchema: "Graphリクエスト", successSchema: "成功応答 · HTTP 200", secretNotice: "アクセストークンはサーバー側で管理し、画面やログに出さないでください。",
  requestHeaders: "AuthorizationヘッダーにGateway.InvokeのBearerトークンを付けます。", noRequestBody: "GETリクエストに本文はありません。",
  reauthenticate: "Graphの追加認証を行う",
  errors: {
    AuthenticationRequired: "Portalのサインインが必要です。ページを開き直してください。", AuthenticationUnavailable: "Portalの認証を確認できませんでした。時間をおいて再度お試しください。", RequestNotAllowed: "この操作は許可されていません。", InvalidRequest: "入力を確認してから実行してください。", GatewayNotConfigured: "Gatewayの接続先が設定されていません。",
    ApiAuthenticationRequired: "試用のアクセストークン認証へ再接続してください。", ApiAuthenticationBusy: "試用の認証を更新しています。少し待ってから実行してください。", ApiAuthenticationUnavailable: "試用のアクセストークンを確認できませんでした。時間をおいて再度お試しください。",
    GatewayUnavailable: "Gatewayに接続できませんでした。時間をおいて再実行してください。", GatewayResponseInvalid: "Gatewayの応答を確認できませんでした。", GraphAccessDenied: "Microsoft Graphへのアクセスが許可されていません。", GraphReauthenticationRequired: "接続の再認証、または条件付きアクセスの確認が必要です。基盤管理者へ確認してください。",
    ConnectionIdentityMismatch: "Graphの応答がサインインした本人と一致しません。",
    RateLimitExceeded: "呼び出し上限に達しました。時間をおいて再実行してください。",
  },
};

/** 日本語と同じ項目を持つ、Graph機能の表示文言です。 */
export type GraphToolDictionary = typeof ja;

/** Graph機能を英語で案内する、同じ公開情報の辞書です。 */
const en: GraphToolDictionary = {
  connected: "Microsoft Graph uses the live API", real: "Live API", mock: "Sample", mockNotice: "Microsoft Graph retrieves your profile through the Gateway.",
  description: "Retrieve the signed-in user's information with Microsoft Graph /me. Explore the API contract and test the complete flow from connecting your account to reading user attributes.",
  fixedRequest: "Read your user information", fixedNote: "Retrieves display name, given name, surname, UPN, object ID, job title, company, department, employee ID, office location, email address, and account enabled state. Fields are fixed by the Gateway's $select.", profileId: "Object ID", profileUpn: "User principal name (UPN)", profileName: "Display name", unavailableField: "－",
  profileFields: { displayName: "Display name", givenName: "Given name", surname: "Surname", userPrincipalName: "User principal name (UPN)", id: "Object ID", jobTitle: "Job title", companyName: "Company name", department: "Department", employeeId: "Employee ID", officeLocation: "Office location", mail: "Email address", accountEnabled: "Account enabled" },
  accountEnabled: "Enabled", accountDisabled: "Disabled", attributesNote: "Unset, omitted, and null attributes display as －. The JSON preserves the original values. A false accountEnabled value displays as Disabled, separately from missing data.",
  permissionsNote: "Reads the signed-in user's /me with delegated permissions. Returned attributes depend on Microsoft Graph permissions, tenant configuration, and directory data. Do not use display attributes alone for authorization or identity verification. Administrators should verify required permissions without adding write permissions for a read-only task.",
  permissionsReference: "Microsoft documentation: get a user and required permissions", upstreamSelect: "Fields the Gateway requests from Microsoft Graph",
  execute: "Retrieve my user information", running: "Checking the API…",
  loginNote: "The Gateway connects to Microsoft Graph with your delegated permissions.",
  completed: "User information retrieved",
  noResponse: "Your user information appears here after the request.", requestId: "Request ID", duration: "Duration", response: "Result", guide: "Integration guide", schema: "Request and response", copy: "Copy", copied: "Copied", copyFailed: "Copy failed. Select the code and copy it manually.",
  guideIntro: "Call the Gateway with an access token for the signed-in user.",
  steps: ["Acquire a Gateway.Invoke access token for the signed-in user.", "Call the Graph API through the Gateway.", "If prompted, complete additional authentication with the same account."],
  boundary: "On your backend, bind the access token to the signed-in user.",
  curlNote: "Use cURL with an existing access token. Keep the token out of shell history and logs.",
  difyNote: "Use a custom Tool or authenticated backend that can obtain a token for the signed-in user.",
  languages: "Implementation language", requestSchema: "Graph request", successSchema: "Success · HTTP 200", secretNotice: "Keep access tokens on the server and out of UI and logs.",
  requestHeaders: "Send a Gateway.Invoke access token in the Authorization header as a Bearer token.", noRequestBody: "The GET request has no body.",
  reauthenticate: "Complete additional Graph authentication",
  errors: {
    AuthenticationRequired: "Sign in to the Portal again.", AuthenticationUnavailable: "Portal authentication could not be checked. Try again later.", RequestNotAllowed: "This request is not allowed.", InvalidRequest: "Check the input and try again.", GatewayNotConfigured: "The Gateway origin is not configured.",
    ApiAuthenticationRequired: "Reconnect access token authentication to try this API.", ApiAuthenticationBusy: "API authentication is refreshing. Wait a moment and try again.", ApiAuthenticationUnavailable: "The API access token could not be checked. Try again later.",
    GatewayUnavailable: "The Gateway could not be reached. Try again later.", GatewayResponseInvalid: "The Gateway returned an unexpected response.", GraphAccessDenied: "Access to Microsoft Graph was denied.", GraphReauthenticationRequired: "Reauthentication or a conditional access check is required. Contact your platform administrator.",
    ConnectionIdentityMismatch: "The Graph response does not match the signed-in user.",
    RateLimitExceeded: "The request limit was reached. Try again later.",
  },
};

/** URLで検証済みの言語に対応する辞書を、サーバーで選択します。 */
export function getGraphToolDictionary(locale: Locale): GraphToolDictionary { return locale === "en" ? en : ja; }
