# 第1回：Azure設定画面改訂の履歴（出力削除済み）

2026-11-02（JST）に完成・全編検証した旧試作の記録です。旧Azure模倣版の出力はユーザー指定で削除しました。以下の確認結果・ハッシュは制作当時の記録であり、削除した動画の再生先は掲載しません。現在の採用版は[設定メニュー版の制作記録](settings-v2/production.md)を参照します。

## 編集方針

VNet概要からアドレス空間、サブネット、各リソース、リスナー、ルートテーブル、Private Endpointをたどる。Azure Portalに似せた教材画面を主役にし、包含関係・往復経路・PEとサービス本体の違いは教材図で補う。本編81台詞のうち設定画面53、補助図28。赤枠は説明する値・行・列に合わせ、画面内のカメラが寄る。字幕、番組ヘッダー、話者の表示は固定する。

初回のユーザー指定は「設定画面を模倣した画面で解説し、必要に応じて図を使う」。追加指定は赤枠・ズーム・動き。既存音声・字幕・台詞順・尺は維持する。MAKO=Aoede、MANABU=Charon、IKUMI=Orus、Gemini 3.8 Flash TTS、全員通常速度1.0。再合成APIを使わず、完成済み第1回の検証済み音声ミックスと、ロゴ・ホログラムの冒頭映像・クロージングを再利用する。

画面は教材用に抜粋した再現で、実環境を取得したスクリーンショットではない。教材用の注釈列・役割欄は、その旨を表示する。値は公開設定例とIaCに基づき、APIM VIP・FW private IP・ACA入口IP・PE IPを推測で固定しない。元の完成動画、共通仕様、他の回の制作物は変更しない。

## 出典

- [勉強会資料](../../../01-network-control.html)：1〜5節、44〜46節。PEの補足は7節。
- `infra/modules/network.bicep`：VNet、6サブネット、NSG/RT関連付け、APIM復路、ACAルート。
- `infra/modules/network-bindings.bicep`：AppGWからAPIM/ACAへの実IPを使うルート。
- `infra/modules/application-gateway.bicep`：通常HTTPSリスナーと内部HTTP/80の中継。
- `infra/modules/services.bicep`、`management.bicep`、`portal-platform.bicep`：各リソースのネットワークへの配置。
- `services-private-endpoints.bicep`、`credential-broker-network.bicep`：PEの接続先。
- Microsoft Learnの[仮想ネットワークの管理](https://learn.microsoft.com/en-us/azure/virtual-network/manage-virtual-network)、[サブネットの管理](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-manage-subnet)、[ルートテーブルの管理](https://learn.microsoft.com/en-us/azure/virtual-network/manage-route-table)：画面項目・導線の参考。画面全体の複製や実環境操作はしていない。

## 保存する原稿と制作履歴

- 派生台本：[azure-walkthrough-script.json](azure-walkthrough-script.json)
- 台詞と画面の対応：[azure-screen-storyboard.json](azure-screen-storyboard.json)
- 元台本：[lesson-01-script.json](../../lesson-01-script.json)
- 旧改訂版の出力：削除済み。現在の採用版は`../../output/lesson-01-gemini/`。

第1回を再制作しないユーザー方針により、旧版を再書き出しする専用ビルダーと画面生成ツールは廃止しました。原稿・SVGは制作内容の記録として残します。採用版の生成原音・送信記録・必要なJSONは、採用版の出力フォルダーへ統合しています。元版の書き出し・タイムライン・検証・音声キャッシュ・生成結果は同フォルダーの`provenance/source-*.json`に履歴として残し、旧元動画や独立した再制作元フォルダーは参照しません。採用版の確認方法は[設定メニュー版](settings-v2/production.md#保存データと納品確認)に記録します。

## 確認

- 原台本の全98台詞について、`visual`以外の値が一致することを確認。省略指定・全章も一致する。
- 本編用SVG81件のXMLを検査。プレビューでアドレス空間、サブネット、VMの公開IP、AppGW/復路のルート表、PE接続の図の文字・赤枠を確認。
- 元MP4のSHA-256：`0d0e94eb0daf47786c91c38b6a31c234162a41b6375c7148fb13aa0be34d3083`。
- 完成尺は957.541667秒、1600×900・24fps・22981フレーム。87台詞・115字幕cueと全章の時刻は元版と同一。
- 改訂MP4のSHA-256：`aa01c030986f8e3787b158d3d25209d0a122ff34c58f79f71073f0a32a713345`。
- `verification.json`は`status: passed`、警告なし。全編の映像・音声デコード、尺、フレーム数、元mix・字幕・台本・声・BGM・冒頭・クロージングの保持を確認。
- 動きの代表カット059/064/066を各5秒で書き出し、開始と拡大後のPNGを目視確認。059の画素比較では変更範囲が本文領域`[65,204]–[1271,714]`に限定され、領域外の字幕・話者・ヘッダーは変更0画素。
- ブラウザーで`readyState:4`、1600×900、`playbackRate:1`、動画エラーなしを確認。08:28のボタンから08:38の該当映像へ移動して停止できた。ローカルサーバーはHTTP Rangeに対応し、全編のシーク可能範囲0〜957.541667秒を確認。
- 46.333333秒・1112フレームの演出確認版は、027（MAKO）、051（MANABU）、059・066（IKUMI）を発話の末尾まで抜粋。SHA-256：`5f7a2c1baea7653e2c95e46ff1851404a9f9ad4adbb72b0bea83d30025042f72`。
- 音声の新規生成は0件。再利用するPCMミックスは元版とバイト一致。新たな全編聴感・発音評価を実施したとは扱わない。
- 初回のsegment結合で4tick（1tick=1/12288秒）のコンテナ時刻丸めを検出し、512tick/フレームへ正規化した。映像・音声両方のpacket payloadハッシュが補正前後で一致し、再エンコードは行わず時刻だけを修正。今後のビルダーも正確なsegment duration・timescale・mux後の厳密な検査に対応済み。

上記は旧試作を制作した時点の確認記録です。その後、採用版を設定メニュー版へ統一し、旧Azure模倣版の動画出力は削除しました。採用版の完成MP4とGemini生成原音・記録は、一つの出力フォルダーに保持しています。第19回以降を含む新しい回の制作は、この旧試作や第1回の出力を参照しません。
