# 利用回数上限の429応答

利用回数枠の上限超過を、既存の監査と本文なし429へ揃える生成時inlineのsnippetです。Azure Policy fragmentを新しく登録しません。XMLは対象エラーの選択・監査・応答のフローを持ち、同名C#は`gatewayRateSafeRetryAfter`の値を作る式だけを所有します。

## 選択・入出力

| 項目 | 動作 |
| --- | --- |
| `context.LastError.PolicyId`・`Reason` | `core-rate-limit`かつ`RateLimitExceeded`だけを処理する |
| `gatewayRateRetryAfter` | 整数として読め、1～300秒の値だけを使う |
| `gatewayRateDecision.renewalPeriodSeconds` | Retry-Afterの取得値を使えない場合の既存の代替値 |
| `gatewayRateDecision`の欠落 | 従来どおり代替値を60秒とする |
| `gatewayRateSafeRetryAfter` | 応答の`Retry-After`へ渡す文字列 |
| 監査 | `rejected`・`RATE_LIMIT_EXCEEDED`・429を既存監査部品へ渡す |

提供元が返した429、別のPolicyId、認証失敗はこの部品の対象にしません。応答は429・`Caller request rate exceeded`、要求ID・専用エラーコード・`Cache-Control: no-store`を維持し、本文は付けません。

## MOD-104の判断と実施

元の`rate-limit-error.xml`から、Retry-Afterを作る1式だけを`__RATE_LIMIT_ERROR_RETRY_AFTER_CSHARP__`へ分離しました。条件・値の範囲・代替値・監査・応答は変更していません。代替値のJSON解析や型変換に例外が起きた場合の既存の扱いも維持し、新しいcatchや補完は追加しません。

429の選択と応答は必要な処理として維持し、同名XML／C#の正規配置へ揃えました。Bicep・Python・試験の参照を切り替え、直下の旧XMLを撤去しました。配備と保護sourceは同じ生成済みsnippetを使います。正規markerは同名XMLだけが直接所有し、Razor検査も正本を展開します。

関連73件の初回実行は72件成功・1件失敗でした。失敗はprotocolの先行する専用on-errorを考慮しない既存試験だったため、base直後に429部品があることを確認する形へ試験だけを更新し、該当1件を別実行して成功しました。回数枠・CM・WebSocket、同名所有、Razor、実Bicep生成と保護source・容量を確認しています。移設前との構造・属性・実式トークンはfull／compact／omitの3生成モードで一致しました。

この部品のローカル棚卸は完了です。実APIMの分散カウンター、Azure反映、工程1・2全体の受入は未実施・未完了です。
