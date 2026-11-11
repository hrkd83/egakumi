import assert from "node:assert/strict";
import { test } from "node:test";
import { appendRealtimeTranscript, joinRealtimeTranscriptionParagraph, type RealtimeTurn } from "../lib/realtime-client.ts";
import { RealtimeProtocol } from "../lib/realtime-protocol.ts";
import { realtimeStreamEventSchema } from "../lib/realtime-request.ts";
import type { RealtimeModel } from "../lib/model-types.ts";

const model: RealtimeModel = { id: "synthetic-transcription", label: "Synthetic transcription", family: "transcription", providerId: "openai", provider: "OpenAI", modelId: "gpt-live-transcribe", apiId: "openai-transcription-ws-v1", operationId: "invoke", path: "/models/openai-transcription/v1/ws/", maxRequestBytes: 65536, maxOutputTokens: null, transport: "websocket", voice: "" };

/** 実プロトコルと表示stateを通し、ネットワークを使わず字幕の到着順だけを制御します。 */
function fixture() {
  const protocol = new RealtimeProtocol(model); let turns: RealtimeTurn[] = []; let elapsed = 0;
  protocol.accept({ type: "session.updated", session: {} }, elapsed);
  return { protocol, turns: () => turns, emit(event: Record<string, unknown>) {
    const values = protocol.accept(event, ++elapsed);
    for (const value of values) {
      realtimeStreamEventSchema.parse(value);
      if (value.type === "transcript") { assert.equal(JSON.stringify(value).includes("native-synthetic"), false); turns = appendRealtimeTranscript(turns, value); }
    }
    return values;
  } };
}

test("文字起こしのdeltaと逆順finalをcommit順の同じ区間へ反映し、native IDを画面へ渡さない", () => {
  const f = fixture();
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-first" });
  f.emit({ type: "conversation.item.input_audio_transcription.delta", item_id: "native-synthetic-first", content_index: 0, delta: "Hello" });
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-second" });
  f.emit({ type: "conversation.item.input_audio_transcription.delta", item_id: "native-synthetic-second", content_index: 0, delta: " world" });
  assert.deepEqual(f.turns().map(({ segmentIndex, text }) => ({ segmentIndex, text })), [{ segmentIndex: 0, text: "Hello" }, { segmentIndex: 1, text: " world" }]);
  f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-second", content_index: 0, transcript: " world!" });
  f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-first", content_index: 0, transcript: "Hello" });
  assert.equal(joinRealtimeTranscriptionParagraph(f.turns()), "Hello world!");
  assert.ok(f.turns().every((turn) => turn.final));
  assert.throws(() => f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-first", content_index: 0, transcript: "replayed" }));
});

test("commit前のdeltaとfinalも一つの区間へ束縛し、別区間を上書きしない", () => {
  const f = fixture();
  for (const event of [{ type: "conversation.item.input_audio_transcription.delta", delta: "部分" }, { type: "conversation.item.input_audio_transcription.completed", transcript: "確定した文章" }]) {
    assert.equal(f.emit({ ...event, item_id: "native-synthetic-early", content_index: 0 }).some((value) => value.type === "transcript"), false);
  }
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-early" });
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-next" });
  f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-next", content_index: 0, transcript: "。次の文章です。" });
  assert.equal(joinRealtimeTranscriptionParagraph(f.turns()), "確定した文章。次の文章です。");
  assert.equal(f.turns().length, 2);
});

test("実プロトコルの先頭空白deltaと空白だけの確定区間を表示境界でも保持する", () => {
  const f = fixture();
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-leading" });
  for (const delta of [" ", "world"]) f.emit({ type: "conversation.item.input_audio_transcription.delta", item_id: "native-synthetic-leading", content_index: 0, delta });
  f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-leading", content_index: 0, transcript: " world" });
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-space" });
  f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-space", content_index: 0, transcript: " " });
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-word" });
  f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-word", content_index: 0, transcript: "next" });
  assert.deepEqual(f.turns().map((turn) => turn.text), [" world", " ", "next"]);
  assert.equal(joinRealtimeTranscriptionParagraph(f.turns()), " world next");
});

test("字幕区間番号は入力の有限整数だけで、生のitemIdや任意フィールドを受け付けない", () => {
  const base = { type: "transcript", role: "input", text: "独立した合成字幕", final: true };
  assert.ok(realtimeStreamEventSchema.safeParse({ ...base, segmentIndex: 0 }).success);
  assert.ok(realtimeStreamEventSchema.safeParse({ ...base, segmentIndex: 3000 }).success);
  for (const segmentIndex of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1, Infinity, true, "1"]) assert.equal(realtimeStreamEventSchema.safeParse({ ...base, segmentIndex }).success, false);
  assert.equal(realtimeStreamEventSchema.safeParse({ ...base, role: "output", segmentIndex: 0 }).success, false);
  assert.equal(realtimeStreamEventSchema.safeParse({ ...base, item_id: "native-synthetic-private" }).success, false);
});

