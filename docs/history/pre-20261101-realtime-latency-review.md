# Egakumi Coreの音声通信と遅延評価

> **履歴資料：2026-11-01の文書整理前スナップショット。現行の操作手順ではありません。** 本文の日付・検証結果・構成・認証方式は当時の記録です。現在の配備状態や試験合格を示しません。[現行の参照文書](../reference/realtime-latency-review.md)と[履歴一覧](README.md)を参照してください。

保存元：`docs/reference/realtime-latency-review.md`。整理開始前に保存した本文を保持し、履歴の注記と配置に伴う相対リンクだけを調整しています。

> 以下は2026年10月24日時点の調査記録です。現在の音声APIはAPIM購読キーで利用し、購読IDを接続の所有者として扱います。現在の認可設定は[API・認証の仕様](../reference/platform.md#apiと認証の仕様)を参照してください。

更新日：2026年10月24日。対象：egakumi-coreのmain、コミット `ac9bc2700fbad51c3459002b89a34ec50abe5b01` の実装・公開設定と、Core内に保存された結合試験の記録。初回調査日：2026年10月23日。初回の基準コミット：`6e954d3d4e9b07f036821579d962139528f31a9b`。

2026年10月25日のAPI再編後の識別子・公開URLは[API・Operation定義](pre-20261101-api-design-proposal.md)を参照。本書中のAPI名・URL・試験結果は、上記コミット時点の調査記録として保持する。

**WebRTCの「接続制御をGatewayで認可し、音声は端末とサービス間で通信する」構成は、Microsoftの低遅延音声の推奨に合っている。一方、WebSocket音声の多段中継、日本と米国の配置、共用Gatewayの容量については、現在の疎通試験だけで遅延要件への適合を判断できない。** ブラウザー音声の標準方式をWebRTCとし、発話終了から実際の再生開始までの時間を負荷下で受け入れることを推奨する。

| 用途 | 現行構成の評価 | 採用条件 |
| --- | --- | --- |
| ブラウザー・モバイルの対話音声 | WebRTCの経路設計は適切 | 端末側のUDP到達性と実際のメディア経路、会話応答時間を確認する |
| サーバーからの音声処理・音声の中継 | WebSocketとWebRTCの両方が候補。電話→自社サーバー→WebRTC→AIでは既存Coreの接続制御を再利用できる設計 | 電話とWebRTCの音声処理は自社アプリ側で担当。Core側はサービス主体のEntra権限・所有者チケット期限等、配置先の基盤側はメディア通信許可を確認する |
| 発話のたびに厳しい応答期限がある用途 | 現時点で性能受入は未完了 | p95・p99、ジッター、損失、割込み停止時間について用途別の合格値を決める |
| 録音ファイルの認識・読み上げ生成 | 通常のREST契約は用途に合う | 対話音声の性能評価と分けて処理時間・サイズを管理する |

今回の評価は、mainをorigin/mainへfast-forward更新したうえでのコミット間差分、保存済み実測、ソースの照合による。Azure環境の再構築や、新しい実通話・負荷試験は実施していない。

2026年10月24日の追補では「電話サービス→自社サーバー→WebRTC→AI」を具体的に照合した。**電話音声を受けてWebRTCへ流す処理は自社サーバー上のアプリが担当する。Coreへ音声変換アプリを追加することを必須変更とはしない。** 基盤側の対応とアプリ側の接続条件は、後述の専用節で分けて記載する。

## main更新の差分と調査結果への影響

`6e954d3` から `ac9bc27` への追加は、`Use precreated managed identities for Azure DNS` の1コミット。変更は29ファイルで、証明書取得・更新用のAzure Public DNS認証、準備手順、テストと説明画像が対象である。**音声経路、IDPS mode、日米の配置、APIM容量に関する評価は維持する。証明書取得・更新の運用条件には変更がある。**

| 差分 | 確認した実装 | 今回の評価への影響 |
| --- | --- | --- |
| Azure DNSの認証を事前作成したユーザー割り当てMIへ変更 | [証明書worker](../../guest/certificate.ps1#L425)がIMDSの`mi_res_id`でDNS用IDを選び、ARM tokenを実行時に取得する | 証明書の初回取得・更新の認証が変わる。通話中の音声フレームを処理する変更ではない |
| 管理VMへDNS用MIを追加 | [管理VM](../../infra/modules/management.bicep#L87)は従来のシステム割り当てMIも維持。[親Bicep](../../infra/main.bicep#L273)がAzure DNS選択時だけ既存IDを渡す | 管理VMの変更であり、APIMのMIやWebRTCのメディア終端は変わらない |
| Azure DNSの準備入力と送信先を変更 | `certificate.azureDnsSubscriptionId`・`certificate.azureDnsManagedIdentityResourceId`を入力し、旧SP資格情報は使わない。管理VMのAzure DNS用自動追加FQDNは`management.azure.com`になる | 旧Azure DNS入力のまま再構築する場合は新しいID参照と権限が必要。これは証明書のDNS-01用で、VNetの名前解決先を変更するものではない |
| 音声・ネットワークの受入 | [動作テスト仕様書](../testing/integration-tests.md) に従い、採用構成の音声と経路を確認する | WebRTCの制御とmediaを分けて測定する |

Azure DNSの新しいIDが利用できなければ、証明書の取得・更新が成立しない。証明書の有効期限や入口への反映条件を満たせない場合には、新規接続の可用性に影響し得るため、[新しいAzure DNS準備手順](../setup/03a-azure-dns-managed-identity.md)と[証明書運用](../operations.md#dns用マネージドidと権限を更新する)で確認する。これを、通常の音声転送へ追加される遅延とは分ける。現在の公開構築手順はAzure Public DNSとDNS用UAMIに対応する。DNS用MIの変更を音声APIの認証へ広げて解釈しない。

保存済みの2026年10月19日・21日起点の音声結果は、当時の測定として引き続き利用できる。最新コミットで再構築して通話性能を測り直した結果には読み替えない。

## 現行実装と通信経路

公開paramsはテスト・本番ともネットワークとAPIMが `japaneast`、Foundryが `eastus2`。ローカルの試験用paramsについても、この2項目が同じ値であることを確認した。Foundry用Private Endpointは日本側のVNet内に作成し、別リージョンのFoundryリソースへ接続する。[テストparams](../../params/test.example.json#L103)、[本番params](../../params/production.example.json#L103)、[Foundry配置](../../infra/modules/services.bicep#L109)、[Private Endpoint](../../infra/modules/services-private-endpoints.bicep#L20)

```text
WebSocketの音声とイベント
端末／アプリ → AppGW／WAF〔日本〕 → Firewall〔日本〕
            → Internal APIM〔日本〕 → PE〔日本〕 → Foundry〔East US 2〕

WebRTCの接続準備と制御
端末／アプリ → AppGW／WAF〔日本〕 → Firewall〔日本〕
            → Internal APIM〔日本〕 → PE〔日本〕 → Foundry

WebRTCの音声と主DataChannel
端末または中継サーバー ⇄ ICEで選択したサービス側のメディア接続先
```

図のWebRTCメディア経路にはCoreのAppGW・APIMを含めない。企業の端末側Firewall、VPN、NAT、サービス側の中継等は別途存在し得る。メディア接続先の所在地は、Foundryリソースの所在地だけから確定しない。

| 実装 | 確認した内容 | 遅延評価への意味 |
| --- | --- | --- |
| [Realtime作成policy](../../apps/gateway/apim/policies/realtime-create.xml#L1) | APIMが短期資格情報を取得し、続いてSDPを送る。資格情報を要求内に保持する | 接続開始には直列の上流処理がある。確立後の音声フレームごとにこの処理を行う構成ではない |
| [WebSocket policy](../../apps/gateway/apim/policies/protocol.xml#L1) | 初回handshakeの認可と上流への接続 | 通話中の音声はAPIMを経由するが、各メッセージに通常RESTのpolicyを適用する仕組みではない |
| [AppGW](../../infra/modules/application-gateway.bicep#L325) | 応答バッファリング無効、backend timeoutは設定値600秒 | SSEの逐次転送を支える。600秒は会話の応答速度を表す値ではない |
| [APIM転送](../../apps/gateway/apim/policies/protocol.xml#L152) | `buffer-response="false"`、timeout 240秒 | 応答の逐次転送とtimeoutの設定であり、TCPの再送待ちやモデル処理を除去しない |
| [往復ルート](../../infra/modules/network.bicep#L580)・[APIM VIP宛ルート](../../infra/modules/network-bindings.bicep#L169) | AppGWとAPIMの往復をFirewallへ向け、PE宛はVNet内経路を維持 | APIM→Foundry PEが、既定ルートによって必ずFirewallをもう一度通るわけではない |

## 電話サービス→自社サーバー→WebRTC→AIへの対応

### 対応可否と担当範囲

**現在のCoreは、この構成で自社サーバーが使うWebRTCの接続制御を再利用できる設計である。ただし、常駐サーバーのEntra権限や配置先の通信条件まで、そのまま満たされるとは限らない。** 以下はソースとオフライン検証による判断であり、サーバー実装とAzureを接続した実通話の合格判定ではない。

ここでいう「自社サーバー」は、電話サービスの音声入出力とWebRTCクライアントを実装する業務アプリである。CoreのAPIMや証明書管理VMを指さない。電話サービスは未指定のため、電話側は双方向WSSを利用する場合を基本に整理する。電話側をSIP／RTPで接続する場合、その受信・変換も自社アプリまたは採用する電話用ゲートウェイの担当となる。

| 対象・担当 | 現在の実装からの判断 | 必要な対応 |
| --- | --- | --- |
| Core：Realtimeの接続準備・終了 | 固定`gpt-realtime-2.1`のSDP交換、署名付き所有者チケット、hangupを利用できる設計 | 既存契約を使う限り、サーバー発という理由でAPIMの処理を作り直す必要はない |
| Core：常駐サーバーの認可 | 共通policyと設定検証は`appOnly`に対応。公開音声API例は本人scopeを設定 | 自社アプリのMI／サービス主体へEntraのapp roleを割り当て、対象APIのrequiredRolesへ権限名を設定する |
| Core：長い通話の終了制御 | 公開例の所有者チケットは600秒。期限後はそのチケットによるhangup・新規sideband接続が拒否される | 想定通話時間と終了処理の余裕に合わせ、関連する期限設定を整合させる。必要なら更新契約を追加する |
| 自社アプリ：電話音声とWebRTCの接続 | Coreが担当する機能ではない。既存ブラウザー試験も電話用アプリではない | 電話の認証・音声形式変換・送信タイミング・返答転送・割込み・切断を実装する |
| 自社サーバー配置先の基盤：メディア経路 | 配置先のNSG・Firewall・NATに依存。Coreの管理VM用設定は音声用UDPを許可していない | アプリ配置先で対象サービス向け通信を許可し、実際のICE経路を確認する |
| Core：Realtime mini | 現行CoreはminiのWebRTCを公開しない | miniが必要な場合だけ、固定モデルの作成・終了・所有者契約を追加設計する |
| Core：Voice Liveを選ぶ場合 | WebRTC用WSS制御契約はある。RealtimeのREST契約とは別 | 専用の制御URL・モデル・API版・認可を使用し、サービス固有のメディア経路と終了を受け入れる |

根拠：[Realtime制御API定義](../../apps/gateway/apim/apis/openai-realtime-control-v1.json)、[Realtime作成policy](../../apps/gateway/apim/policies/realtime-create.xml#L1)、[共通のapp-only判定](../../apps/gateway/apim/policies/gateway.xml#L76)、[protocolのapp-only判定](../../apps/gateway/apim/policies/protocol.xml#L79)、[所有者の照合](../../apps/gateway/apim/policies/realtime-hangup.xml#L10)、[通信の最終拒否規則](../../infra/modules/network-bindings.bicep#L144)。

### 既存Coreを利用する接続手順

```text
音声の往復：
電話利用者 ⇄ 電話サービス ⇄ 双方向WSS ⇄ 自社アプリ ⇄ WebRTC ⇄ Azure音声AI

接続準備・終了等：
自社アプリ → CoreのAppGW → Firewall → APIM → FoundryのPrivate Endpoint
```

上図の音声は、自社アプリからCoreのAppGW・APIMを経由しない。自社アプリの出口がCoreのFirewallを利用する場合には、そのFirewallを通る。電話側WSSのTCP待ちや電話サービス内の処理は残るため、WebRTCへの変更で全区間の遅延が一定量減るとは見積もらない。

Realtimeでは、自社アプリが次の既存契約へ合わせればよい。

1. 自社アプリ内で通話ごとのWebRTC peer connection、送受信用音声track、イベント用`oai-events` DataChannelを用意する。ICE候補の収集が完了したSDP offerを作る。Coreには後からICE候補を追加する専用APIはない。
2. Core向けのaccess tokenを使い、`POST /models/realtime/v1/calls`へ`{"sdp":"<SDP offer>"}`を送る。Coreの入口はこのJSON形式だけを受け付ける。Azure直結例のraw SDPや、任意の`session`／`model`を追加したJSONを、そのままCoreへ送る契約ではない。
3. CoreはAPIMのMIで短期資格情報を取得し、SDPを代理送信する。自社アプリは201のSDP answer、`X-Core-Call-Id`、`X-Core-Call-Ticket`を受け取り、answerをpeer connectionへ適用する。短期資格情報を自社アプリへ配布する変更や、自社アプリのMIへFoundryの直接呼出権限を追加することは、この経路では必須ではない。
4. ICE／DTLS・DataChannelの成立を確認し、電話側の音声をtrackへ供給する。受信trackの音声を電話側の形式へ戻して送る。必要な会話設定・イベント処理は、自社アプリが採用APIに沿ってDataChannel等で行う。
5. 通話終了・接続失敗・時間上限時には、電話側の再生待ちとWebRTC接続を片付け、同じ認証主体と所有者チケットで`POST /models/realtime/v1/calls/{callId}/hangup`を呼ぶ。音声が聞こえなくなったことと、Azure側の終了確認を別に記録する。作成成功後にanswer適用やICEが失敗した場合も回収対象とする。

Coreは既にSDPを代理交換する方式を採用しており、Microsoftもこの方式を掲載する。ブラウザーで生成したSDPだけを要求する制限はCoreの入口にはない。一方、任意のサーバー用ライブラリとAzureのSDP・codec・DataChannelの互換性まで確認済みという意味ではない。[MicrosoftのSDP代理交換](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-webrtc#set-up-webrtc)、[CoreのSDP入力検証](../../apps/gateway/apim/policies/realtime-create.xml#L10)、[回帰試験用ブラウザーの接続処理](../../tests/browser/realtime.html#L72)

Voice Liveを選ぶ場合、自社アプリはCoreの`wss://<Gateway>/models/voice-live/webrtc`へ接続し、`rtc.call.sdp.create`の`sdp_offer`と採用するsession設定を送る。上流は固定登録した`/voice-live/realtime/calls`で、通常音声WSS用の`/voice-live/realtime`とは異なる。`rtc.call.sdp.created`のanswerを適用し、音声はWebRTC、必要な制御はWSSまたはDataChannelで扱う。`model-voice-live-webrtc`の`invoke`へのapp-only登録を追加する。Realtimeの作成・hangup・所有者チケットをそのままVoice Liveの契約とは扱わず、電話切断時のセッション終了も別に受け入れる。[CoreのVoice Live制御定義](../../apps/gateway/apim/apis/azure-voice-live-webrtc-v1.json)、[MicrosoftのVoice Live交渉と制御](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-webrtc#set-up-webrtc-connection)

### Core側で必要になる設定と条件付きの変更

**既存の`gpt-realtime-2.1`・Realtime制御契約・East US 2のFoundryを使い、自社アプリを別のアプリ基盤へ配置する範囲なら、CoreのBicep／policyソースやAppGW→Firewall→APIMの構成変更を必要とする要因は確認していない。** app-only向けroleの設定、Entraの権限、必要な接続元IP・所有者チケット期限を設定し、既存の公開処理でpolicyへ反映する。設定ファイルを書き換えるだけで稼働中の認可が変わるわけではない。

この「設定変更で対応できる」は、既存機能を利用する範囲に限る。チケット更新機能の新設、Realtime miniのWebRTC契約追加、Core内への業務ホスト追加、別リージョンのFoundry新設・接続先分離まで無条件に含めない。特にFoundryの`foundryLocation`変更は、サーバー発WebRTCへの対応とは別の変更として、以下のモデル提供条件を確認する。

**常駐サーバーはGateway向けアプリロールを使って呼び出します。** 呼出サービス主体へEntraで`Gateway.Invoke.App`を割り当て、APIの`requiredRoles`へ同じ権限名を設定します。

| 設定箇所 | 設定する内容 |
| --- | --- |
| EntraのCoreアプリと自社アプリのID | Applications向け`Gateway.Invoke.App`、access tokenの`idtyp`、自社アプリのMI／サービス主体へのrole割当。Coreの既存手順を利用する |
| APIの`authorization.requiredRoles` | `["Gateway.Invoke.App"]`。呼出サービスはGateway audienceのapp-onlyトークンを取得する |
| 帰属と所有者 | 検証済みトークンの`tid`・`oid`から`callerId`とセッション所有者を決定する |
| sidebandを使う場合だけ | `model-realtime-observer`にも同じ主体・契約・`callerId`のapp-only登録を追加し、`invoke`を許可する。接続時は`call_id`とチケットを渡す。自社アプリ自身のDataChannelで必要な処理ができれば、別のsidebandは必須ではない |
| 接続元制限 | Coreの`network.clientAddressPrefixes`を制限している環境では、自社アプリの実際の出口IPを許可する。内部APIMの直接呼出しへ変更せず、既存のAppGW経路を利用する |

入口tokenの宛先はCoreアプリであり、Foundry向けtokenとは別である。CoreのApplication ID URIを使う`/.default`で取得し、受領tokenの`aud`・v2発行・`azp`・`oid`・`idtyp`・roleを実機で確認する。MIのClient IDとObject IDを相互に代用しない。[Coreのサービス主体の登録手順](../reference/platform.md#コアのentraアプリ登録)

既存の所有者チケットは「本人専用」の形式ではなく、検証済みのテナント・主体・契約・call ID・期限に結び付く。app-onlyを認可した場合はサービス主体が所有者になる。同じ主体が複数の電話通話を扱うため、自社アプリで電話側call IDとCore側call ID／チケットを対応付け、通話間で音声・制御を取り違えない。Coreの登録停止だけで成立済みのWebRTC音声が即時停止するとは扱わず、アプリによる既存通話の停止も設計する。

**長い電話通話では600秒の所有者チケット期限を見直す。** 公開例の`sessionTtlSeconds`は、Azure側の通話時間上限や自動切断タイマーではない。期限が切れれば、Coreのhangupと新規sideband接続が403になる。設定検証の許容範囲は60〜3600秒で、作成・終了・利用するobserverの値を一致させる必要がある。想定通話時間に終了・再試行の余裕を加えて設定し、アプリ側にも時間上限を持たせる。範囲を超える要件では、チケット更新または別の通話継続契約を追加設計する。単に設定上限を広げるだけでは、所有者の再認可やAzure側の制限の確認は完了しない。[制御APIの期限](../../apps/gateway/apim/apis/openai-realtime-control-v1.json)、[observerの期限](../../apps/gateway/apim/apis/openai-realtime-observer-v1.json)、[期限の検証](../../scripts/deployment/helpers/configuration.py#L475)、[observerとの整合](../../scripts/deployment/helpers/configuration.py#L901)

また、購読単位の共通回数制限は既定で無効（無制限）であり、60要求／60秒は有効化した場合の初期値にすぎない。同時通話数の上限でもない。Realtimeのhangupとobserverは共通回数制限から除外されている。新規通話・再接続・共用API負荷を見て必要な場合にだけ共通回数制限と購読別overrideを設定し、同時通話・キュー・CPUの制限は自社アプリ側でも管理する。[Coreの回数制限](../../apps/gateway/apim/policies/rate-limit.xml#L6)、[停止操作の除外](../../apps/gateway/apim/policies/gateway.xml#L227)、[observerの除外](../../apps/gateway/apim/api-publication.bicep#L144)

### 配置先の基盤で確認するネットワーク条件

自社アプリをCoreと別のアプリ基盤へ配置し、既存の公開Core URLで制御する案が、既存Coreへの変更を抑えやすい。CoreのBicepは管理ポータル用ACAを含む6サブネット・4NSG・4ルート表と管理VMを構成し、業務アプリのホストを作る汎用テンプレートではない。空きアドレスがあることだけで、その場所へアプリを配置できる設定が揃っているとは扱わない。[Coreのネットワークの担当範囲](../../infra/modules/network.bicep#L24)

| 経路 | 配置先の基盤で確認・追加すること |
| --- | --- |
| 電話サービス→自社アプリ | 長時間の双方向WSSを受けられるHTTPS入口・TLS・アプリへの転送を用意する。電話サービスの認証方式はアプリで検証する。既存のCore音声APIへ電話サービスを直接向けるだけでは、認証もメッセージ形式も一致しない |
| 自社アプリ→Core／Entra／電話側制御API | 必要なHTTPS／WSS先とTCP443を許可する。DNS、TLS検証、出口IP、接続維持も確認する |
| 自社アプリ→Realtimeのメディア | Microsoftの現行案内は対象Foundry地域の`AzureCloud.<region>`に対するUDP／TCP3478。現行地域では`AzureCloud.eastus2`を照合し、配置先のNSG・Firewall・OSで許可する。実際の候補・transport・宛先を記録する |
| 自社アプリ→Voice Liveのメディア | Realtimeの3478やTeams／ACSのport表を流用しない。採用するVoice Liveの現行条件・SDP・選択候補を照合する。保存済みの3479観測だけで必要portを固定しない |

メディアの通信許可は、サーバーからの接続と戻り通信を成立させるためのものであり、CoreのAppGWへUDP listenerを追加する変更ではない。HTTP／WSS用のFQDN許可だけでも成立しない。NATの条件によりSTUN／TURN等の設定が必要な場合には、採用ライブラリ・サービス・配置先に合わせて追加し、全構成で自社TURNサーバーが必須とはしない。[RealtimeのFirewall条件](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-webrtc#step-4-optional-configure-network-firewall)、[aiortcのICE設定](https://aiortc.readthedocs.io/en/latest/api.html#aiortc.RTCConfiguration)

Coreの**管理VMをそのまま本番の電話中継サーバーへ転用することは推奨しない**。証明書管理と通話処理の可用性・権限・負荷を分けるため、業務アプリ用ホストを別途用意する案を基本とする。管理VMで限定的な検証をする場合も、現行NSGは未許可の送受信を4096番で拒否し、既定経路はFirewallへ向かう。UDP3478を通すにはNSGとFirewallの両方へ追加許可が必要で、電話側WSSの受信口も別途必要になる。既存の`managementAdditionalSecurityRules`・`managementAdditionalNetworkRules`等はその検証用拡張に利用できるが、業務アプリ用ホストの自動作成は行わない。[管理VMの許可](../../infra/modules/network.bicep#L120)、[管理VMのFirewall規則](../../infra/modules/network.bicep#L395)、[既定経路](../../infra/modules/network.bicep#L581)、[NSGの最終拒否](../../infra/modules/network-bindings.bicep#L144)

自社サーバーがCoreのFirewallから外へ出る配置では、WebRTCもそのFirewallの影響を評価する。以前の端末発WebRTCと同じ経路とは限らず、IDPSを一律に無効化することを開始条件にはしない。既存Foundryの`publicNetworkAccess: Disabled`を解除したり、CoreのPEを撤去したりすることも、本案の必須変更ではない。制御APIのPrivate Linkと、別経路のWebRTCメディアという境界を維持する。[Foundryの設定](../../infra/modules/services.bicep#L111)

### 自社アプリ側の実装条件と受入

電話の着信・接続元検証、音声の取り出し、WebRTC trackへの供給、返答の再符号化と転送、割込み、通話別の状態管理は自社アプリ側で担当する。サーバー用WebRTC実装の例として`aiortc`にはpeer connection・音声track・DataChannel・ICEのAPIがあるが、CoreやMicrosoftがこの組合せの動作を保証したという意味ではない。[aiortc公式API](https://aiortc.readthedocs.io/en/latest/api.html)

具体例としてTwilio Media Streamsでは、会話へ音声を戻すには双方向の`<Connect><Stream>`が必要で、音声はBase64のG.711 μ-law／8kHz／monoである。自社アプリはこれをWebRTCで合意したcodec・sample rateへ合わせて処理し、逆方向も電話側の形式へ戻す。WebSocketのJSONをそのままWebRTC音声trackへ渡すことはできない。電話側の事業者・方式が異なる場合には、その契約に置き換える。[Twilioの双方向通信](https://www.twilio.com/docs/voice/media-streams#bidirectional-media-streams)、[音声メッセージ仕様](https://www.twilio.com/docs/voice/media-streams/websocket-messages#start-message)

電話側の音声バッファは、AzureとのWebRTC接続とは別に管理する。例えばTwilioには未再生音声を破棄する`clear`と、再生・破棄の進捗を扱う`mark`があり、AI側をcancelするだけでは電話側の再生待ち音声を消したことにならない。WebRTCの音声を自社アプリが受信した時点も、電話利用者が聞いた時点とは異なる。割込みと会話履歴の整合は電話側の再生状況まで含めて設計する。[Twilioの再生・破棄](https://www.twilio.com/docs/voice/media-streams/websocket-messages#mark-message)

Twilioの入口はTCP443のWSSと`X-Twilio-Signature`の検証を必要とする。これはCore向けEntra認証とは別の接続である。電話側Webhook／WSSに合わせるために、Core全体のJWT認証を緩める必要はない。同じCoreのAppGWで電話用入口も公開する方針を選ぶ場合だけ、専用のbackend・経路・認証契約を追加設計する。[Twilioの入口要件](https://www.twilio.com/docs/global-infrastructure/firewall-configurations/media-streams-configuration)

サーバー発の受入では、既存のブラウザー試験に加えて次を確認する。

- **認可と所有者**：実際のMI／サービス主体で作成・必要な制御・hangupが成功し、未登録主体・異なる契約・別通話のチケットでは拒否される。電話側の署名不正とCore側の401／403を区別する。
- **音声と経路**：対象ホストからICE／DTLS・codec交渉・実音声往復を確認し、NAT・UDP遮断時の挙動、キュー量、音声の途切れを記録する。ブラウザーと同じ統計項目が取れないライブラリでは、取れない値を0と補わない。
- **長い通話と失敗時の回収**：10分超を含む採用時間、チケット期限、電話切断、Core作成後のICE失敗、worker停止時の回収・再試行を確認する。プロセス再起動だけで既存peer connectionが復元されるとは扱わない。
- **電話で聞こえる品質**：利用者の発話終了→電話で返答が聞こえるまでと、割込み→電話で旧返答が止まるまでを測定する。同じ電話サービス・モデル・設定・負荷でサーバー→AIのWSSとWebRTCを比較する。

最小案は、**Coreの既存Realtime制御と固定モデルを維持し、Coreには自社アプリ用のapp-only向けrole設定と必要な期限設定を追加する。電話・WebRTCの処理は自社アプリで実装し、その配置先の基盤で音声通信を許可する**、という役割分担である。mini、新しいVoice Live接続先、Core内への業務ホスト追加、CoreのAppGWで電話受付まで共用する変更は、選択した要件に応じる追加設計とする。

## 音声の実測で確認すること

[音声の受入条件](../testing/integration-tests.md#35-model-gatewayリアルタイム音声) に従い、実音声、認識、正常終端、割込み・停止・再接続を確認する。応答開始時間の分布、同時通話負荷、ジッター・損失・再生バッファ、他APIとの競合も、採用するモデル・設定・経路で測定する。

[回帰試験用ブラウザーの観測コード](../../tests/browser/realtime.html) の `candidate-pair.currentRoundTripTime` はSTUN疎通確認による直近の往復時間であり、会話の「発話終了→返答再生」の時間とは区別する。hangup要求の応答時間も、音声生成・再生開始時間と分けて記録する。[W3CのICE候補ペア統計](https://www.w3.org/TR/webrtc-stats/#dom-rtcicecandidatepairstats-currentroundtriptime)

[WebSocket試験クライアント](../../scripts/voice_client.py) は `turn_detection: null` と明示的なcommit／response.createを使う。この試験とは別に、自然発話の終了判定を含む対話性能を確認する。

## Microsoftの公式推奨との照合

### 端末の対話音声はWebRTCを基本にする

MicrosoftのAzure Realtime公式手順は、通常のリアルタイム音声ストリーミングにWebRTCを推奨し、WebSocketはサーバー側の送受信等に位置付けている。Voice Liveも、UDPによる連続した音声転送と、損失時にTCPの順序回復を待つWebSocketの違いを説明している。[Azure Realtime WebRTC](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-webrtc)、[Voice Live WebRTC](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-webrtc)

Coreは、WebRTCの制御だけをGatewayへ通すため、この推奨と整合する。Realtimeの短期資格情報をサーバーに保持してSDP交渉を代理する方法も、Microsoftが公式手順に掲載する選択肢である。Gatewayを通す認可と、音声の直接経路は両立できる。

一方、WebSocket音声を端末対話の既定にすると、音声をAppGW・APIMへ中継し、TCP損失時の待ちも引き受ける。サーバーが音声を受け持つ要件には合理性があるが、ブラウザーからの低遅延会話ではWebRTCを先に評価する。

### 自社サーバーの中継と接続方式は個別に選ぶ

自社サーバーで音声を中継する理由と、そのサーバーからAzureへ使う通信方式は個別に判断する。例えば、電話サービスがWebSocketで渡す音声を受け取る、指定の音声認識・LLM・音声合成を組み合わせる、サーバー上の独自処理で音声を加工する、といった要件は中継アプリを置く理由になる。Microsoftも、電話サービスとの橋渡しや、サーバー内で音声認識・LLM・合成を組み合わせる用途を示している。[Microsoftの音声エージェント設計](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/build-voice-agent#when-to-use-the-websocket-protocol)

中継サーバーからAzureへは、WebSocketに加えてWebRTCを使う構成も候補になる。WebRTCはサーバーでも実装でき、Pythonの`aiortc`等のライブラリがある。MicrosoftのHosted Agent設計も、サーバー側のWebRTC stackを自社運用する構成と、実装が簡単なWebSocketを選ぶ構成を説明している。ただし同資料のHosted Agentへの接続方式と、Coreが公開するAzure Realtime／Voice Liveの契約は区別する。[aiortc公式資料](https://aiortc.readthedocs.io/en/latest/)、[サーバー側WebRTCの設計](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/build-voice-agent#use-webrtc-media-with-websocket-signaling)

```text
電話サービス ⇄ WebSocket ⇄ 自社中継サーバー ⇄ WebRTC ⇄ Azure音声AI
```

この構成では、サーバーが電話側の音声をWebRTCの音声trackへ渡し、返答を電話側の形式へ戻す。必要なcodec・sample rateの変換、送信タイミング、バッファ、UDP経路と接続維持を含めて実装・評価する。電話側のWebSocket区間の待ち時間は残り、変換処理もあるため、全区間で遅延が減るかは比較測定で決める。

Coreでのサーバー発WebRTCは、既存のブラウザー音声試験とは別に、採用ライブラリとAzureの接続、Gateway向け認証・所有者・終了制御、サーバーからのメディア経路を受け入れる必要がある。公開例の本人委任登録は、常駐サーバー用のapp-only登録を自動では含まない。今回の追補では、既存policyと設定検証がサービス主体に対応し、対象APIへの登録追加で利用する方針を確認した。詳細と担当範囲は本書の「電話サービス→自社サーバー→WebRTC→AIへの対応」を参照する。[Coreの音声の受入](../testing/integration-tests.md#35-model-gatewayリアルタイム音声)、[本人認証の受入](../testing/integration-tests.md#32-認証本人接続購読管理sql)

認証、社内DBの検索、業務APIの呼出し、対応するセッション制御だけが目的なら、音声を端末とAzureの間に流し、サーバーは制御やtool処理を担当できる。Voice Liveはこの分担を公式に説明している。録音済み音声の送信と、会話中の音声中継も別の用途であり、WebSocketを使うこと自体は録音済み処理を意味しない。[Voice Liveのメディアとサーバー制御](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-webrtc)

### WebSocketでもWAFが通話内容を継続検査するわけではない

AppGWのWAFはUpgrade後のWebSocketデータを検査・加工しない。APIMのアクセス制御policyは初回の `onHandshake` に適用され、接続後はメッセージを中継する。したがって、WSSを選ぶ理由を「WAFやAPIMが音声・設定変更の各イベントをすべて検査するため」とすることはできない。[AppGWのWebSocket仕様](https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-websocket)、[APIMのWebSocket仕様](https://learn.microsoft.com/en-us/azure/api-management/websocket-api)

各イベントへの強制的な制限が必要なら、その機能を持つ信頼するアプリ側中継が必要になる。これを追加する場合は、中継の配置・キュー・処理時間も含めて評価する。WebRTCのsidebandも、直接DataChannelに対する同期的な検査器と同じ役割ではない。[Coreの音声制御の境界](../reference/platform.md#音声サービスの選び方)

### 日本側のPEは米国のサービスを日本へ移すものではない

Private EndpointはVNetと同じリージョンに置く必要があるが、接続対象のサービスは別リージョンに置ける。このため、日本のprivate IPへ解決することと、日本だけで処理・通信が完結することは別の条件になる。[Private Endpointのリージョン条件](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview)

Microsoft公表のAzureバックボーン統計では、Japan East→East US 2は **P50 RTT 165ms**。データは2026年7月30日までの30日間の統計である。CoreのAPIMからEast US 2のサービスへの接続には地理的距離があり、数msのGateway設定調整だけでこの距離を解消することはできない。ただし165msはCoreの要求時間・追加遅延の測定値でも、保証値でもない。[Azureリージョン間RTT統計](https://learn.microsoft.com/en-us/azure/networking/azure-network-latency)

さらに、CoreのRealtime配備は `GlobalStandard` である。Microsoftは、この配備方式では処理先を動的に選び、高い継続負荷で遅延のばらつきが増え得ると説明する。リソースの `eastus2` だけから、モデル推論の場所・所要時間は確定しない。[Foundryの配備方式](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/deployment-types)

### リージョンと音声モデルはサービス別に判断する

この節は2026-10-24時点のモデル構成・地域表に基づく比較である。2026-10-27に初期モデルを見直しており、新しい選定については[配備モデル一覧](../reference/foundry-model-catalog.md#初回配備する32モデル)と対象リージョンの最新カタログを照合する。

**現行の音声モデル・版・配備方式をすべて維持したまま、共通Foundryの`foundryLocation`だけを`eastus2`から`japaneast`へ変更する案は成立しない。** 2026年10月24日に、Coreのテスト／本番の配備設定とMicrosoftのモデル地域表を照合した。Azure OpenAIとして自分のFoundryへ配備するモデルと、Voice Liveが提供する配備済みモデルは、同じ名前が含まれていても提供地域を共用しない。

| 調査当時のCore音声モデル | 固定版・配備方式 | East US 2 | Japan East | 移行への影響 |
| --- | --- | --- | --- | --- |
| `gpt-realtime-2.1` | `2026-07-07`・`GlobalStandard` | 対応 | 非対応 | 現行RealtimeのWebSocket／WebRTCの配備先としてそのまま移せない |
| `gpt-realtime-2.1-mini` | `2026-07-07`・`GlobalStandard` | 対応 | 非対応 | 現行miniのWebSocket配備を維持できない |
| `gpt-4o-mini-transcribe` | `2025-12-15`・`GlobalStandard` | 対応 | 非対応 | 現行OpenAIファイル文字起こしの配備を維持できない |
| `gpt-4o-mini-tts` | `2025-12-15`・`GlobalStandard` | 対応 | 対応掲載なし | 現行OpenAI読み上げの配備先として採用できない |
| `gpt-audio-1.5` | `2026-02-23`・`GlobalStandard` | 対応 | 対応掲載なし | 現行Audio Chatの配備先として採用できない |
| `whisper` | `001`・`Standard` | 対応 | 非対応 | Azure OpenAIとしての現行Whisper配備を維持できない |

上表の「対応」はMicrosoftの公開された配備地域の条件であり、導入先subscriptionでのquota・空き容量確保や、実配備成功まで確認した意味ではない。`gpt-4o-mini-tts`と`gpt-audio-1.5`はGlobal Standardの地域表にJapan Eastの対応が掲載されていないため、利用可能なものとして移行計画へ入れない。[最新のCoreモデル配備](../../config/test.json)、[MicrosoftのGlobal Standard／Standard地域表](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability?pivots=standard)

一方、Speech／Voice LiveとしてJapan Eastのリソースを利用する場合は、次の違いがある。

| サービス・機能 | East US 2からJapan Eastへ変更する場合の条件 |
| --- | --- |
| Voice Liveの`gpt-realtime-2.1`・`gpt-realtime-2.1-mini` | 両地域で利用可能、LLMの処理scopeはGlobal。Azure OpenAIの直接Realtime APIをJapan Eastへ移すこととは別の選択肢 |
| Voice Liveの`gpt-4.1` | Japan Eastでも利用可能。ただし地域表ではEast US 2のRegionalからJapan EastのGlobalへ処理scopeが変わる |
| Voice Liveの旧`gpt-realtime`・`gpt-realtime-mini`・`gpt-realtime-1.5` | East US 2にはあるがJapan Eastには提供されない。2.1系が使えることを旧モデル全般へ広げない |
| Voice Liveの`mai-transcribe-2-streaming` | East US 2は対応、Japan Eastは非対応。Coreの保存済み途中認識試験を同じモデルのまま移せない |
| Voice LiveのHD voices・`mai-transcribe-2` | Japan Eastからも利用できるが処理scopeはGlobal。音声認識・合成それぞれが日本で処理される保証にはならない |
| Speech単体の通常のリアルタイムSTT・Neural TTS | Japan Eastでも利用可能。OpenAIのtranscribe／TTSやVoice Liveとは別のAPI契約 |
| Speech単体の標準HD voices・MAI voices・Personal voice | East US 2には提供されるがJapan Eastには提供されない。Voice Live経由のHD voicesとは条件が異なる。Custom voice HD endpointも別の欄であり、すべてのHD機能が非対応という意味ではない |

出典：[Speech／Voice Liveの地域と処理scope](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions?tabs=voice-live)。Japan EastでVoice Liveの2.1系が使えても、上表のAzure OpenAI配備を同じ地域へ作れる根拠にはならない。Voice Live WebRTCには最寄りリージョンへの自動ルーティングの説明もあるため、資源を日本へ置く変更の効果は実メディア経路と応答時間で判定する。[Voice Live WebRTCのルーティング](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-webrtc#prerequisites)

Voice Liveの採用モデルは配備設定から確定し、音声認識・テキストモデル・音声合成の処理経路を確認する。公開例のモデル選択を全構成の必須条件にせず、モデル・接続方式ごとに [音声の受入](../testing/integration-tests.md#35-model-gatewayリアルタイム音声) を実施する。[Voice Liveのモデル別構成](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live#supported-models-and-regions)

なお、CoreのRealtime miniはWebSocketのみを公開する契約だが、MicrosoftのWebRTC対応モデル一覧にはminiも含まれる。miniによる低遅延対話が必要なら、Coreで固定モデルのWebRTC作成・所有者・終了契約を追加する候補になる。[Coreのmini契約](../reference/platform.md#音声サービスの選び方)、[音声の受入条件](../testing/integration-tests.md#35-model-gatewayリアルタイム音声)

Coreの[Foundryリソースとモデル配備](../../infra/modules/services.bicep#L111)は一つの共通Foundryを親とし、[backendの合成](../../infra/main.bicep#L308)もその接続先を使う。そのため、`foundryLocation`は音声だけの切替スイッチではない。本調査では、現行East US 2の共通Foundryを維持し、必要ならJapan EastのVoice Live／Speech用リソースを別に用意して比較する案を推奨する。別リソース側のAPIM MI権限、固定backend、必要なPE・DNS・通信許可を揃える。Voice Liveへ切り替える場合には、自社アプリ側もその交渉・イベント・終了契約へ対応する。これは既存Realtimeをサーバーから利用するための設定追加とは別の構成変更である。

### Firewallの直列配置は検査要件と効果で選ぶ

MicrosoftのArchitecture Centerは、AppGWの後段にFirewallを置く構成と、AppGWで受信・Firewallで外向きを担当する並列構成の両方を示す。後段Firewallでの検査等が必要なときは直列構成に理由がある。Coreの形自体が非対応の設計ということではない。[AppGWとFirewallの設計パターン](https://learn.microsoft.com/en-us/azure/architecture/example-scenario/gateway/firewall-application-gateway)

Coreの本番設定はPremiumのIDPS **Alert**、TLS inspection設定は含まない。AlertのIDPSは規則処理と並列に動き、Alert and Denyのinline処理とは異なる。現在の設定へ、TLS復号検査やinline IDPSの負荷をそのまま当てはめない。[CoreのFirewall設定](../../config/production.json#L15)、[Firewall policy実装](../../infra/modules/network.bicep#L329)、[IDPSの処理方式](https://learn.microsoft.com/en-us/azure/firewall/rule-processing#idps)

端末からAzureへ流れるWebRTCメディアには、このCore FirewallのIDPSが直接作用しない。Core内に中継サーバーを置く場合は、そのサーバーの実効ルートを別に確認する。WebSocket音声はCore Firewallを通るため、Alertでも負荷時の影響は比較対象になる。現在の設定だけを根拠にIDPSを遅延の主因と断定せず、影響が小さいと実測済みとも扱わない。Alert and Denyへ変更する場合は、そのmodeで再評価する。

受信経路のIDPS検知を必要としない場合は、AppGW→APIMを直接とし、外向きのFirewallを維持する構成を比較できる。その際は往復UDR・NSG・入口証明をセットで見直す。WebRTCメディアは元からこの受信経路を通らないため、この変更で狙う効果は主に接続制御とWSS中継である。

Firewallの遅延probeは、Firewall周辺の状態を調べる補助指標になる。Microsoftは、ネットワーク全体のend-to-end時間や、そのFirewallが追加した遅延そのものを測る指標ではないと明記している。probeの値をGateway各段の実測遅延として加算しない。[Firewall遅延指標の測定範囲](https://learn.microsoft.com/en-us/azure/firewall/monitor-firewall-reference#azfw-latency-probe)

### 共用Gatewayの容量は実負荷で確認する

CoreはテストがAPIM Developer 1unit、本番がPremium 1unitで、APIMリソースにavailability zoneの指定を持たない。AppGWの最小台数はテスト0、本番2である。テストの成功をそのまま本番の遅延・継続性の保証へ広げることはできない。[テスト設定](../../config/test.json#L31)、[本番設定](../../config/production.json#L39)、[APIM実装](../../infra/modules/services.bicep#L201)

MicrosoftのAPIM設計指針は、実ワークロードでの負荷試験、重要APIのcompute分離の検討、zone冗長化では少なくとも2unitを推奨している。Coreの1unitを直ちに不足と断定する根拠はないが、常設の音声サービスでは容量と冗長性の設計を追加する必要がある。[APIMのWell-Architected指針](https://learn.microsoft.com/en-us/azure/well-architected/service-guides/azure-api-management)

長時間のWSSはRPSだけで評価しない。24kHz・16bit・monoのPCMを連続送信する場合、音声は1方向48kB/s、Base64化で約64kB/s、約512kbpsになる。これはPCMの仕様からの計算値で、JSON・TLS等を含めず、送受信双方が連続する場合は約1Mbpsになる。実際の発話率、接続数、送信キュー、Gateway帯域を合わせて評価する。

CoreのAPIM診断は100% sampling、本文bytesは0で、logger種別は `azureMonitor` である。本文を残さない設定は維持しつつ、共有負荷試験で診断処理の影響も確認する。Microsoftには高RPSでApplication Insights記録が性能に影響するという案内があるが、その試験値を別loggerのCoreへ直接転用できない。また、Coreの各音声フレームに100%の診断処理が走るという意味でもない。[Coreのloggerと診断設定](../../apps/gateway/apim/api-publication.bicep#L584)、[Application Insights診断の性能上の注意](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-app-insights#performance-implications-and-log-sampling)

## 公開事例と参考実装

| 事例 | 公開された構成・工夫 | Coreへ適用できる示唆 |
| --- | --- | --- |
| 関西テレビのAIハチエモン | Microsoft AI Co-Innovation Lab KOBEとの事例。Realtime API、Custom Voice、Voice Live、RAG、WebSocketサーバーを組み合わせる | 日本語・固有の声・業務データを含む音声対話の実例。WSS中継にも用途がある。公開ページにはCoreの三段Gatewayと同じ経路のp95測定はない |
| Astra Techのbotim | Voice Liveと、botimサービスを接続するmiddlewareを採用。nativeなgpt-realtime、VAD・雑音対策、弱いネットワークやピーク時の品質への工夫を説明 | 低遅延をネットワーク装置だけで決めず、モデル・発話判定・端末回線も含めて設計する実例。公開された導入実績をCoreの性能保証値には転用できない |
| Azure-SamplesのVoiceRAG | ブラウザー→PythonのRTMiddleTier→Azure Realtime、AI Searchによる検索toolという参考実装 | 音声の中間サーバーとtool処理を構成する方法を参照できる。顧客本番の負荷試験結果とは区別する |

出典：[関西テレビの事例](https://www.microsoft.com/en-us/ailab/case-study/kansai-television)、[Astra Techの導入事例](https://www.microsoft.com/en/customers/story/25412-astra-tech-azure-ai-foundry)、[Azure-Samples VoiceRAG](https://github.com/Azure-Samples/aisearch-openai-rag-audio#architecture-diagram)

これらの事例から得られるのは、選択した通信方式・モデル・中間処理の使い方である。Coreと同じ日本側のAppGW→Firewall→APIM、米国側のFoundryという構成が、所定の会話応答時間を満たすことを示す比較試験は確認できない。

## 改善の優先順位

| 優先 | 対応 | 期待する効果と判断方法 |
| --- | --- | --- |
| 1 | ブラウザー・モバイルの対話音声はWebRTCを標準方式とする | 音声のGateway中継とTCP損失時の待ちを避ける。現行Coreの制御APIを利用できる |
| 1 | 接続時間、返答開始時間、割込み停止時間を別の受入項目にする | 疎通PASSと、体感品質の合格をそれぞれ判断できる |
| 1 | 利用拠点の実回線でUDPとICE経路を確認する | 自宅の成功だけで企業proxy・VPN・Wi-Fiを含む利用条件を合格にしない |
| 2 | 現行Realtimeと、近い地域のVoice Live・native音声モデルを比較する | 音声専用の接続先を分ける価値を判断する。日本配置の表記だけで効果を確定しない |
| 2 | 音声中継アプリが必要な用途では、サーバー→AzureのWebRTCも比較する | 中継の必要性だけでWSSに固定せず、変換・バッファ・運用負担を含めて選ぶ |
| 2 | WSSを必要とする用途で、接続数・帯域・キュー・他API負荷を測る | 共用APIM 1unitでの競合、専用Gatewayや中継の必要性を判断する |
| 3 | 受信Firewallの検査要件を確認し、必要なら並列構成と比較する | WSSと接続制御の追加経路を減らす効果と、必要な検査を評価する |
| 3 | sampling・unit数・冗長化を本番負荷に合わせて調整する | p95・p99と通話継続性を保つ。機密本文のログ追加で計測を補わない |

Realtime公式の端末側Firewall案内は、対象Foundry地域の `AzureCloud.<region>` に対するUDP／TCP 3478である。Voice Liveの過去の3479観測は、その試験時の経路情報として扱う。Realtime・Teams・ACSのport表を他サービスへ共用せず、Voice Liveは現行のサービス条件と実選択候補を別に照合する。[Realtimeの端末側Firewall設定](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-webrtc#step-4-optional-configure-network-firewall)

MicrosoftのTeams／ACS向け案内も、短いメディア経路、proxyやVPN経由の削減、UDP、NATやWi-Fiの確認を重視する。これは端末側ネットワークを評価する参考であり、CoreへTeamsのIP・portをそのまま登録する指示ではない。[Teamsのproxy案内](https://learn.microsoft.com/en-us/microsoftteams/proxy-servers-for-skype-for-business-online)、[ACSのネットワーク推奨](https://learn.microsoft.com/en-us/azure/communication-services/concepts/voice-video-calling/network-requirements)

共通Foundryの `foundryLocation` を一括変更すると、他のモデルの提供地域・版・quota・PE・権限にも影響する。近い音声接続先を試す場合は、音声用リソース、固定backend、APIMのMI権限、必要なPE・DNS、音声固有のAPI契約と受入試験を一体で追加する。変更の単位を音声接続として具体化してから、共通リソース全体の配置変更と比較する。

## 性能受入の具体案

最初に業務上の目標を定める。たとえば「通常の日本語対話で、発話終了から返答が聞こえるまでp95で1.5秒以内」は目標案として使えるが、Microsoftの保証値でも、Coreが達成済みの値でもない。tool実行を含む応答と含まない応答、接続直後と接続済みのターンは集計を分ける。

| 測定項目 | 開始と終了・収集する情報 | 注意点 |
| --- | --- | --- |
| 接続開始時間 | 接続要求開始→SDP完了→ICE／DTLS成立→DataChannel open | 初回の認証・DNS・TLS・資格情報取得と、通常の発話応答を分ける |
| 会話応答時間 | 入力の実際の発話終了→返答音声の最初の非無音が再生される時点 | `response.created`、最初のパケット、audio要素の存在だけで代用しない |
| 発話終了判定 | 実際の発話終了→サービスの終了判定 | 自然な日本語、短い間、言い直し、雑音を含める。手動commitとは別に試験する |
| 割込み停止時間 | 利用者の割込み開始→既存の返答が聞こえなくなる時点 | cancel ACK・hangup ACKに加え、再生待ち音声の停止を測る |
| メディア品質 | 候補ペアRTT、UDP／TCP、候補種別、RTPのjitter・packetsLost・再生バッファ・concealment | 秒／ms、累積値／区間値を区別する。観測できない値を0と補わない |
| WSSの送信待ち | 音声取得→送信、受信→再生予約、キュー量と最古データの待ち時間 | 古い音声をため込むと、接続が正常でも会話が遅れる |
| Coreの処理 | 制御API・handshake、上流の処理、APIM容量、AppGW接続数、Firewallの状態 | 長い接続の寿命・要求総時間を、返答開始時間と混ぜない |

WebRTCの品質項目は[W3Cの統計定義](https://www.w3.org/TR/webrtc-stats/)に合わせる。発話終了の設定も独立した待ち時間となり、Voice Liveの公式仕様には既定500msの `silence_duration_ms` がある。値を短くする場合は、応答速度と発話の誤分割を同時に評価する。[Voice Liveの発話終了判定](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to#turn-detection-parameters)

試験は以下の順で比較する。

1. **同じモデル・版・音声・入力で経路を比較する。** 現行WebRTCと現行WSSを比較し、必要な場合に、認可済みの試験用サーバーから同じbackendへ接続する対照を追加する。中継アプリが必要な用途では、同じアプリを使ったサーバー→AzureのWSS／WebRTC比較も別に設ける。モデル変更による差と、Gateway経路による差を一度に混ぜない。
2. **同じ条件で配置・モデルを比較する。** 近い音声用接続先、Voice Liveのnative音声モデル等を追加する。Microsoft側のglobal routingがある経路は、実測の接続情報も記録する。
3. **実際の利用回線を比較する。** 社内有線、Wi-Fi、VPN、モバイル等で、通常時と損失・ジッターがある条件を確認する。UDP遮断時の失敗や代替経路もサービスごとに判定する。
4. **単独・想定通常・ピークの同時通話負荷を比較する。** REST／SSE／toolの共用負荷、再接続集中、構成更新時も含める。数百〜千ターン程度から測定を始め、p95・p99には標本数・失敗数と不確かさを添える。
5. **差分から変更対象を選ぶ。** メディアRTTが大きければ接続先・回線、発話終了待ちが大きければ判定設定、処理開始が遅ければモデル・容量、WSSの待ちが大きければ中継・キュー・Gatewayを改善する。

APIMのManaged Identity tokenは期限までcacheされるため、認証policyがあることだけから、毎回Entraへの往復が発生すると見積もらない。初回と更新時を含む観測で判断する。[Managed Identity policyのcache仕様](https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy)

採用判断は、必要な認可・停止制御を満たしたうえで、実回線と想定負荷での会話応答時間と音声品質が目標に入ることを条件とする。現時点の推奨は、既存WebRTCの制御方式を維持し、音声の性能受入を追加し、その測定から接続先・モデル・WSS中継の見直しへ進むことである。

## 今回の確認範囲

main更新前に調査ファイルを退避し、`git fetch origin` と `git merge --ff-only origin/main` で `6e954d3` から `ac9bc27` へ更新した。差分一覧と実装を確認し、前述の音声・ネットワーク主要資材群への `git diff --exit-code` は差分なしだった。

Azure DNS変更の確認には、既存の `test_azure_dns_identity.py`、`test_dns_selection.py`、`test_prepare_secrets.py`、`test_configuration.py` を実行し、計156件がPASSした。これらはオフラインでID入力・設定合成・旧資格情報を使わない動作等を検査するテストである。証明書workerのPowerShell試験は前回の差分確認では未実行で、実AzureのID割当・DNS更新・証明書発行の成功は判定していない。過去の音声実測と、今回のソース差分・オフライン検査の範囲を分けて扱う。

電話→自社サーバー→WebRTCの追補では、`git ls-remote origin refs/heads/main`の結果とローカルHEADがともに`ac9bc2700fbad51c3459002b89a34ec50abe5b01`であることを再確認した。公開API例とpolicy、設定検証、NSG／Firewall／UDR、ブラウザー試験の接続処理を照合した。実Azureの稼働設定を再取得して構成ドリフトを検査したものではない。本書で再利用対象とする音声契約は公開API定義と既存の結合試験にあるものを指す。構築・登録の実行方法は現在の導入手順を参照する。

今回のオフライン検証では、公開例の`model-webrtc`・`model-realtime-observer`・`model-voice-live-webrtc`へ合成IDのapp-only登録を追加し、既存の設定検証が受け入れることを確認した。作成・終了・observerの期限を1800秒へ揃えた設定は通り、observerだけ600秒に戻した不整合設定は拒否された。この検証は設定をAzureへ公開せず、実際のMI tokenの発行・認可成功を代用するものではない。

既存の`test_configuration`・`test_prepare_coverage`・`test_contract_rate_policy`の121件と、`test_realtime_policy`の15件、計136件がPASSした。Realtimeの検証はリポジトリ内の固定版PowerShell（`.tools/powershell/7.6.7/pwsh`）と既存のBicepコンパイラーを利用し、所有者チケット・SDP・失敗時cleanup等をオフラインで検査した。PATH上の`pwsh`は未登録だが、この固定版による当該試験は実行できた。今回、自社アプリの実装・配備、実際の電話接続、サーバー用WebRTCライブラリとAzure間の接続、性能測定は実施していない。

続くリージョン差の確認では、テスト・本番configの音声6モデルの固定版・SKUと、MicrosoftのGlobal Standard／Standardの地域表を照合した。Speech単体とVoice Liveは別の対応表として確認した。Japan Eastへの実配備、quotaの取得、新しい音声リソースの作成や既存リソースの移設は行っていない。
