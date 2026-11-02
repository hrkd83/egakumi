import assert from "node:assert/strict";
import { test } from "node:test";
import { createPortalStore } from "../lib/portal-store.ts";
import { updateTextModelSelection } from "../lib/text-model-choices.ts";
import { responsesModel } from "./helpers/model-fixtures.ts";

test("初回のカード表示はカタログの最大値を使い、手入力した値は同じモデル内では維持する", () => {
  const store = createPortalStore();
  const model = { ...responsesModel, maxOutputTokens: 128_000 };
  assert.equal(store.getState().textInput.maxOutputTokens, undefined);
  assert.equal(store.getState().textInput.modelId, "");
  store.getState().updateTextInput(updateTextModelSelection(store.getState().textInput, model));
  assert.equal(store.getState().textInput.maxOutputTokens, 128_000);
  store.getState().updateTextInput({ maxOutputTokens: 2048, message: "自分で調整した設定" });
  store.getState().setIntegrationLanguage("python");
  assert.equal(store.getState().textInput.maxOutputTokens, 2048);
});

test("カードを閉じて提供元とカテゴリを変えても、比較に使う入力だけを保持する", () => {
  const store = createPortalStore(); const file = new File(["fixture"], "comparison.wav", { type: "audio/wav" });
  store.getState().updateModelDrafts({ embeddingTexts: ["比較する文章", "もう一件"], imagePrompt: "青いロボット", speechText: "比較のために読み上げます", speechFile: file });
  store.getState().setActiveProvider("openai"); store.getState().setActiveProvider(null); store.getState().setActiveProvider("cohere");
  store.getState().setModelCategory("image"); store.getState().setActiveProvider("black-forest-labs");
  assert.deepEqual(store.getState().modelDrafts, { embeddingTexts: ["比較する文章", "もう一件"], imagePrompt: "青いロボット", speechText: "比較のために読み上げます", speechFile: file });
  assert.deepEqual(Object.keys(store.getState().modelDrafts).sort(), ["embeddingTexts", "imagePrompt", "speechFile", "speechText"]);
});

test("編集元の配列や別項目の更新で、保存した他の入力を変更しない", () => {
  const store = createPortalStore(); const texts = ["元の文章"];
  store.getState().updateModelDrafts({ embeddingTexts: texts, imagePrompt: "画像入力" }); texts[0] = "外側で変更";
  store.getState().updateModelDrafts({ speechText: "音声入力" });
  assert.deepEqual(store.getState().modelDrafts.embeddingTexts, ["元の文章"]); assert.equal(store.getState().modelDrafts.imagePrompt, "画像入力");
  store.getState().updateModelDrafts({ imagePrompt: "", speechFile: null }); assert.equal(store.getState().modelDrafts.imagePrompt, "");
});

test("新しいページ・ログインのstoreへ文章やファイルを共有せず、永続化しない", () => {
  const first = createPortalStore(), second = createPortalStore();
  first.getState().updateModelDrafts({ speechFile: new File(["fixture"], "private.wav"), embeddingTexts: ["private text"] });
  assert.deepEqual(second.getState().modelDrafts, { embeddingTexts: null, imagePrompt: null, speechText: null, speechFile: null });
  assert.equal("persist" in first, false); assert.equal(second.getState().modelApiKeyId, "");
});

test("API方式だけを変えても以前の結果を消し、同じメッセージとStreamを比較に使う", () => {
  const store = createPortalStore();
  assert.equal(store.getState().textInput.operationId, "responses-create");
  store.getState().updateTextInput({ message: "同じ問いで比較", stream: true });
  store.getState().completeGeneration({ text: "古い結果", model: "gpt-5.4-nano", durationMs: 1, rawResponse: {} });
  store.getState().updateTextInput({ operationId: "chat-completions" });
  assert.equal(store.getState().generationStatus, "idle"); assert.equal(store.getState().generationResult, null);
  assert.equal(store.getState().textInput.message, "同じ問いで比較"); assert.equal(store.getState().textInput.stream, true);
  store.getState().failGeneration({ error: { code: "InvalidRequest" } });
  store.getState().updateTextInput({ operationId: "responses-create" });
  assert.equal(store.getState().generationFailure, null);
});
