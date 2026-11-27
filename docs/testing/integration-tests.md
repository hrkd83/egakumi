# 構築後の動作テスト

Core・Entra登録・Gateway・[Portal配備](../setup/12-deploy-portal.md)を終えた環境で、[第13章の実接続受入](../setup/13-verify-deployment.md#gatewayとportalの実接続)として行います。採用したAPIと通信方式を[API契約](../reference/gateway/api-contracts.md)から選び、合成データと検証用の利用者・購読を用意します。 [Entraテストデータの準備](entra-fixtures.md)

## 実施順と記録

1. 公開DNS・HTTPS、AppGWからFirewallを経由するHTTPS、APIM・Portalのヘルス、閉域経路を確認する。 [ネットワークの受入](network-security-validation.md)
2. Gatewayの入口認証を確認し、利用する[Model Gateway](model-gateway-tests.md)と[Tool Gateway](tool-gateway-tests.md)を実行する。
3. Portalのサインイン、サインアウト、BearerでのAPI試用とGraphの本人照合を確認する。 [Portal認証の受入](portal-authentication-tests.md)
4. [WAF](waf-compatibility.md)、[アクセスログのクエリ保護](log-privacy.md)、[本文監査](../reference/audit/request-body-audit.md#azure受入)を、同じ試行の相関情報で照合する。
5. 検証用購読とセッションを回収し、結果・未確認・対象外を記録する。

実行時刻、実行した資材のrevision、適用範囲、応答状態、相関IDとログ参照は `.local/<environment>/evidence/` に保護保存します。資格情報と業務データを作業記録へ貼り付けません。環境変更や失敗条件の試験は隔離した検証環境で実施します。 [開発手順のテスト共通ルール](../../DEVELOPMENT.md#テストの共通ルール)

## Entra Gateway OAuthと専用open Productの追加受入

| 条件 | 確認すること |
| --- | --- |
| 発行者・対象・委任 | 認証テナントの正しいAPIアプリ向けv2ユーザートークンと `Gateway.Invoke` で成功し、別audience、別tenant、期限切れ、scopeなし、アプリ単独のトークンは拒否する |
| 認証の優先順 | 有効な購読キーは指定ProductまたはAPIの範囲で利用できる。キーを明示した要求では、キーが無効でもBearerへ切り替わらない |
| Productの所属 | 専用open Productと各APIの関連、例外キー用の所属、各APIの購読設定を読み戻す。未認証、対象外のAPI、購読停止後は拒否する |
| レート制限 | Bearerで別ユーザーのカウンターが混ざらないことを確認する。設定した値の境界を隔離環境で試験する。例外キーの共通上限は既定で無効 |
| 本人連携 | GraphへのBearer OBOと、例外キー利用時のCM本人接続を個別に試す |
| 監査 | 認証経路・利用主体と結果を照合し、資格情報やクエリ値の持ち出しがないことを確認する |

[Gatewayの認証・認可仕様](../reference/identity/gateway-authentication.md)を参照してください。
