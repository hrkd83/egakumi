"use client";

import { useEffect, useRef, useState } from "react";
import { TextGenerationPanel } from "@/components/text-generation-panel";
import { EmbeddingPanel } from "@/components/embedding-panel";
import { ImageGenerationPanel } from "@/components/image-generation-panel";
import { SpeechPanel } from "@/components/speech-panel";
import { RealtimeAudioPanel } from "@/components/realtime-audio-panel";
import { RerankPanel } from "@/components/rerank-panel";
import { DecisionPanel } from "@/components/decision-panel";
import { usePortalStore } from "@/components/portal-store-provider";
import { useModelSelection } from "@/components/use-model-selection";
import { resolveTextModelVariant, updateTextModelSelection } from "@/lib/text-model-choices";
import type { EmbeddingModel, ImageGenerationModel, ModelCategoryId, ModelOperation, RealtimeModel, SpeechModel, TextGenerationModel } from "@/lib/model-types";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import type { RerankModel } from "@/lib/rerank-types";
import type { DecisionModel } from "@/lib/decision-types";

/** 詳細ページへ渡す公開カタログです。秘密キーや内部の接続先は含みません。 */
export type ModelDocumentProps = {
  category: ModelCategoryId;
  models: TextGenerationModel[];
  embeddingModels: EmbeddingModel[];
  imageModels: ImageGenerationModel[];
  speechModels: SpeechModel[];
  realtimeModels: RealtimeModel[];
  rerankModels: RerankModel[];
  decisionModels: DecisionModel[];
  operations: ModelOperation[];
  dictionary: Dictionary;
  locale: Locale;
  gatewayOrigin: string | null;
};

/** 初回表示だけカタログの最大値を設定し、編集中の本文・Stream・その後の数値変更を保持します。 */
function TextDocument({ models, dictionary, locale, gatewayOrigin }: Pick<ModelDocumentProps, "models" | "dictionary" | "locale" | "gatewayOrigin">) {
  const input = usePortalStore((state) => state.textInput);
  const { savedSelection } = useModelSelection("text", models);
  const updateInput = usePortalStore((state) => state.updateTextInput);
  const selectApiKey = usePortalStore((state) => state.setModelApiKeyId);
  const initialized = useRef(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const next = resolveTextModelVariant(models, { modelId: savedSelection?.id ?? "", operationId: savedSelection?.operationId as typeof input.operationId });
    if (next) {
      const previous = models.find((model) => model.id === input.modelId && model.operationId === input.operationId);
      if (previous?.apiId !== next.apiId) selectApiKey("");
      if (previous !== next || input.maxOutputTokens === undefined && next.maxOutputTokens !== null) {
        updateInput(updateTextModelSelection(input, next));
      }
    }
    setReady(true);
  }, [models, input, savedSelection, selectApiKey, updateInput]);

  // 初期化前の未設定上限を入力エラーとして見せないよう、短い待機表示を挟みます。
  if (!ready) return <p role="status" aria-busy="true" className="py-6 text-sm text-muted-foreground">{locale === "ja" ? "ガイドを準備しています…" : "Preparing the guide…"}</p>;
  return <TextGenerationPanel models={models} dictionary={dictionary} locale={locale} gatewayOrigin={gatewayOrigin} />;
}

/** 機能別の専用ページに既存のガイド・仕様・試用を表示します。提供元の切替では旧パネルを解放します。 */
export function ModelDocument({ category, models, embeddingModels, imageModels, speechModels, realtimeModels, rerankModels, decisionModels, operations, dictionary, locale, gatewayOrigin }: ModelDocumentProps) {
  const common = { dictionary, locale, gatewayOrigin };
  if (category === "text") return <TextDocument key={models[0]?.providerId ?? "text"} models={models} {...common} />;
  if (category === "embeddings") return <EmbeddingPanel key={embeddingModels[0]?.providerId ?? "embeddings"} models={embeddingModels} {...common} />;
  if (category === "image") return <ImageGenerationPanel key={imageModels[0]?.providerId ?? "image"} models={imageModels} {...common} />;
  if (category === "speech") return <SpeechPanel key={speechModels[0]?.providerId ?? "speech"} models={speechModels} {...common} />;
  if (category === "realtime") return <RealtimeAudioPanel key={realtimeModels[0]?.providerId ?? "realtime"} models={realtimeModels} {...common} />;
  if (category === "rerank") return <RerankPanel models={rerankModels} {...common} />;
  if (category === "decision") return <DecisionPanel models={decisionModels} {...common} />;
  return <div className="space-y-6">
    <p className="rounded-xl border border-border bg-white p-5 text-sm leading-7 text-muted-foreground">{dictionary.modelGateway.notYet}</p>
    {operations.map((operation) => <section key={`${operation.apiId}/${operation.operationId}`} className="space-y-4 rounded-xl border border-border bg-white p-5 sm:p-6">
      <h2 className="text-lg font-semibold">{operation.name}</h2>
      <p className="break-all font-mono text-sm leading-7"><span className="mr-3 font-semibold text-primary">{operation.method}</span>{operation.path}</p>
      {operation.models.length > 0 && <div className="flex flex-wrap gap-2">{operation.models.map((model) => <span key={model} className="rounded-md bg-muted px-3 py-1.5 font-mono text-sm">{model}</span>)}</div>}
    </section>)}
  </div>;
}
