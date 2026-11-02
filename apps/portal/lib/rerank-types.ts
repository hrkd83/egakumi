/** 公開済みのリランク操作と、そのGateway制約を表します。 */
export type RerankModel = {
  id: string;
  label: string;
  providerId: "cohere";
  provider: "Cohere";
  apiId: "cohere-inference-v1";
  operationId: "rerank";
  path: string;
  maxRequestBytes: number;
  maxDocuments: number;
  minTopN: number;
  maxTopN: number;
};
