# shadcn/uiのCSS

`shadcn.css`は公式`shadcn@4.23.0/dist/tailwind.css`のコピーです。元のMITライセンスは`shadcn.LICENSE.md`に保存しています。

生成したUI部品は`components/ui/`に置いています。生成用のshadcn CLIはアプリの実行時依存に含めず、生成された部品と実行時に必要なCSSを保持します。CSS自体に外部インポート・スクリプト・URL参照はありません。

更新時は公式CLIで生成したソース・CSS・ライセンスを確認し、依存にCLIが再追加されていないか確認してから、`npm audit`・ビルド・画面の回帰確認を行います。
