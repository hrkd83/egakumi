# CodexのGateway接続と端末展開

工程7では、[登録済みのegakumi-codex](../setup/02b-entra-codex.md)を使い、CodexからGatewayのResponses APIへ接続します。この手順では端末設定の照合用資材を保護領域に生成し、新規構築した環境での受入に使います。実際の対象端末・対象バージョン・配布方式は端末管理者が決めます。

## EntraとGatewayの受入条件

1. 認証用Entraに`egakumi-api`と`egakumi-codex`があり、別々のClient IDであることを確認します。CodexアプリはSingle tenantのMobile and desktop Public clientとし、`http://127.0.0.1:43127/callback`を登録します。ポートを変える場合は登録URIと生成オプションを揃えます。
2. `egakumi-api`の委任権限`Gateway.Invoke`をCodexアプリへ付与し、管理者同意、利用者割当、条件付きアクセスを確認します。本人認証はブラウザーで開始するAuthorization Code＋PKCEです。`offline_access`を更新用として要求します。
3. 新規Azure構築後、App Gatewayの公開入口を通して、ResponsesのBearer認可、レスポンスのSSE、期待するモデルへのルーティングを先に受け入れます。Codexの配布範囲はEntraの同意・割当と端末管理で制御します。 [Gateway受入試験](../testing/integration-tests.md#entra-gateway-oauthと専用open-productの追加受入)

## 資材を生成する

生成CLIは、選んだ環境の`.local/<environment>/params.local.json`から`codex.clientId`、認証テナント、`gateway.authentication.entra.audience`、`gateway.publicHostName`を読みます。認証テナントは`entraTenantId`を優先し、省略時は構築入力と同じく`azureTenantId`を使います。現行[Responses API定義](../../apps/apim/apis/openai-inference-v1.json)からベースパスとモデル名・別名を照合します。要求先は`https://<GATEWAY_HOST>/models/openai/v1/responses`になります。旧profileヘッダー、固定キー、固定トークン、外部の認証コマンドは配布設定に含めません。

併用端末向けの例です。値は公開用の例です。

```sh
python3 -B scripts/prepare_codex_endpoint.py \
  --environment test --revision pilot01 --mode side-by-side --model gpt-6-sol
```

| 選択 | 生成する設定 | 配布後の挙動 |
| --- | --- | --- |
| `--mode side-by-side` | `config.toml`にGateway providerの定義。`aigateway.config.toml`に選択するprovider・モデル・任意のカタログ | CLIは対象版の`codex --profile aigateway`で会社Gatewayを選択。Desktopはprovider切替とモデル選択を対象版で確認する |
| `--mode dedicated` | `config.toml`にproviderの定義と、会社Gatewayを選ぶ既定値 | 会社専用端末での既定接続に使用する |
| `--mode dedicated --enforce-provider` | さらに完全なprovider定義と選択を含む`requirements.toml` | 会社Gatewayを固定する方針を採用した専用端末で使用する。既存のクラウド・端末管理設定と組み合わせた実効値を確認する |

生成先は`.local/<environment>/generated/codex/<revision>/`です。新しい`--revision`を指定して生成し、保護権限と内容を確認します。worktreeから主チェックアウトの入力を使う場合は`--runtime-root`で主チェックアウトの絶対パスを明示します。実値を含む生成結果はローカルの照合に使います。配布に使う設定は、端末管理者が社内の管理手順で用意します。CLIは端末の設定変更やAzure操作を行いません。

### Windowsで独自モデルカタログを使う場合

対象バージョンに対応するCodex形式のカタログを取得し、含めるモデルの能力・コンテキスト・推論設定が実際のルートと一致するか確認します。独自別名には対象モデルのメタデータを付け、`slug`をGatewayが受理する名前にします。通常の`/v1/models`応答は別形式です。ローカルカタログは組み込みカタログを置き換えるため、併用端末では会社用CLIプロファイルの範囲で適用します。 [モデルカタログの仕様と例](https://developers.openai.com/codex/enterprise/roll-out-a-gateway#start-with-a-local-modelsjson-file)

1. 選んだ環境の`secrets/codex-models.json`にカタログを、`secrets/codex-endpoint.json`に`catalogInstallPath`を含むJSONオブジェクトを用意します。両方とも所有者限定で保存します。`catalogInstallPath`には端末上の絶対パスを設定します。例は`{"catalogInstallPath":"C:\\ProgramData\\OpenAI\\Codex\\models.json"}`です。
2. 生成コマンドへ`--windows-catalog`を追加します。カタログには選択するモデルを含めます。生成時に各slugを初期Responses APIのモデルまたは別名と照合し、認証情報用のフィールドを拒否します。自由記述欄も含め、カタログ原本に秘密や環境情報が入っていないことと、実モデルの能力を確認します。
3. 端末管理者がカタログを組織の管理手順で用意し、TOMLに記載した絶対パスへ設定します。ネイティブWindows向けのパスをWSLのCLIへ流用せず、対象ランタイムから読めるパスを用意します。併用端末のDesktopで独自カタログの隔離と切替を確認できない場合は、対象版の配布構成を見直します。

## Windowsの端末配布と切り戻し

Intune／Configuration Managerでは、システムの既定`config.toml`を`%ProgramData%\OpenAI\Codex\`に配布します。端末固定を採用した場合の`requirements.toml`も同じディレクトリが既定です。ユーザー用CLIプロファイルは対象ユーザーの`CODEX_HOME`（既定はホームの`.codex`）へ配布します。既存ファイルへ統合する場合は、現在の組織設定とprovider定義の競合を管理者が確認して更新します。システムファイルとその親は管理者／SYSTEMだけが変更でき、一般利用者は読み取れることを確認します。ユーザーの認証キャッシュはパッケージに含めません。 [CLIのプロファイル](https://developers.openai.com/codex/config-advanced#profiles) · [Windowsの配備](https://developers.openai.com/codex/enterprise/windows-deployment) · [既定値と強制設定の配布](https://developers.openai.com/codex/enterprise/roll-out-a-gateway#distribute-defaults-and-requirements)

配布前の資材を復旧用に確保し、少数端末から順に展開します。切り戻しでは配布を止め、変更したprovider・プロファイル・管理設定を元に戻します。会社Gatewayの利用を停止する場合は、Codex向けの委任許可・同意・割当を回収します。既発行トークンの有効期限と処理中ストリームの終了条件は別に確認します。

## 対象端末での受入

CLIのGateway OAuthで既定のAuthorization Bearer送信が使えるのは0.166.0以降です。Desktopは製品の表示バージョンと内蔵runtimeを記録し、実際に採用する版で同等の確認を行います。 [Gateway OAuthの公式要件](https://developers.openai.com/codex/enterprise/connect-to-a-gateway#use-native-gateway-oauth)

| 確認 | 合格条件 |
| --- | --- |
| 構成・配布 | CLIの`/status`、対応版の`/debug-config`、Desktopのprovider・モデル表示で実効値を照合する。Windowsの通常ユーザーで読み、システム設定を書き換えられないことを確認する |
| 本人認証 | CodexからブラウザーのEntra認証を開始し、本人のGateway許可で推論できる。OpenAIサインインを必要としない会社Gatewayを確認する。期限到来後の更新または再サインイン、対象の割当・条件付きアクセスに従った拒否を確認する |
| 推論と監査 | SSEの逐次表示・正常完了・エラー、複数ターン、ローカルツールの実行と結果の継続を確認する。送ったモデル名、実deploymentと利用者をGateway監査で照合する |
| 端末ごとの方針 | 併用端末では会社GatewayとChatGPT Enterpriseをそれぞれ選び、期待する接続先で新規チャットが成立する。専用端末の固定を採用した場合はユーザー設定やプロファイルで解除できないことを確認する |
| 復旧 | 旧設定への切替、既存Enterprise設定、再度の会社Gateway設定と認証を確認する |

ローカル試験は合成入力で設定生成、保護保存、TOMLの内容を確認します。CLIとDesktopの実配布、Entraサインイン、推論の受入結果は対象端末ごとに区別し、秘密を除いた実環境の記録も保護領域で保存します。
