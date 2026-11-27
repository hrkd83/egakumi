# API設定の準備

`core-api-settings`は、各APIの`<base />`より前で監査情報と制御設定を準備する共通フラグメントです。
共通認証が要求を拒否した場合にも、APIの接続先分類などを監査へ渡せるよう、先に実行します。

呼出元は、API定義から生成したBase64文字列を`gatewayApiSettingsSource`へ保存し、この部品を呼びます。
復元したデータは「API種類」「監査JSON」「制御JSON」の3行です。各JSONは生成時に1行へ整形し、JSON内の改行はエスケープされるため、3行の区切りと混ざりません。配備前に形式を検査します。API固有のデータは各APIに残し、読み込み処理を共有します。
JSON文字列をさらにJSONへ入れる二重のエスケープを避け、配備サイズと実行時のJSON解析を抑えます。
設定のJSON文字列をそのまま渡すことで、日付に見える文字列や数値の表現を共通化のために変換しません。通常HTTPの監査だけは、従来と同じ解析・操作別の選択を行います。

| 変数 | 内容 |
| --- | --- |
| `gatewayApiSettingsSource` | 呼出元APIが用意するBase64形式の設定データ |
| `gatewayApiSettings` | 一度だけBase64を復元した3行の設定データ |
| `gatewayAuditSettings` | 監査用の接続先分類・認証方式・計測対象など |
| `gatewayControlSettings` | APIの操作基本設定・モデル別設定とトークン計数対象 |

通常HTTPの`core-api-operation-settings`は、ここで準備したJSON文字列`gatewayControlSettings`を読み、`operationConstraints[context.Operation.Id]`から操作の基本設定を取得します。モデル別の項目は`modelSettings[モデル名]`に基本設定との差分として保存し、解決したモデルに対応する項目だけを上書きします。選択結果は`gatewayOperationSelection`へ渡します。

操作設定の取得不備を判定するのは、後続の`core-api-operation-settings`です。表や操作の設定を取得できない場合、モデル別設定の形式や本文サイズ設定が不正な場合は500と`OPERATION_SETTINGS_INVALID`を返します。先行する入力ヘッダー検査の不備とは区別します。

`modelSettings`と`modelAliases`を使わない操作は、本文を読まず基本設定を返します。個別の差分がないモデルも基本設定を使い、モデル選択部品では`allowedModels`による追加の利用許可は確認しません。別名は操作直下の`modelAliases`で一意に定義します。後段のモデル一覧・能力表の照合と固定URIの選択は、続く棚卸の対象です。

段階2ではAPI・操作とモデル名から内部設定を自動選択し、クライアントにprofileを選ばせません。`X-Egakumi-Profile`の有無・値は選択条件に使わず、値数・IDの検査や、そのヘッダーを理由とする403も撤去します。下流転送前の削除だけを清掃として残します。選択した操作制約、モデルalias、FLUXの固定上流URIと監査への受渡しは維持します。

内部`profiles`・`defaultProfile`・`modalities`は廃止し、一つのモデルから一つの設定を取得する形に整理します。`settingId`は既存監査の`profileId`へ転記するラベルで、クライアントが選択する値ではありません。購読利用枠profileやCMの接続`profileName`は変更対象外です。[操作設定の部品](../../request/core-api-operation-settings/README.md)で選択処理を確認できます。

通常HTTPでは、`context.Operation.Id`に対応する専用backendの情報を監査へ反映します。
WebSocket・外部MCP・生成MCPの入口は、API全体の監査設定を使います。
生成MCPの計測役割や元のREST操作ID、認証後のprofile反映、WebSocketモデルの更新は従来の処理が担当します。

この部品は本文・キー・トークンを読まず、認証、モデルの許可、入力制約、利用枠を判定しません。
CMの管理・ブラウザー受付など、AIモデル用設定を渡さないAPIへ呼出しを追加しません。

XMLが処理順を示し、同名C#が監査情報の選択式を担当します。
生成時にC#と説明コメントをXMLへ組み込み、Azureには完成したフラグメントを登録します。

`gatewayDecision`では認証方式の設定確認を配備前へ任せ、この箇所の`__AUTHORIZATION_BASE64__`復元・JSON解析を撤去します。APIMの検証済み購読があれば購読情報を、なければ共通認証済みのBearer本人情報を引き継ぎます。方式選択には購読の有無を使い、キー優先と不正キー拒否は先行する共通認証が決定します。後段の`gatewayTargetAllowed`には設定のBase64読取りが残り、続く棚卸で扱います。ソース、Azure資材の読戻し、実接続の受入は対象環境ごとに区別して確認します。

HTTP側の認証結果の組み立ては[core-api-authentication-result](../../authentication/core-api-authentication-result/README.md)へ分離します。準備済みの`gatewayOperationSelection`から監査用ラベルも直接読み、内部情報の引き渡し失敗は呼び出し元が500と監査へ整理します。
