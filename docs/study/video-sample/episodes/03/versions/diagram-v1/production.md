# 第3回 NSGによる通信制御

2026-11-02（JST）制作。完成動画を制作済み。自動検証・代表場面の目視確認・ブラウザーの通常速度再生とチャプター移動に合格。

到達点：優先順位、送受信、状態追跡を使って、代表的な通信の許可・拒否を判断できる。

出典は[CONTENT-PLAN](../../../CONTENT-PLAN.md)の第3回、[01 ネットワークの制御](../../../01-network-control.html#nsg-associations)の11〜19節と対応するリポジトリ定義。稼働中Azureからの取得値・疎通実測ではない。

## 制作範囲と編集判断

ユーザーの追加指定「実際の設定値をしっかり解説してね。多少時間のびてもいいから」を反映した。15分へ圧縮せず、公開params/configの具体CIDR・優先度・宛先ポート・送受信元・Allow/Denyの条件を、通信の目的と結び付けて説明する。通常速度で生成した音声の実長から、ロゴ・章の切り替えを含む24分39.542秒に確定した。

[台本](script.json)は最終119台詞・10章、読み上げ本文8,761字。4台詞の回専用ホログラム導入から本編へ入り、確認問題3問、まとめと第4回「UDRと往復経路」への問いを含む。第1回の題名だけを変えた原稿にはしていない。

数値は教材と追跡済み公開設定例に基づく。PEの全実IP、管理VM単体の実IP、APIMの実VIP、Firewall公開IPは作成後の出力から得る値として扱い、架空の固定IPを補っていない。NSGのAllowと、DNS・経路・Firewall・サービス認証の成功を区別した。

## 共通仕様と素材

[制作手順](../../PRODUCTION-GUIDE.md)・[制作プロファイル](../../production-profile.json)・[現行キャスト](../../podcast-cast-gemini.json)に従う。

| 項目 | この回の指定 |
| --- | --- |
| 音声モデル | Google Cloud `gemini-3.8-flash-tts` / Gemini 3.8 Flash TTS |
| MAKO | Aoede / Moderator / 読みはマコ |
| MANABU | Charon / Product Manager / 読みはマナブ |
| IKUMI | Orus / Solution Architect / 読みはイクミ |
| 話速 | 全員1.0倍。生成後の速度・ピッチ変換なし |
| 映像 | 1600×900、24fps、H.264 / AAC |
| 冒頭 | 既存共通ロゴ4秒、3人の顔・名前・役割、回専用の掛け合い |
| 本編 | 共通フレーム、話者表示、最大2行の字幕。WebVTTも保存 |
| BGM | `assets/music/main-bgm.mp3`を2秒クロスフェードでループし、会話中はダッキング |
| 終了 | 既存共通ロゴ8秒、`brand-theme.mp3`の63.8〜71.8秒。先頭1秒・末尾2秒のフェード |
| 音量目標 | 約−16 LUFS / True Peak約−1.5 dBTP。最終実測は−16.01 LUFS / −1.5 dBTP |

共通`assets/`の既存肖像・ロゴ・音楽を使用し、顔・身体・ロゴは再生成しない。担当回の`episodes/03/`と`output/lesson-03-gemini/`のみを変更する。共有素材・共有プログラムの開始時ハッシュは出力先の[source-preservation-baseline.json](../../output/lesson-03-gemini/source-preservation-baseline.json)へ保存した。

## 章と資料の対応

台本の`sourceReferences`と各章の`sourceRefs`にも出典を保持する。そこでの`file`は`video-sample/`基準。

| 章ID | 内容 | 教材の節 |
| --- | --- | --- |
| `opening` | 同じ443でも、通れる相手が違う？ | 11・19節 |
| `map` | 4つのNSGと、送信・受信の向き | 11節 |
| `rules` | 小さい優先度から、最初の一致まで | 12・13節 |
| `api` | 通常APIの入口を、両端で確かめる | 13・14・19節 |
| `pe` | 同じPE区画でも、接続先を分ける | 16・17・19節 |
| `state` | 許可した接続の応答と、新規接続 | 12節 |
| `platform` | 基盤通信と、NSGの外にある判断 | 12・15・17節 |
| `options` | 名前より、Accessと優先度を読む | 17・18節 |
| `check` | 3つの通信を、自分で判定する | 18・19節 |
| `summary` | 判定の順番を持ち帰る | 19節。次回の問いだけ20節へ接続 |

## 設定値・図・実装の対応

設定入力の正本は[params/production.example.json](../../../../../params/production.example.json)と[config/production.json](../../../../../config/production.json)。値はこの2ファイルの組合せによる公開例であり、稼働環境への照会結果ではない。

| 教材 | 動画で説明する値・判断 | 回専用の図 | リポジトリの定義 |
| --- | --- | --- | --- |
| [11節：関連付け](../../../01-network-control.html#nsg-associations) | AppGW `10.40.0.0/24`、PE `10.40.1.0/24`、Firewall `10.40.2.0/26`、APIM `10.40.2.64/26`、管理VM `10.40.2.128/26`、ACA `10.40.2.192/26`。NSGはAppGW/APIM/PE/管理VMの4区画。PEは`privateEndpointNetworkPolicies: NetworkSecurityGroupEnabled` | `cidrs.svg`、`associations.svg`、PEポリシーのカード | [network.bicep](../../../../../infra/modules/network.bicep)、公開params |
| [12節：評価順・DNS](../../../01-network-control.html#nsg-evaluation) | 同じNSG・同じ方向で小さい優先度から最初の一致。受信4096は4 NSG、送信4096はAPIM/管理VMのみ。既定は65000以降。DNS送信400/TCP53・410/UDP53はAppGW/APIM/管理VMへ追加。`dnsServers=[]`では`168.63.129.16`。一部のAzure基盤通信は通常NSG処理の対象外。応答はstateful、新規接続で変更確認 | `priority.svg`、`associations.svg`、DNSカード、応答の図 | [network.bicep](../../../../../infra/modules/network.bicep)、[network-bindings.bicep](../../../../../infra/modules/network-bindings.bicep) |
| [13節：AppGW](../../../01-network-control.html#nsg-agw) | 受信100は`0.0.0.0/0`→AppGW TCP80/443、110はGatewayManager TCP65200–65535、120はAzureLoadBalancer全プロトコル/全ポート、150は公開gRPC TCP8443、194はFW公開IP/32→TCP443。送信100はAPIM TCP80、110はKV実PE IP群/TCP443、120は内部gRPC/TCP443、200はInternet全プロトコル/全ポート。送信の独自終端拒否なし | `agw-inbound.svg`、`agw-outbound.svg` | [network.bicep](../../../../../infra/modules/network.bicep)、[network-bindings.bicep](../../../../../infra/modules/network-bindings.bicep) |
| [14節：APIM入口・PE](../../../01-network-control.html#nsg-apim-private) | 受信100はAppGW/TCP80、110はApiManagement/TCP3443、120はAzureLoadBalancer/TCP6390、130はAPIM区画/UDP4290、140はAppGW/TCP443、150は管理VM実IP/TCP443。送信100はFoundry実PE IP群/TCP443、110はKV実PE IP群/TCP443、176はAPIM区画→ACA実内部IP/TCP443。NSG宛先はAPIM区画、実VIPとの違いを説明 | `apim-inbound.svg`、`api-compare.svg`、APIM→PEの図、Brokerカード | [network-bindings.bicep](../../../../../infra/modules/network-bindings.bicep)、[credential-broker-network.bicep](../../../../../infra/modules/credential-broker-network.bicep) |
| [15節：APIM基盤通信](../../../01-network-control.html#nsg-apim-platform) | 送信200 Storage/TCP443、210 Sql/TCP1433、220 AzureKeyVault/TCP443、230 AzureMonitor/TCP1886・443、235 EventHub/TCP443・5671・5672、240 AzureActiveDirectory/TCP443、250 AzureConnectors/TCP443、260 Internet/TCP80・443、270 Internet/UDP123、280 APIM区画/UDP4290。タグは自分たちのリソース1個やFQDN一覧ではない | `apim-platform-storage.svg`、`apim-platform-monitor.svg`、タグ/FQDNのカード | [network.bicep](../../../../../infra/modules/network.bicep) |
| [16節：PE](../../../01-network-control.html#nsg-pe) | 受信100 APIM→Foundry/TCP443、110 AppGW・APIM→KV/TCP443、140 VM実IP→Foundry・KV/TCP443、160 ACA・管理VM区画→ACR/TCP443、175 ACA→SQL/TCP1433、177 ACA→KV/TCP443。宛先はサービスごとの実PE IP群。受信4096あり、送信の独自4096なし | `pe-services.svg`、`pe-compare.svg` | [network-bindings.bicep](../../../../../infra/modules/network-bindings.bicep)、[portal-network.bicep](../../../../../infra/modules/portal-network.bicep)、[credential-broker-network.bicep](../../../../../infra/modules/credential-broker-network.bicep) |
| [17節：管理VM](../../../01-network-control.html#nsg-management) | `operatorAddressPrefixes=[]`では受信Deny4080、指定時は指定元→VM実IP/Allow100。`operatorProtocol: Tcp`、`operatorPorts: ["22"]`、追加管理規則は空。送信100はVM区画→Foundry・KV実PE IP群/TCP443、120はgRPC比較、170はACR/TCP443。基盤送信300/310/320/330は認証・管理・監視・StorageのTCP443、350はInternet/TCP80・443、360は`ntpAddressPrefixes`（公開例Internet）/UDP123 | 管理VM受信カード、管理VM→PEカード、`vm-platform.svg` | [network.bicep](../../../../../infra/modules/network.bicep)、[network-bindings.bicep](../../../../../infra/modules/network-bindings.bicep)、[portal-network.bicep](../../../../../infra/modules/portal-network.bicep)、公開params/config |
| [18節：切り替え](../../../01-network-control.html#nsg-options) | gRPC公開例は有効/公開8443/内部443、無効時は5規則を同名Denyへ変更。クライアント配列が空ならWeb拒否4080/公開gRPCもDeny。`apimKeyVaultAccessEnabled=true`、falseならAPIM送信110をDeny/PE受信110からAPIM除外、基盤220/IAMとは別。`certificatePublicProbeEnabled=true`で194 Allow、falseでDenyでもWeb100が先なら許可。`credentialProfiles`非空時のみBroker送信176作成、PE175/177は常時作成、176の残存は実ルール確認 | `grpc-switches.svg`、`overlap.svg`、`overlap-empty.svg`、KV/監視/Brokerカード | [network.bicep](../../../../../infra/modules/network.bicep)、[network-bindings.bicep](../../../../../infra/modules/network-bindings.bicep)、[credential-broker-network.bicep](../../../../../infra/modules/credential-broker-network.bicep)、公開params/config |
| [19節：通信例](../../../01-network-control.html#nsg-checks) | AppGW→APIM TCP80は両端100、VM→APIM TCP80は両端4096、VM→APIM TCP443は送信120/受信150。APIM→Foundry PE TCP443は両端100、AppGW→同PEはPE4096。ACA→SQL PE TCP1433はPE175、VM→同PEは標準許可なし。APIM/VM→Internet TCP25は送信4096 | `api-compare.svg`、`pe-compare.svg`、`quiz-1.svg`〜`quiz-3.svg` | 上記4 Bicepファイル、公開params/config |

Azureの基本仕様は[NSGの概要](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview)で、優先順位・最初の一致・stateful・変更後の新規接続・基盤通信例外を照合した。PEへのNSG適用と`NetworkSecurityGroupEnabled`の意味は[Private Endpointのネットワークポリシー](https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy)で照合した（2026-11-02確認）。

## 字幕・図の確認

字幕は読み上げ用カタカナと表示用の製品名を分け、意味を一致させた。最大2行ずつの`subtitleChunks`を使い、IP・CIDR・設定識別子の途中で改行しないよう調整した。演技指示は自然言語のstyleへ置き、本文や字幕には表示しない。

主要設定の16場面で対象行を強調した。対象台詞末尾番号は009/010（CIDR）、020/022（AppGW受信）、040/041（AppGW送信）、048/049（APIM受信）、059/060（PE受信）、074/075（APIM保存・認証）、077/078（APIM監視・Web・同期）、089/090（管理VM送信）。強調版SVGは`diagrams/*-focus-*.svg`へ保存した。

確認問題は、①管理VM→APIM TCP80の拒否、②APIM→Foundry PEの同一接続への応答と新規接続の違い、③Web100 Allowと監視194 Denyの優先順位、の3問。`quiz-1.svg`〜`quiz-3.svg`は各行に条件・送信側・受信側などのラベルを付けた。

最終119台詞と回専用図は技術レビュー済み。全119台詞のプレビュー生成に成功し、初稿の代表40種と主な最終修正場面を目視した。完成MP4から16代表場面と終端1枚を抽出して目視確認し、切れ・重なり・設定値の誤表示がないことを確認した。詳細は[目視QA](../../output/lesson-03-gemini/qa/visual-review.json)を参照する。

## 準備・送信承認・音声生成

事前検査`check-production.py --episode 3`は合格。Python / Node / ffmpeg / ffprobe / gcloud / sharpと共通素材・プログラムのハッシュを確認した。開始時空き容量は4.48 GiB。

`prepare-gemini-lesson.py`と音声生成dry-runは合格。119リクエスト、上記の3声、通常速度1.0倍、`workers=1`で生成する。既存のGoogle Cloudユーザー認証を使い、既定プロジェクトやIAM・キーは作成・変更しない。トークンは台本・設定・成果物へ保存しない。

初回の外部送信は、設定値を含む台本をGoogle Cloud TTSへ送信することへの明示承認が必要としてautomatic approval reviewに拒否された。その時点ではAPI送信を開始していない。送信先・プロジェクト・送信内容・台本を提示後、ユーザーが「完成させてください」と指示し、送信を承認した。承認後、119台詞すべてを生成し、全HTTP 200・STOPで完了した。再送・自動再試行は0回。原音発話は合計1,428.4秒（23分48.4秒）。[API使用量](../../output/lesson-03-gemini/speech-usage.json)は入力テキスト42,182トークン、音声出力35,710トークン、合計77,892トークン。費用概算・実請求額は算定していない。

送信先は`aiplatform.googleapis.com`のプロジェクト`egakumi-example`、`global`、`gemini-3.8-flash-tts:generateContent`。送信内容は読み上げ本文、声、自然言語の話し方、直前台詞の文脈で、公開例のCIDR・NSG優先度/ポート・設定フラグを含む。`.local`設定や稼働Azureの値は取得していない。承認と送信範囲の記録は[transmission-approval.json](../../output/lesson-03-gemini/transmission-approval.json)を参照する。

成功済み原音はリクエスト・音声ハッシュで再利用する。429等では新規送信を止め、結果不明のリクエストは自動再送しない。意図して再録する場合は新IDを付け、既存原音・履歴を保持する。

## 再生成・書き出し・検証コマンド

以下はリポジトリルートから実行する。この記録へコマンドを掲載したことは、その工程が成功済みという意味ではない。

```sh
python3 docs/study/video-sample/check-production.py --episode 3
node docs/study/video-sample/build-lesson.mjs --script episodes/03/script.json --output lesson-03-gemini --preview true
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/03/script.json --config episodes/03/generation.json --output lesson-03-gemini
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/03/generation.json --workers 1 --dry-run
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/03/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/03/script.json --generation-config episodes/03/generation.json --cast podcast-cast-gemini.json --output lesson-03-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/03/script.json --generation-config episodes/03/generation.json --cast podcast-cast-gemini.json --output lesson-03-gemini > docs/study/video-sample/episodes/03/verification.json
python3 -u docs/study/video-sample/episodes/03/serve-preview.py
```

既存設定を更新するときだけprepareへ`--replace`を追加する。表示字幕・図のみの修正でも設定を更新し、generateで検証済みキャッシュと新設定を結び直してから再合成する。音源計画・間・ハッシュが同一なら、ビルダーの`--reuse-audio true`を使える。元台本を書き換える`--write-script`は使わない。

最終検証では、生成証跡・ハッシュ・通常速度・字幕・カメラ・BGM・共通終了・全編デコード・映像音声の長さ・音量を確認する。検証失敗時の空ファイルや途中結果を合格とは記録しない。標準尺14〜17分から外れた警告が出た場合は、設定値の説明を優先して延長するユーザー指定と、実長・編集判断を併記する。

## 完成情報

| 項目 | 結果 |
| --- | --- |
| 完成MP4 | [egakumi-study-03.mp4](../../output/lesson-03-gemini/egakumi-study-03.mp4) |
| 完成尺 | 24分39.542秒（1,479.5416667秒） |
| 映像 | 1600×900 / 24fps / H.264 / 35,509フレーム |
| 音声 | AACステレオ / 48kHz / 全員通常速度1.0倍 |
| 台詞・字幕 | 119台詞を省略なし / 129字幕キュー / 最大2行 |
| ファイル容量 | 67,504,296 bytes（約64.38 MiB） |
| MP4 SHA-256 | `1167ee763eda542e89202c9c54d2aaa642161558413833fbfbc435c484552af2` |
| 自動検証 | 合格。全編映像・音声デコード、生成証跡、音声・リクエスト・台本ハッシュ、通常速度、字幕、カメラ、BGM、8秒終了を照合 |
| 音量 | −16.01 LUFS / True Peak −1.5 dBTP |
| 末尾の音声余裕 | 最短0.141667秒 |
| 目視確認 | 完成MP4の16代表場面と終端1枚：合格 |
| 聴感確認 | 未実施。発音や会話の自然さは実際の聴取・認識による確認をしていない |

自動検証には「24.66分は標準14〜17分の目標外」という尺の警告が1件ある。設定値の説明を優先し尺延長を認めたユーザー指定に基づく編集結果で、再生・デコード・字幕・音量の失敗ではない。話速を変えたり長い無音を加えたりして尺を調整していない。

目視QAには、Broker字幕の一箇所で「内部IPへ／のTCP443」と文節途中で改行する、という非阻害の編集メモがある。文字の欠落や表示枠外への切れはない。

### チャプター

| 開始 | 内容 |
| --- | --- |
| 00:05 | 同じ443でも、通れる相手が違う？ |
| 00:37 | 4つのNSGと、送信・受信の向き |
| 03:15 | 小さい優先度から、最初の一致まで |
| 06:03 | 通常APIの入口を、両端で確かめる |
| 09:16 | 同じPE区画でも、接続先を分ける |
| 12:28 | 許可した接続の応答と、新規接続 |
| 13:48 | 基盤通信と、NSGの外にある判断 |
| 18:13 | 名前より、Accessと優先度を読む |
| 22:03 | 3つの通信を、自分で判定する |
| 23:40 | 判定の順番を持ち帰る |
| 24:31 | 共通クロージング |

### 保存した成果物

- [再生ページ](../../output/lesson-03-gemini/index.html)
- [字幕 WebVTT](../../output/lesson-03-gemini/lesson-captions.vtt)
- [台本全文](../../output/lesson-03-gemini/lesson-transcript.md)
- [タイムライン](../../output/lesson-03-gemini/lesson-timeline.json)
- [書き出し記録](../../output/lesson-03-gemini/lesson-export.json)
- [検証結果](verification.json)（[出力先コピー](../../output/lesson-03-gemini/verification.json)）
- [API使用量](../../output/lesson-03-gemini/speech-usage.json)
- [出典ファイルのハッシュ](../../output/lesson-03-gemini/source-basis.json)
- [共有素材・プログラムの保持確認](../../output/lesson-03-gemini/source-preservation.json)
- [完成動画の目視QA](../../output/lesson-03-gemini/qa/visual-review.json)

成果物の正本はローカル出力ファイル。生成物はGit管理対象外。共有README・計画・設定・素材・レンダラーは変更していない。

### ローカル再生

[一時再生ページ](http://127.0.0.1:65298/)を起動済み。ページの「動画を保存」からMP4を保存でき、字幕・台本・共通クロージングも取得できる。localhostのサーバー停止後も、完成MP4と保存済みindex.htmlは出力フォルダーに残る。

ブラウザーでは長さ1,479.541667秒、1600×900、再生速度1.0、readyState=4、メディアエラーなしを確認した。後半18:13のチャプターを押すとcurrentTime=1,093.710408秒となり、指定位置から再生した。seekable範囲は全編[0, 1,479.541667]。確認後はページを先頭・停止状態へ戻し、視聴用のタブを残した。

共有`serve-episode.py`はHTTP Range未対応で、ブラウザーのseekableが[0,0]となりチャプター移動が0秒へ戻った。共有ファイルを変更せず、回専用の[serve-preview.py](serve-preview.py)を追加して解決した。127.0.0.1だけへバインドし、空きポートを選ぶ。単一bytes Rangeの206/416、suffix/open-ended、通常GET、HEAD、既存のパス処理を実サーバーで検証済み。仕様は[RFC 9110 §14](https://www.rfc-editor.org/rfc/rfc9110.html#section-14)と[Python公式HTTPサーバー](https://docs.python.org/3/library/http.server.html#http.server.SimpleHTTPRequestHandler)に基づく。共有ツール側への改善は別途集約できるが、本回の再生は専用サーバーで完了している。

[ブラウザー確認記録](../../output/lesson-03-gemini/browser-verification.json)を保存。共有素材・プログラム33ファイルのハッシュは制作開始時からすべて一致した。
