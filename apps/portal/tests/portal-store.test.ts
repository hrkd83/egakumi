import assert from "node:assert/strict";
import { test } from "node:test";
import { createPortalStore } from "../lib/portal-store.ts";
import { resolveTextPanelInput, textModelDraft, updateTextModelSelection } from "../lib/text-model-choices.ts";
import { responsesModel } from "./helpers/model-fixtures.ts";
import { createTextPanelStore, getTextPanelView } from "../lib/text-panel-state.ts";

test("提供元の初回はカタログ最大値、同じ提供元へ戻るとモデル・API方式・編集値が揃う", () => {
  const store = createPortalStore();
  const first = { ...responsesModel, maxOutputTokens: 128_000 };
  const chat = { ...first, operationId: "chat-completions" as const };
  const other = { ...first, id: "synthetic-other-provider", providerId: "deepseek" as const, operationId: "chat-completions" as const, maxOutputTokens: 384_000 };
  const firstScope = "text/openai", otherScope = "text/deepseek";
  const initial = resolveTextPanelInput([first, chat], undefined, store.getState().textInput);
  assert.equal(initial.maxOutputTokens, 128_000);
  store.getState().setTextModelDraft(firstScope, textModelDraft({ ...updateTextModelSelection(initial, chat), maxOutputTokens: 16 }));
  store.getState().updateTextInput({ message: "同じ合成文で比較", stream: true });
  const otherInput = resolveTextPanelInput([other], store.getState().textModelDrafts[otherScope], store.getState().textInput);
  assert.equal(otherInput.maxOutputTokens, 384_000);
  const returned = resolveTextPanelInput([first, chat], store.getState().textModelDrafts[firstScope], store.getState().textInput);
  assert.equal(returned.modelId, first.id); assert.equal(returned.operationId, "chat-completions"); assert.equal(returned.maxOutputTokens, 16);
  assert.equal(returned.message, "同じ合成文で比較"); assert.equal(returned.stream, true);
  // 画面のlocal storeを破棄しても、root memoryのdraftから同じ設定へ復元できます。
  assert.deepEqual(resolveTextPanelInput([first, chat], store.getState().textModelDrafts[firstScope], store.getState().textInput), returned);
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
  assert.equal("persist" in first, false); assert.deepEqual(second.getState().textModelDrafts, {});
});

test("提供元の結果・キーIDを別パネルへ共有せず、設定変更でそのパネルの結果だけ消す", () => {
  const first = createTextPanelStore(), second = createTextPanelStore();
  first.getState().setModelApiKeyId("synthetic-key-id");
  first.getState().completeGeneration({ text: "合成応答", model: "synthetic-model", durationMs: 1, rawResponse: {} });
  assert.equal(second.getState().modelApiKeyId, ""); assert.equal(second.getState().generationResult, null);
  first.getState().resetGeneration(); assert.equal(first.getState().generationStatus, "idle"); assert.equal(first.getState().generationResult, null);
  first.getState().failGeneration({ error: { code: "InvalidRequest" } }); first.getState().resetGeneration();
  assert.equal(first.getState().generationFailure, null);
});


test("Aの完了後にBが共有入力を編集したら、Aの選択を保持して古い結果を即座に隠す", () => {
  const root = createPortalStore(), panel = createTextPanelStore();
  const chat = { ...responsesModel, operationId: "chat-completions" as const, maxOutputTokens: 128_000 };
  const scope = "text/openai";
  root.getState().setTextModelDraft(scope, { modelId: chat.id, operationId: chat.operationId, maxOutputTokens: 16 });
  const original = resolveTextPanelInput([chat], root.getState().textModelDrafts[scope], root.getState().textInput);
  panel.getState().beginGeneration(original);
  panel.getState().completeGeneration({ text: "合成応答", model: "synthetic-model", durationMs: 1, rawResponse: {} });
  assert.equal(getTextPanelView(panel.getState(), original).generationStatus, "complete");
  // AはActivityで保持されたまま、Bが比較用の共有入力を編集します。
  root.getState().updateTextInput({ message: "Bで変更した合成文", stream: !original.stream });
  const returned = resolveTextPanelInput([chat], root.getState().textModelDrafts[scope], root.getState().textInput);
  assert.equal(returned.operationId, "chat-completions"); assert.equal(returned.maxOutputTokens, 16);
  assert.equal(getTextPanelView(panel.getState(), returned).generationResult, null);
  assert.equal(getTextPanelView(panel.getState(), returned).generationStatus, "idle");
  // 復帰時effectは結果を破棄します。同一入力の再確認はstoreを更新しません。
  panel.getState().reconcileInput(returned);
  assert.equal(panel.getState().generationResult, null);
  const settled = panel.getState(); panel.getState().reconcileInput({ ...returned }); assert.equal(panel.getState(), settled);
});

test("pendingな部分応答は非表示化で消え、中止の状態をReactのcleanup後にも保持する", async () => {
  const { deactivatePanelRequest } = await import("../lib/panel-request-lifecycle.ts");
  const panel = createTextPanelStore();
  const input = resolveTextPanelInput([responsesModel], undefined, createPortalStore().getState().textInput);
  panel.getState().beginGeneration(input);
  panel.getState().updateGeneration({ text: "合成の部分応答", model: "synthetic-model", durationMs: 1, rawResponse: {} });
  const reference = { current: new AbortController() as AbortController | null }, controller = reference.current!;
  deactivatePanelRequest(reference, (pending) => { if (pending) panel.getState().cancelGeneration(); });
  assert.equal(controller.signal.aborted, true); assert.equal(reference.current, null);
  const returned = getTextPanelView(panel.getState(), input);
  assert.equal(returned.generationStatus, "idle"); assert.equal(returned.generationResult, null); assert.equal(returned.generationCancelled, true);
  panel.getState().reconcileInput(input); assert.equal(panel.getState().generationCancelled, true);
  panel.getState().beginGeneration(input); assert.equal(panel.getState().generationCancelled, false);
});
