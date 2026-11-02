import assert from "node:assert/strict";
import { test } from "node:test";
import { getEmbeddingModels, getImageGenerationModels, getRealtimeModels, getSpeechModels, getTextGenerationModels } from "../lib/model-catalog.ts";
import { modelSelectionScope, resolveInitialModel } from "../lib/model-selection.ts";
import { resolveTextModelVariant } from "../lib/text-model-choices.ts";
import { createPortalStore } from "../lib/portal-store.ts";

test("初回は公開カタログの最新世代を選び、古い固定nanoや名前の辞書順で決めない", () => {
  const text = getTextGenerationModels().filter((model) => model.providerId === "openai");
  const initial = resolveTextModelVariant(text, createPortalStore().getState().textInput);
  assert.equal(initial?.id, "gpt-6.1-sol");
  assert.equal(initial?.operationId, "responses-create");
  assert.equal(resolveInitialModel([{ id: "z-latest" }, { id: "a-earlier" }])?.id, "z-latest");
});

test("各カテゴリは正本順を維持し、単一モデルや同世代の種類に架空の世代を付けない", () => {
  assert.equal(resolveInitialModel(getEmbeddingModels().filter((model) => model.providerId === "openai"))?.id, "text-embedding-3-small");
  assert.equal(resolveInitialModel(getEmbeddingModels().filter((model) => model.providerId === "cohere"))?.id, "embed-v-4-0");
  assert.equal(resolveInitialModel(getImageGenerationModels().filter((model) => model.providerId === "openai"))?.id, "gpt-image-2.5-flare");
  assert.equal(resolveInitialModel(getImageGenerationModels().filter((model) => model.providerId === "black-forest-labs"))?.id, "FLUX.2-flex");
  assert.equal(resolveInitialModel(getSpeechModels().filter((model) => model.providerId === "openai"))?.id, "gpt-transcribe");
  assert.equal(resolveInitialModel(getRealtimeModels().filter((model) => model.providerId === "openai"))?.modelId, "gpt-realtime-2.1");
  assert.equal(resolveInitialModel(getRealtimeModels().filter((model) => model.providerId === "azure-ai-speech"))?.modelId, "gpt-6.1-sol");
});

test("明示選択はカテゴリ・提供元別のメモリーに保持し、カタログから消えた場合だけ先頭へ戻す", () => {
  const store = createPortalStore();
  const scope = modelSelectionScope("image", "openai");
  const selection = { id: "gpt-image-2" };
  store.getState().rememberModelSelection(scope, selection);
  selection.id = "external-mutation";
  store.getState().rememberModelSelection(modelSelectionScope("text", "openai"), { id: "gpt-5.4-nano", operationId: "chat-completions" });
  const models = getImageGenerationModels().filter((model) => model.providerId === "openai");
  assert.equal(resolveInitialModel(models, store.getState().modelSelections[scope])?.id, "gpt-image-2");
  assert.equal(resolveInitialModel(models, { id: "removed-model" })?.id, "gpt-image-2.5-flare");
  assert.deepEqual(createPortalStore().getState().modelSelections, {});
  assert.equal(resolveInitialModel([], { id: "missing" }), undefined);
});

test("廃止した5.4系・5.5の選択を引き継いでも現在の先頭モデルと対応方式へ戻す", () => {
  const models = getTextGenerationModels().filter((model) => model.providerId === "openai");
  for (const modelId of ["gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.5"]) {
    const selected = resolveTextModelVariant(models, { modelId, operationId: "chat-completions" });
    assert.equal(selected?.id, "gpt-6.1-sol");
    assert.equal(selected?.operationId, "responses-create");
  }
});
