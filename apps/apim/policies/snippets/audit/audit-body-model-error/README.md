# HTTP監査モデルの内部設定エラー

本文モデルreaderの固定PolicyId `core-audit-body-model-read`だけを、本文なし500へ分けるXMLフローです。`policy-error`／`OPERATION_SETTINGS_INVALID`を既存Gateway監査へ1回渡し、既存のquery-error記録、要求IDと`Cache-Control: no-store`を維持します。入力・例外・内部設定の詳細を応答やログへ出しません。

HTTP共通テンプレートと、親on-errorを継承しないDocument開始・状態取得・PIIの3操作へ、先行Entra認証エラー／回数枠エラーの処理後に生成時inlineで差し込みます。LiveとRealtime開始・終了は既存のon-errorのbaseからHTTP処理へ進みます。CM専用login／browser入口は本文モデルreaderを使わず、このフローを追加しません。

既存のC#式を複製する部品ではありません。短い条件・監査呼出し・応答をXMLが所有し、空のC#・新しいAzure Policy fragmentは追加しません。BicepとPythonは同じXML正本を展開します。

MOD-125で正規フォルダーへ結線しました。本文のJSONやモデル値を読めない場合の既存空文字、対象外操作、他のPolicyIdのエラー、先行401／429は維持します。Azure反映・実HTTP受入は未実施です。

共有結線後の関連110件は62.550秒で全件成功しました。初回110件は、試験helperが正規XML snippetを展開していなかったため専用操作の2対照が失敗し、他108件は成功しました（124.048秒）。helperを正式な展開順へ揃えています。前段の共通HTTPのみの36件成功（38.229秒）とは別実行として記録します。途中の試験インデントと、snippet検査でXMLコメントを要素へ数える不備も修正しました。
