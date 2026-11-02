# Portalの整理前検証記録

> **履歴資料：2026-11-01の文書整理前スナップショットからの抜粋。現行の検査結果ではありません。** 記載の依存・脆弱性数・実行内容は当時のREADMEに記載された範囲です。今回の文書整理で再実行・再検証した結果ではありません。

保存元：`apps/portal/README.md`の「検証」節。本文を抜粋し、履歴注記と相対リンクだけを調整しています。現行の実行方法は [DEVELOPMENT.md](../../DEVELOPMENT.md#テストの実行方法)、Portalの構成は [Portal README](../../apps/portal/README.md)を参照してください。

## 保存した記録

```sh
npm run lint
npm test
npm run build
npm audit
```

2026-10-30の導入時検査では、開発用を含むnpm auditの既知脆弱性は0件です。これは検査時点の結果であり、依存更新時にも再実行します。

shadcnのCLI依存に既知の低深刻度の脆弱性があるため、生成した部品と必要なCSSだけをアプリに保持しています。CSSの出典・MITライセンス・更新時の注意は[../../apps/portal/styles/README.md](../../apps/portal/styles/README.md)を参照してください。

SQLドライバーはTedious 19.2.1とAzure Identity 4.13.0を使用します。新しい組合せのMSAL依存に既知の脆弱性があるためです。MSALが使用する`uuid.v4()`は互換な修正版11.1.1へ限定上書きしています。依存更新時は、この上書きがまだ必要かをauditと認証試験で確認してください。

[履歴一覧](README.md)
