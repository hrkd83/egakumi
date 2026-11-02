# 第A2回 ハブ接続後のDNS・通信制御と切替

出典：`docs/study/CONTENT-PLAN.md`。到達点：中央DNS・Private DNS・NSG／Firewallの許可・出口IPを整合させ、段階切替、新規接続での検証、切戻し、IaCへの反映と再配備の管理を説明できる。

対応資料：[50節：中央DNS](01-network-control.html#hub-dns)、[51節：Private DNS](01-network-control.html#hub-private-dns)、[52節：通信許可・送信元IP](01-network-control.html#hub-policy)、[54節：切替・再配備](01-network-control.html#hub-rollout)

資料リンクは`docs/study/`基準。台本のsourceReferences.fileはvideo-sample/基準。

共通仕様は../../PRODUCTION-GUIDE.md、配役は../../podcast-cast-gemini.jsonを読む。
script.jsonは制作開始用の未完成原稿。TODOをすべて置き換え、約15分の対話・図・字幕を執筆する。
レビューとプレビュー後にstatusをready-for-generationへ変更する。
共通ファイルは変更せず、このフォルダーと担当回のoutputだけを編集する。
