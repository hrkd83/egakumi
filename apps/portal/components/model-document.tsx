"use client";

import { TextGenerationPanel } from "@/components/text-generation-panel";
import { EmbeddingPanel } from "@/components/embedding-panel";
import { ImageGenerationPanel } from "@/components/image-generation-panel";
import { SpeechPanel } from "@/components/speech-panel";
import { RealtimeAudioPanel } from "@/components/realtime-audio-panel";
import { RerankPanel } from "@/components/rerank-panel";
import { DecisionPanel } from "@/components/decision-panel";
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

/** 機能別の専用ページにガイド・仕様・試用を表示し、提供元・音声シリーズの切替で旧パネルを解放します。 */
export function ModelDocument({ category, models, embeddingModels, imageModels, speechModels, realtimeModels, rerankModels, decisionModels, operations, dictionary, locale, gatewayOrigin }: ModelDocumentProps) {
  const common = { dictionary, locale, gatewayOrigin };
  if (category === "text") return <TextGenerationPanel key={models[0]?.providerId ?? "text"} models={models} {...common} />;
  if (category === "embeddings") return <EmbeddingPanel key={embeddingModels[0]?.providerId ?? "embeddings"} models={embeddingModels} {...common} />;
  if (category === "image") return <ImageGenerationPanel key={imageModels[0]?.providerId ?? "image"} models={imageModels} {...common} />;
  if (category === "speech") return <SpeechPanel key={speechModels[0]?.providerId ?? "speech"} models={speechModels} {...common} />;
  if (category === "realtime") return <RealtimeAudioPanel key={realtimeModels[0]?.family ?? "realtime"} models={realtimeModels} {...common} />;
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