test("3000区間を超えても順序・全履歴を保ち、完了済みitemの再利用は拒否する", () => {
  const f = fixture();
  for (let index = 0; index < 3002; index++) {
    f.emit({ type: "input_audio_buffer.committed", item_id: `native-synthetic-${index}` });
    f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: `native-synthetic-${index}`, content_index: 0, transcript: "x" });
  }
  assert.equal(f.turns().length, 3002);
  assert.equal(f.turns().at(-1)?.segmentIndex, 3001);
  assert.equal(joinRealtimeTranscriptionParagraph(f.turns()), "x".repeat(3002));
  assert.throws(() => f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-0" }));
  assert.throws(() => f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-3001", content_index: 0, transcript: "replayed" }));
});

test("16000文字を超える入力・出力字幕を単体と累積deltaの両方で保持する", () => {
  const long = "合成🙂".repeat(5000), f = fixture();
  f.emit({ type: "input_audio_buffer.committed", item_id: "native-synthetic-long" });
  f.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "native-synthetic-long", content_index: 0, transcript: long });
  assert.equal(joinRealtimeTranscriptionParagraph(f.turns()), long);
  const protocol = new RealtimeProtocol({ ...model, family: "realtime" });
  protocol.accept({ type: "session.updated", session: {} }, 0);
  protocol.accept({ type: "response.created" }, 1);
  let turns: RealtimeTurn[] = [];
  for (const event of [
    { type: "response.output_audio_transcript.delta", delta: "a".repeat(10_000) },
    { type: "response.output_audio_transcript.delta", delta: "b".repeat(10_000) },
    { type: "response.output_audio_transcript.done", transcript: long },
  ]) for (const value of protocol.accept(event, 2)) if (value.type === "transcript") {
    realtimeStreamEventSchema.parse(value); turns = appendRealtimeTranscript(turns, value);
    assert.equal(turns[0].text, event.transcript ?? (event.delta!.startsWith("a") ? event.delta : "a".repeat(10_000) + event.delta));
  }
  assert.equal(turns[0].text, long);
});

test("文字起こしの表示だけを一段落へ結合し、日本語・英語・明示空白・引用や絵文字を保持する", () => {
  const segments = ["こんにちは", "。", " 次は ", "Hello", " world", "!", " Goodbye", " 🙂"];
  const turns = segments.map((text, segmentIndex): RealtimeTurn => ({ id: segmentIndex + 1, role: "input", text, final: true, segmentIndex })).reverse();
  const before = structuredClone(turns);
  assert.equal(joinRealtimeTranscriptionParagraph(turns), "こんにちは。 次は Hello world! Goodbye 🙂");
  assert.deepEqual(turns, before);
  assert.equal(joinRealtimeTranscriptionParagraph([{ id: 1, role: "output", text: "混ぜない応答", final: true }]), "");
  assert.equal(joinRealtimeTranscriptionParagraph([{ id: 1, role: "input", text: " \n", final: true }]), "");
  assert.equal(joinRealtimeTranscriptionParagraph(["日", " ", "本"].map((text, id) => ({ id, role: "input", text, final: true }))), "日 本");
  assert.equal(joinRealtimeTranscriptionParagraph(["hel", "lo"].map((text, id) => ({ id, role: "input", text, final: true }))), "hello");
});

test("空finalは本文を消さず、100区間・64000文字を超えても履歴やUnicodeを切り捨てない", () => {
  let turns = appendRealtimeTranscript([], { type: "transcript", role: "input", text: "認識できた文章", final: false, segmentIndex: 0 });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "", final: true, segmentIndex: 0 });
  assert.equal(turns[0].text, "認識できた文章");
  for (let segmentIndex = 1; segmentIndex < 200; segmentIndex++) turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "x".repeat(900), final: true, segmentIndex });
  assert.equal(turns.length, 200);
  assert.equal(joinRealtimeTranscriptionParagraph(turns), "認識できた文章" + "x".repeat(199 * 900));
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "遅すぎた古い区間", final: true, segmentIndex: 0 });
  assert.equal(turns[0].text, "遅すぎた古い区間");
  const unicode = appendRealtimeTranscript([], { type: "transcript", role: "input", text: "🙂" + "a".repeat(15999), final: true, segmentIndex: 0 });
  assert.equal(unicode[0].text, "🙂" + "a".repeat(15999));
  assert.equal(joinRealtimeTranscriptionParagraph([{ id: 1, role: "input", text: "🙂" + "a".repeat(63999), final: true }]), "🙂" + "a".repeat(63999));
});
