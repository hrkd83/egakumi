# 公開情報の混入検査

ローカルでは公開情報の混入を広く検査し、GitHub Actionsでは変更した追跡ファイルに含まれる明確な秘密鍵・既知トークンを検査します。どちらも[AGENTS.md](../AGENTS.md)の保存・公開境界と、差分の内容確認を補助するものです。

このチェックアウト内で実際の企業・環境情報を保存できるのは、直下の`.local/`と`.azure/`だけです。ソース・公開記録・配布物へ持ち込まず、この2領域もGitへ登録しません。その他のignore対象や作業記録へ保存許可を広げません。

## ローカルの検査

Python 3.11以上とGitが必要です。外部パッケージは不要です。clone直後にフックを有効にします。

```sh
python3 -I -B scripts/install_git_hooks.py
git config --local --get core.hooksPath
# .githooks
```

フックはcloneだけでは有効になりません。各cloneで設定します。既存の独自フック・hooksPathがある場合は上書きせず停止するので、管理者と統合してください。

| タイミング | 対象 |
| --- | --- |
| pre-commit | ステージされたファイルの実際のバイト列、著者情報、参照名、作業ツリー |
| commit-msg | コミットメッセージ、著者・コミッター、参照名 |
| pre-push | 到達可能な全履歴、削除済みファイル、著者・メッセージ・タグ、push対象SHAと送信先参照名 |

通常の履歴検査は`HEAD`と`refs/heads/`、`refs/remotes/`、`refs/tags/`を起点にします。ツールのローカル実行用名前空間は通常範囲に含めませんが、`--revisions`やpre-pushで明示した参照・SHAは祖先も検査します。

手動実行では次のコマンドを使います。

```sh
python3 -I -B scripts/check_public_data.py --staged --worktree
python3 -I -B scripts/check_public_data.py --all-history
python3 -I -B scripts/check_public_data.py --worktree-strict
python3 -I -B -m unittest discover -s tests -p test_public_data_guard.py -v
```

ローカル検査は、未承認のGUID・クラウドID・ホスト名・メール・IP・端末の絶対パス、代表的な秘密値、認証付きURLを対象にします。UTF-16、URL・Unicodeエスケープ、代表的なBase64も上限付きで検査します。未承認のバイナリー・埋め込み素材・過大なファイル・シンボリックリンクは要確認として停止します。履歴不足・読み取り失敗・不正なポリシー・検査起動失敗も成功扱いにしません。

検出値や本文はログへ出さず、規則名・実行内の連番・行番号だけを表示します。調査時も実値をIssueやCIログへ貼らず、保護したローカル領域で確認します。

## ローカル保存先と確認範囲

実環境の入力・秘密・状態・生成入力・証跡は、`.local/<environment>/`の`params.local.json`、`secrets/`、`state/`、`generated/`、`evidence/`へまとめます。`.azure/`はazdのローカル環境に使います。私有ディレクトリは権限700、秘密を含むファイルは600で保護します。正式手順で選んだ環境と明示した入力だけを使い、別の配置先や過去の状態を自動探索・流用しません。

チェックアウト直下の`.local/`と`.azure/`は、通常・strictのどちらでも内容を読みません。ルート自体がシンボリックリンクなら拒否します。ステージ・追跡ファイル・検査対象履歴へ入った場合は、空ファイルや合成データでもパス自体を拒否します。下位ディレクトリの同名領域や、その他のignore対象へ例外を広げません。

通常の作業ツリー検査では`.git`、依存・ビルド用の`.venv`、`apps/portal/node_modules`、`apps/portal/.next`と、ローカル教材用の`docs/study`を除外します。これらの除外は企業情報の保存許可でも、ステージ・履歴検査の除外でもありません。`docs/study`はローカル専用で、現在の追跡対象や送信する先端ツリーへ含めません。strictでは教材・依存生成先も確認し、私有2領域の内容は引き続き除外します。

`.state/`、ルートの`secrets/`、`params/*.local.json`、`infra/main.parameters.json`、アプリ直下の`.env*`等は実情報の保存先ではありません。利用者が明示したチェックアウト外の保護入力や、ツールがホーム等で管理する認証キャッシュは別に扱い、勝手にコピー・変更・削除しません。

Pythonのbytecodeには端末の絶対パスが入ることがあります。正式CLIは保存を抑止し、子のPythonにも継承します。開発用の`python -m`・`python -c`は`-B`または`PYTHONDONTWRITEBYTECODE=1`を指定します。bytecodeのignoreは保存境界を緩めません。

## ローカル許可リストと差分レビュー

正本は[scripts/public-data-policy.json](../scripts/public-data-policy.json)です。公開仕様の共通定数と、独立した合成例・テスト値を区別します。例外には規則・値・完全一致のファイルパス・理由を記録し、公開定数の根拠や配布素材の権利を同じPRでレビューします。企業・実環境の値を例外へ追加して通過させません。

画像・音声等は、表示内容・メタデータ・埋め込みを人が確認し、公開素材のパスとSHA-256を記録します。変更後は再確認します。実情報や機密ファイルのハッシュを公開許可に使いません。CI用の承認記録や、許可リスト更新のための先行PRは不要です。

公開前には、差分・新規ファイル全文・ファイル名・コミット情報・素材の内容を確認します。任意の企業情報や、画像・音声の意味をスキャナーだけで判定できるとは扱いません。追加の企業固有語をローカルで検査する場合は、チェックアウト外の保護ファイルを`EGAKUMI_PRIVATE_TERMS_FILE`で指定できます。この辞書はGitHubへ送信しません。

## GitHub Actionsの軽量な秘密検査

[検査workflow](../.github/workflows/secret-check.yml)は、[scripts/check_secrets.py](../scripts/check_secrets.py)で比較元・比較先のcommit間に変更された追跡ファイルを検査します。Python標準ライブラリーだけで実行します。

```sh
python3 -I -B scripts/check_secrets.py --base HEAD^ --head HEAD
```

CIの対象は秘密鍵ブロックと、形式を識別できる既知のAPIトークン・アクセストークンです。検出値はログへ出しません。企業ID・Azure／EntraのID・ドメイン・IP・著者情報・過去履歴はCIの判定対象に含めず、ローカル検査と差分レビューで確認します。Base64の再帰展開や、バイナリー素材・許可リストの承認処理もCIでは行いません。

2MiBを超えるファイルと、NULを含むバイナリーは読み取り検査を省略し、件数だけを表示します。これらの内容は差分レビューで確認します。

mainの必須チェック名は`Secret check`です。PRレビューとブランチ保護を維持し、このチェックの成功を確認して取り込みます。移行用の手動実行や追加の承認設定は不要です。

CIはGitHubへ送信した後に動くため、送信前の確認はローカルのフックと差分レビューで行います。CIの成功は、実環境情報が一切含まれないことの証明にはしません。
