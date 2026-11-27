import type { TryCredential } from "./api-auth/try-credential.ts";
import type { SpeechModel } from "./model-types.ts";
import type { SpeechInput } from "./speech-request.ts";

/** TTSはJSON、STTは選択ファイルのmultipartとして送り、形式に合わないContent-Typeを付けません。 */
export function buildSpeechExecutionRequest(model: SpeechModel, input: SpeechInput, credential: string | TryCredential, file: File | null): RequestInit {
  const value = { ...input, ...(typeof credential === "string" ? { apiKeyId: credential } : credential) };
  if (model.kind === "tts") return { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) };
  if (!file) throw new Error("Audio file is required");
  const body = new FormData(); body.append("input", JSON.stringify(value)); body.append("file", file);
  return { method: "POST", credentials: "same-origin", cache: "no-store", body };
}
