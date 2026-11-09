# APIMのAPI定義・ポリシーを変更する

[共通確認](common.md) · [API定義](../../apps/gateway/apim/apis/openai-inference-v1.json)

構築済みの隔離環境で、既存Chat APIの本文上限を変更し、正常要求・上限超過の拒否・元への復旧を確認する。新APIやバックエンドの追加、初期構築のやり直しは不要。以下は操作例であり、記載だけで試験済みにはしない。

| 変更内容 | 正本 |
| --- | --- |
| API・operation、許可モデル、入力制約 | `apps/gateway/apim/apis/<API名>.json`。制約から操作ごとのポリシーも生成される |
| 全APIに共通するポリシー | `apps/gateway/apim/policies/service.xml`と、それが読み込むXML。変更後は認証・継承順も確認する |

資材が管理するものはソースを変更してGで反映する。Azure Portal上だけを編集して、次回配備で上書きされる状態を正本にしない。共通認可や`<base />`の継承を外して復旧しない。全体ポリシーの確認XMLは`render_apim_policy.py`で生成できるが、その出力だけでAPI別の生成ポリシーを検証したことにはならない。

```sh
python3 -B scripts/render_apim_policy.py --config "$DEPLOY_PARAMS_FILE" \
  --output "$DEPLOY_LOCAL_DIRECTORY/generated/apim-policy-review.xml"
```

出力先は未作成のファイルを指定する。API別制約の修正は以下の代表例で確認する。

## 変更する箇所

paramsの`apiDefinitions`が空配列の場合は、標準の `apps/gateway/apim/apis/openai-inference-v1.json` の次の1箇所を変更する。明示した定義を使う環境では、実際に読まれる同じAPI定義を対象とし、標準ファイルを変えただけで反映されると考えない。

```text
authorization.operationConstraints["chat-completions"]
  .profiles の profileId == "chat" の maxRequestBytes
```

変更前32768 bytesを4096 bytesへ変更する。元の値が異なる環境ではその値を保護記録へ控え、復旧時は控えた値へ戻す。`vision`、`compatible`、`router`のprofile、許可モデル、認証、WAF、他operationは変更しない。

`profiles`を持つoperationの外側の`maxRequestBytes`は、読込時に全profileの最大値から組み立てられる。外側だけを4096にしてもChatの上限変更にはならない。Visionの大きい上限が残るため、外側とChatの値が同じになるとも限らない。

## 変更前の準備

1. 同じ環境のparams・state・APIM識別子と、同時実行中の配備がないことを確認する。
2. API定義の変更前を保存し、差分がChat profileの上限一箇所だけになることを確認する。他人の未確定差分を含めない。
3. `chat`の`allowedModels`に含まれ、実配備済みの検証モデルと、対象APIを呼べる専用キーを使う。値は秘密保管から読み、引数・通常ログへ出さない。
4. 実値・試行結果・要求ファイルは `.local/<environment>/evidence/<work-id>/` に保護保存する。

## 計画して反映する

```sh
python3 -B scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"
```

計画と今回のソース差分を確認した後、同じ入口で実行する。

```sh
python3 -B scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute
```

この入口はAPIだけの軽量更新ではなく、Broker・CM・SQL・API・WAF等を含めて照合する。計画は実Foundryの読取照合を行わず、実行ではBroker／SQL処理の後にモデル照合が行われるため、事前に人が現物とparamsを確認する。モデル不一致の負例を作るためにGを実行しない。目的外の差分がある場合は先に分離する。既存イメージを再利用できるかも計画・記録で確認し、API変更のたびに新しい環境を構築しない。

今回の終了コードと配備記録を確認する。古い成功JSONやhealthだけを、今回のAPI更新成功の証拠にしない。

## 正常と拒否を確認する

要求先は既存の `/models/openai/v1/chat/completions`、profile指定は `X-Egakumi-Profile: chat` とする。本文は通常の合成テキストだけを使い、画像・SQL攻撃文字列等を混ぜない。

- **正常：** 有効な検証キーと許可モデルで、小さいJSONを1回送る。WAF通過や401だけでなく、モデルの正常応答を確認する。
- **境界：** 同じ正しいJSON構造で、送信するUTF-8本文全体が4096 bytesと4097 bytesになる2種類を用意する。差は`messages[].content`の合成文字列だけにする。空白・改行・JSON記号もbytesへ含める。
- 4096 bytesは本文サイズ検査を通過し、4097 bytesはAPIMの413／`REQUEST_TOO_LARGE`となることを確認する。403だけを本文上限の成功と数えない。
- `chat`以外のprofileや別APIの上限が今回の差分で変わっていないことを、定義と配備先の対象設定で確認する。

APIキーを含むcurlコマンドを作業票へ貼らない。HTTPクライアントは保護ファイルからキーを読み、秘密値を表示せず、status・公開エラーコード・要求bytesだけを必要範囲で記録する。

## 元へ戻す

1. Chat profileの`maxRequestBytes`だけを変更前の値へ戻し、差分を確認する。
2. Gの計画を確認し、同じコマンドに`--execute`を付けて反映する。全体ポリシーやAPI自体を削除しない。
3. 元の上限が32768なら、先ほどの4097 bytesの正常JSONが本文サイズ検査を通過し、通常要求も成功することを確認する。
4. 最終のソース・配備先設定が変更前に戻ったことと、他API・既存キーの保持を記録する。専用要求ファイル等の保持・回収も決める。

応答不明なら直ちに再配備を重ねず、実行履歴と現在設定を読む。計画・反映・確認・復旧を一組で行い、同じ変更を試験行ごとに再配備しない。
