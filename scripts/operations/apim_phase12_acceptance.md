# 工程1・2のREST受入

[apim_phase12_acceptance.py](apim_phase12_acceptance.py)は、検証済みのAPI反映先へ代表REST要求を送る入口です。`GW_PHASE12_SOURCE_ROOT`、`GW_PHASE12_RUNTIME_ROOT`、`GW_PHASE12_CONFIG_FILE`で、実行ソース・利用者が選んだ主チェックアウト・保護入力を明示します。実値は保護した環境設定で扱います。

```sh
python3 -I -B scripts/operations/apim_phase12_acceptance.py key
python3 -I -B scripts/operations/apim_phase12_acceptance.py bearer
python3 -I -B scripts/operations/apim_phase12_acceptance.py invalid-bearer
```

実行前に、同じ環境のoperation lockを取得し、`state/phase12-two-stage-migration.json`の前半・後半の完了、PIIの500復元・反映・読戻し、後半の`endSnapshot`と現在のinput／Core／checkpointの一致を確認します。`state/phase12-waf-result.json`には`scopeGatewayOnly`、`managementReadbackVerified`、`inputCoreAndCheckpointUnchanged`の3項目が全てBooleanのtrueであることが必要です。未完了・不一致ならHTTP通信前に停止します。

資格情報は`state/phase12-acceptance-credentials.json`に記録したファイルだけを使います。探索・キー再生成・ログイン・権限変更は行いません。Bearerのローカルclaims確認は入口の適格性だけで、署名・本人の認証はAPIMの実応答で確認します。

key／bearerはChat・ResponsesのJSON／SSE、転送時の保存抑止、metadataのISO文字列保持、全返却Itemを保持したfunction継続、Code Interpreterの拒否を確認します。要求のstoreはtrueとし、前段で確認したAPIMのfalse強制を経由します。Chat等でstoreが応答に返らない場合、その観測自体を成功と推測しません。invalid-bearerはDocumentの開始／結果取得、PII、CMの保護された6操作だけを対象に、401・本文なし・Bearer challenge・no-store・要求IDを確認します。

HTTPS要求は自動再送・redirect・proxyを使わず、本文・SSE行・時間・出力トークンを制限します。モデル要求には推論の利用が発生します。応答本文・返却Item・関数引数はメモリー内だけで扱い、資格情報とともに保存しません。結果・相関ID・利用量・時刻は主チェックアウトの選択環境の`evidence/`へ各ケース後に保護保存し、画面には固定状態とHTTP状態だけを表示します。

TLSは[固定したclient依存](../integration-requirements.txt)と[共有context](../deployment/helpers/client_tls.py)を使います。Windowsでは`truststore`によるOS標準の証明書・ホスト名検証、他のOSではPythonの既定検証を使います。Windowsで依存を読めない場合は停止し、検証の無効化や別の信頼方式への自動切替は行いません。

これはRESTのHTTP受入です。SDK／MCP・利用量監査の照合・工程1～2全体の完了を宣言する入口ではありません。
