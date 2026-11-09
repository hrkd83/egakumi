# 通常保守のスクリプト

[運用手順](../../docs/operations/README.md) · [運用テスト](../../docs/testing/operations-tests.md)

構築済みの同じ環境のparams・Core stateを使う。リポジトリルートで実行し、反映は`--execute`を明示する。計画と反映は別の結果として記録する。

| 入口 | 用途 | 計画時のAzure通信 |
| --- | --- | --- |
| `release.py` | `--component gateway/portal/all`で既存の個別配備を利用。単体は`--image-digest`で互換な旧版へ切戻し | なし |
| `model.py` | config正本の一配備を`--name`と`--intent create/update`で選ぶ。容量はPATCH、版変更は追加の明示指定 | 現物を読んで比較する |
| `entra_secret.py` | `--stage create/switch/rollback/retire`で新値作成、KV／CM切替、確認後の旧値廃止を分ける | 既定計画では変更しない。各stageの前提は手順参照 |
| `certificate.py` | `--stage check/renew/sync`で既存workerと参照先・公開TLSを確認する。期限前の計画更新は`--stage renew --rotate` | 既定計画は通信なし。実際の確認にも`--execute`を使う |
| `api_key.py` | 通常購読の主副の片側を再生成し、指定した新規秘密ファイルへ保存 | なし。実行時に現物の購読を照合する |
| `waf.py` | 既存の用途別WAFのreconcilerを`--scope gateway/portal/all`で利用 | なし |
| `monitoring.py` | 対象Action groupのメール宛先、または既存アラート1規則の閾値を更新 | 現物を読んで比較する |

```sh
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component all
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component all --execute
```

全入口の引数は`--help`で確認する。ソース・config・API定義の編集は、変更目的に合わせて手順どおり行う。Portalで利用者本人の認可が必要な操作は、正規のPortal画面・認証経路を使う。

## 保存と途中停止

- `common.py`が同じ環境の入力・所有記録を照合する。反映時は環境別のローカルロックを取り、このcheckoutでの同時運用を拒否する。別端末・Azure Portal等の同時変更まで防ぐロックではない。
- 子のAzure CLIにも応答本文のファイルログを無効化する環境設定を継承する。認証・secret作成の結果をCLI内部ログへ残さず、呼出元のメモリーと指定した保護保存先だけで扱う。
- 記録は`.local/<environment>/state/operations/`、秘密は同じ環境の`secrets/`で保護する。ディレクトリ700・ファイル600とし、秘密を標準出力・コマンド引数へ渡さない。
- キー・secretの再生成等は応答不明で自動再送しない。変更要求済み・保存済み・認証未確認等を記録し、同じ生成処理を重ねる前に対象と記録を確認する。
- ETagを提供するAPIでは条件付き更新する。APIが提供しない場合の直前比較は、外部操作との原子的な競合防止を保証しない。現物読戻しと、他の保守を同時実行しない前提を守る。
- `applied`や`completed`はスクリプトの工程完了である。新規サインイン・本人接続・業務API・通知受信等、手順に指定する受入まで自動的にPASSへ変えない。

初期構築は`azd up`、利用終了の撤去は既存の`azd down`手順で行う。通常保守のために基盤全体を作り直さない。
