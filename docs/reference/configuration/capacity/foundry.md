# Foundryの容量

[3環境の初期配備値](../environment-capacity.md)

初期配備する28モデルの名前・版・配備種別を、サービス別configの `modelDeployments` で管理する。初回は `prod`・`stg`・`dev` 共通で、新規配備ごとにAzureのモデル・SKU情報で確認できる最小容量を選ぶ。1ユニットが許容される場合は1で配備し、SKUがより大きい最小値を求める場合はその値を使う。 [Foundryのサービス別config](../../../../infra/config/capacity/foundry.json) · [設定項目](../config-options.md#モデル配列の要素servicesmodeldeployments)

構築の事前検査は、対象サブスクリプションでモデル・版・SKUの提供、容量の許容値、クォータの残り、配備可能な容量を照合する。同じ利用枠を使う新規配備は合算する。新規配備の最小容量や割当を確認できない場合は構築を停止する。 [構築前の容量確認](../../../setup/08-build-infra.md#資源を作らずに追加確認する場合) · [AzureのモデルSKU情報](https://learn.microsoft.com/en-us/rest/api/microsoftfoundry/accountmanagement/models/list#capacityconfig) · [Azureのモデル配備可能容量](https://learn.microsoft.com/en-us/rest/api/microsoftfoundry/accountmanagement/model-capacities/list)

構築後は、モデルを使う環境に割り当てられたクォータと必要な処理量を見て、Microsoft Foundryポータルで配備ごとの容量を設定する。この運用上の割当でGatewayの上流を検証する。 [配備容量の変更手順](../../../operations/foundry.md#容量だけを変更する)

基盤の再実行では配備名・モデル・版・SKUが一致し正常と確認できた既存モデルをARMの配備対象から外す。ポータルで調整した容量は保持される。既存配備を照合できない場合は、原因を確認してから再開する。

ここでのホストは、APIMが実際に送信する接続先URLのホスト名を指す。Foundry 1アカウントでも、GPT系のOpenAI APIを `<resource>.openai.azure.com`、Claude Messagesを `<resource>.services.ai.azure.com` に送れる。既存のPrivate Endpointは両方のDNS名に対応する。同じホストのパス、プロジェクト、モデル配備名の変更でAPIMの接続枠は分かれない。 [FoundryのFQDNとPrivate Endpoint](https://learn.microsoft.com/en-us/azure/foundry/how-to/upgrade-azure-openai#private-network-configuration) · [Claudeのエンドポイント](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude)

| 設定する場所 | 設定する内容 |
| --- | --- |
| Foundryのモデル配備 | 同じFoundryリソースにモデル・バージョン・配備名・配備種別を設定する。Standardはモデル別のTPM・RPMを確認し、ClaudeはRPM・非キャッシュ入力TPM・出力TPMを確認する。対象モデルがProvisioned対応で処理枠の予約が必要なら、ピークRPM、入出力トークン、キャッシュ率でPTUを算定し、該当配備に割り当てる。 |
| APIMのBackendsとAPIポリシー | GPT系のResponses／Chat Completionsは既存のOpenAI v1接続先を使用。Claude Messagesを公開するときは既存の手順に沿って、同じFoundryのMessages用ホストをBackendに登録し、ClaudeのAPIだけをそこへルーティングする。TLS検証、バックエンド認証、Private DNSへの解決を確認する。利用者向けのGateway URLと認証方式は維持する。 |

本番の最初の構成はFoundry 1リソースとし、既存のサービスURLをAPIごとに使う。PTUは対応するProvisioned配備の容量単位で、StandardのTPM割当に重ねて設定する項目ではない。Claudeは対象モデルの提供形態を確認し、Standard系で提供されるモデルには対応する個別クォータを確保する。 [OpenAI APIの現行設定](../../../../apps/api-gateway/apis/openai-inference-v1.json) · [Claudeの手動公開手順](../../../../apps/api-gateway/policies/portal-examples/claude-messages.md) · [PTUの算定](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/provisioned-throughput-sizing) · [Claudeの配備種別](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models-hosting-comparison)

通常・ストレス試験で最も多い接続先が上限の75%以内（12ユニットで18,432本以内）に収まり、モデルクォータと応答の基準も満たすなら、Foundryは1アカウントで足りる。OpenAI v1の対象APIは同一アカウントの別の公式ホストでも利用できる場合がある。これを利用する場合は、認証・状態を含めた互換性とAPIM側で枠が分かれることを実測し、Foundry側の同じ割当を二重計上しない。Claude Messagesなど同じホストに集中するAPIで余裕が確保できなければ、そのモデルに限り2つ目のFoundryアカウント・Private Endpointを比較する。会話状態を使うAPIは元の状態へ戻れることを確認し、配信途中の切断では中断を記録する。 [Azure OpenAI v1のエンドポイント](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle) · [APIMのバックエンドプール](https://learn.microsoft.com/en-us/azure/api-management/backends)

通常ピークはモデル合計24,000 RPM。仮の8,000入力／1,000出力トークンで、入力1億9,200万 TPM、出力2,400万 TPM相当。ストレス時はその2倍となる。現行資材は1アカウント・1プロジェクトに28モデルを初期配備し、Claudeは手動で追加する。本番公開前に、モデル別の利用比率、RPM・TPMまたはPTU、配備可能容量、キャッシュ適用時の消費を確定する。現在はモデルごとにリージョン別とサブスクリプション共通の割当があるため、自動の容量事前検査は選択リージョンのusageとモデルの配備可否を確認する。利用枠が複数リージョンや環境で共有される場合、FoundryポータルのScopeと既存配備を確認し、環境を合計して残枠に収まるか照合する。ClaudeのGlobal Standardは同一サブスクリプション内で同一モデル・版の枠を共有する。`prod` の利用枠は `stg`・`dev` の試験で消費しないよう、サブスクリプションの分離または環境ごとの割当を確認する。Gatewayの最大負荷は合成バックエンドで検証し、実モデルは割当、応答、429を別途確認する。 [Foundryの割当範囲](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/quotas-limits#check-quota-management-scope) · [Claudeのクォータ](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models-quotas-limits)

## モデル・上流API別の計測

| サービス | 容量を測る単位と受入 |
| --- | --- |
| Foundryモデル全般 | テキスト・埋め込み・画像・リランク・意思決定を、モデル、版、配備SKU、TPM/RPM、画像等の固有単位で集計する。音声やリアルタイムモデルは接続時間・開始数も計測する。 [Foundryのモデル分類](../../gateway/foundry-model-catalog.md) · [モデルのクォータ](https://learn.microsoft.com/en-us/azure/foundry/openai/quotas-limits) |
| Speech・リアルタイム音声 | Foundryの有料枠を使う。Speech S0の参考値はリアルタイムSTT 100同時、TTS 30要求/秒。現行の短音声RESTは対象APIの条件で測る。Voice Liveも新規接続/分・トークン/分・セッション時間を別に確かめる。WebRTCではAPIMを通る制御用WebSocketと上流へ直接届くメディア通信を分けて計測する。 [Speechのクォータ](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits) · [短音声のREST API](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short) |
| Document Intelligence | Foundry経由のRead／Layout。S0の既定例は解析開始15件/秒、結果取得50件/秒。文書ページ数と結果ポーリングによる増幅を含め、必要なら枠を申請する。 [Document Intelligenceのクォータ](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits) |
| Language PII | 機能別の秒・分のレート、同時に送る文書数、文書文字数を照合する。対象がマルチサービス枠の場合はその行を使う。 [Languageの制限](https://learn.microsoft.com/en-us/azure/ai-services/language-service/concepts/data-limits) |
