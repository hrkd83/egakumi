import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { getModelCatalog } from "../lib/model-catalog.ts";
import { groupTextModels } from "../lib/text-model-choices.ts";
import { getSpeechDictionary } from "../lib/i18n/speech.ts";
import type { ModelCategory, TextGenerationModel } from "../lib/model-types.ts";

/** UI本体の集約関数をそのまま使い、ネット接続や別実装で表示順を代用しません。 */
function actualProviderGrouping() {
  const source = readFileSync(new URL("../components/model-gateway.tsx", import.meta.url), "utf8");
  const ast = ts.createSourceFile("model-gateway.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const declaration = ast.statements.find((node): node is ts.FunctionDeclaration => ts.isFunctionDeclaration(node) && node.name?.text === "groupProviders");
  assert.ok(declaration);
  const compiled = ts.transpileModule(declaration.getText(ast), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
  return new Function("groupTextModels", compiled + "\nreturn groupProviders;")(groupTextModels) as (category: ModelCategory, models: TextGenerationModel[]) => { id: string; operations: { operationId: string }[] }[];
}

test("音声カードはAzure先頭・STT/TTS順にし、他カテゴリーとAPI宣言順を変えない", () => {
  const group = actualProviderGrouping(), categories = getModelCatalog();
  const before = structuredClone(categories);
  const speech = group(categories.find((category) => category.id === "speech")!, []);
  assert.deepEqual(speech.map((provider) => provider.id), ["azure-ai-speech", "openai"]);
  assert.deepEqual(speech[0].operations.map((operation) => operation.operationId), ["stt", "tts"]);
  for (const id of ["embeddings", "image"] as const) assert.equal(group(categories.find((category) => category.id === id)!, [])[0].id, "openai");
  assert.deepEqual(categories, before);
});

test("日英の音声案内はPortal代表12音声と公式一覧を明記し、API全体の上限と混同させない", () => {
  for (const locale of ["ja", "en"] as const) {
    const text = getSpeechDictionary(locale);
    assert.match(text.voiceHelp, /12/);
    assert.match(text.voiceHelp, locale === "ja" ? /代表的/ : /representative subset/);
    assert.ok(text.voiceListDocs);
    assert.doesNotMatch(text.voiceHelp, /7種|seven Japanese/);
  }
});
