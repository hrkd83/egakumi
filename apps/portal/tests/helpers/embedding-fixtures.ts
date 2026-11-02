import type { EmbeddingModel } from "../../lib/model-types.ts";

/** 通信を行わない検証用の公開モデル情報です。秘密値は含みません。 */
export const smallEmbedding: EmbeddingModel = { id: "text-embedding-3-small", label: "text-embedding-3-small", providerId: "openai", provider: "OpenAI", apiId: "openai-embeddings-v1", operationId: "embeddings", path: "/models/openai-embeddings/v1/embeddings", maxRequestBytes: 262_144, maxInputs: 16, inputTypes: [] };
/** large固有の既定次元と上限を検証するモデルです。 */
export const largeEmbedding: EmbeddingModel = { ...smallEmbedding, id: "text-embedding-3-large", label: "text-embedding-3-large" };
/** 実際のAzure配備名とAPI制約に合わせたCohereモデルです。 */
export const cohereEmbedding: EmbeddingModel = { id: "embed-v-4-0", label: "embed-v-4-0", providerId: "cohere", provider: "Cohere", apiId: "cohere-inference-v1", operationId: "cohere-embeddings", path: "/models/cohere/v1/embed", maxRequestBytes: 262_144, maxInputs: 16, inputTypes: ["search_document", "search_query", "classification", "clustering"] };
