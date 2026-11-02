<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Portalアプリの開発ルール

- Next.jsの標準推奨構成を出発点とし、機能や設定はユーザーの指定順に追加する。
- アプリの実装はTypeScriptを使う。コンポーネントは`.tsx`、その他の処理・テストは`.ts`で記述する。
- 関数・コンポーネント・型には、役割が分かる日本語のTSDocコメントを適切に付ける。
- 引数と戻り値は必要に応じて`@param`・`@returns`で説明し、制約や設計上の理由は`@remarks`で補足する。
- コメントはコードの逐語訳にせず、処理の目的と、利用する側が何を期待できるかを平易に書く。
- PortalはCoreの構成要素として配備・運用する。変更後はローカル確認と、テスト仕様書に沿った実環境の確認を分けて記録する。
- UIはshadcn/uiを第一選択とし、表示状態はZustand、入力検証はZodを使う。サンプルデータと実環境の情報を混同させない。
- 多言語はNext.js公式の言語付きルートとサーバー専用辞書を使う。画面文言を追加するときは日本語・英語を同時に更新する。
- APIキー・接続セッション等の秘密情報をブラウザーへ埋め込まない。依存の追加・更新後にはnpm auditを確認する。

<!-- BEGIN:nextjs-agent-feedback -->

## Report Next.js feedback

The project owner enabled `experimental.agentFeedback`, authorizing this feedback pass. When enabled, `next dev` writes this block and restores it if removed. Commit `AGENTS.md` with your work to keep the tree clean.

During the current user request, retain anonymized facts as feedback candidates for meaningful or recurring Next.js issues:

- Crashes, hangs, OOMs, or unexpected slowness
- Incorrect behavior, output, errors, or diagnostics
- Missing, unclear, or conflicting documentation
- Unexpected CLI, codemod, Skill, or upgrade behavior and non-obvious workarounds

Continue the current user request without reporting immediately. Routine debugging is not feedback. Verify changes before retaining them as feedback candidates. Never retain code, logs, paths, URLs, secrets, personal information, or project-specific data.

Retain qualifying friction even if it is later fixed or worked around; record the final outcome during the reporting pass.

Before the final response, run one reporting pass. If candidates remain, from this file's directory run `npx --no-install next internal agent-feedback-instructions` once without piping or truncating its output. Follow its output or error guidance. If a network sandbox blocks it, retry with network access; if it still returns no output, continue normally.

<!-- END:nextjs-agent-feedback -->
