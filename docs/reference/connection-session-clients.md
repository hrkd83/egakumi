# 本人接続v2のクライアント実装

購読キー、CMのtoken管理、SQLの本人接続は基盤側で共通化する。各アプリは自身のログイン利用者を検証し、本人専用の接続セッションをバックエンドへ保存する。この文書の「本人」は署名・issuer・audienceを検証済みの`tenantId + oid`であり、UPNやHTTPで申告されたユーザーIDではない。

## 全言語共通の保存・更新契約

保存キーは`Gateway origin + 購読ID + 論理プロファイル + 検証済みtid/oid`。購読キーを再生成しても同じ購読IDを使う。保存する値は次のとおり。

```text
revision                         保存版数（比較更新用）
status                           pending / complete / signed_out
current                          仮値または有効な接続セッション
sessionId / generation           接続セッションの識別子と世代
metadata                         無操作・最長期限・次回更新時刻
authentication                   pendingの間だけ、短命の取引・pollSecret
pending                          候補値・候補ID・元世代・候補期限
rotationCommitAttempted          確定要求を送る前に保存する試行済みフラグ
```

保存先は暗号化したバックエンド共有DBなどを使う。ブラウザーのlocalStorage、画面Cookie、Difyの共有環境変数へ本人セッションを置かない。アプリが複数台の場合は、分散ロックと版数の比較更新を使う。ロック期限を設ける方式では、期限を越えた古い処理が書き込めないfencingも必要。単なるプロセス内mutexでは複数インスタンスを保護できない。

処理は利用者単位で次の順序に統一する。

1. 保存済み候補の確定が未完了なら、業務要求より先にその確定を回復する。
2. 現行値を付けて業務APIを呼ぶ。認証が必要なら、仮値と取引の保存後に`loginUrl`だけを表示する。
3. statusをバックエンドから確認し、応答の本人をアプリ本人と比較する。statusから接続セッションを取り出そうとしない。
4. 更新候補を業務応答の専用ヘッダーから取得し、保存してから確定する。
5. 成功済み業務は更新失敗で再実行しない。世代を比較し、遅れた応答で巻き戻さない。

期限維持のタイマーは用意しない。statusや更新確定を呼んでも無操作期限は延長されない。候補・現行値・購読キー・上流の例外本文をログへ出さない。ブラウザーへ転送する業務応答から`X-Core-Connection-*`を除去する。

## Next.js / Node.js

サーバー用クライアントの正本は[`apps/portal/lib/connection-session/client.ts`](../../apps/portal/lib/connection-session/client.ts)。[`clients/connection-session/typescript/connection-session.ts`](../../clients/connection-session/typescript/connection-session.ts)は同じ実装を再exportする入口で、別管理のコピーではない。別アプリへ取り込むときは正本を使い、暗号化共有store・認証済み本人の境界をそのアプリへ実装する。追加の認証SDKは要求せず、アプリの既存認証から確認済み本人を受け取る。PortalのBetter Authログインと、この接続セッションの期限を同一にしない。

```typescript
// app/api/graph/me/route.ts の組み込み例。server-onlyのモジュール内で生成する。
import "server-only";
import { ConnectionSessionClient } from "./connection-session";

export async function GET() {
  // 以下3件は呼び出しアプリが実装する境界。HTTPのtid/oid申告で代用しない。
  const principal = await requireVerifiedPrincipal();
  const subscription = await authorizedSubscriptionFor(principal);
  const store = encryptedSharedConnectionStore;
  const client = new ConnectionSessionClient({
    origin: configuredGatewayOrigin,
    apiKey: subscription.primaryKey,
    subscriptionId: subscription.id,
    profile: "graph-delegated",
    principal: { tenantId: principal.tenantId, ownerId: principal.objectId },
    store,
  });
  const result = await client.get("/tools/graph/v1/me");
  if (result.kind === "login") {
    // 仮セッションの保存完了後。画面はURLを表示し、アプリ自身の完了確認APIを照会する。
    return Response.json({ status: "need_login", loginUrl: result.loginUrl },
      { status: 409, headers: { "Cache-Control": "no-store" } });
  }
  return result.response; // 更新秘密ヘッダーは参照実装が除去済み。
}
```

上記の`requireVerifiedPrincipal`・購読認可・暗号化共有storeはアプリの実装箇所であり、参照実装に架空の認証処理は含めない。`SharedStateStore.withExclusive()`内で同じ保存キーを排他し、`StateTransaction.save()`は期待revisionと一致するときだけ保存する。DBに暗号化・復号するのもアダプターの担当。

アプリ自身の完了確認Route Handlerでも同じ本人認証・購読認可を行い、同じbindingの`client.poll()`を呼ぶ。ブラウザーへは`pending/complete`だけを返す。ブラウザーにGatewayのpollSecretを送らない。完了後は通常のGraph取得を再要求する。書き込み操作の自動再送はアプリが冪等性を確認した場合だけ行う。

このTypeScript実装の業務応答は64KiBまでの有限なJSONを対象とし、ストリーミングには使用しない。応答本文を更新確定より先に受け取り、確定の通信待ちで成功済みの本文を失わないようにしている。

ログアウト・本人の全失効・接続解除には、それぞれ同じクライアントの`logout()`・`logoutAll()`・`disconnect()`を使用する。接続解除が202の`disconnect_pending`でもSQLでは利用禁止済みのため、アプリ側の秘密を破棄する。

更新候補を保存後に確定通信が失敗した場合、`sessionUpdatePending: true`と元の業務応答を返す。画面に再認証を要求せず、次回要求の先頭で確定を再試行する。

Portalの実装は`lib/connection-session/store.ts`と`crypto.ts`を使用し、`portalConnectionState`へ利用者別の接続状態をAES-256-GCMで保存する。保存先の鍵は`BETTER_AUTH_SECRET`から用途別に導出し、DBのリース・版数比較で排他する。署名鍵の更新後は旧暗号文を復号できないため、管理者がGraph保存状態を初期化してから再認証する運用が必要である。画面の再実行だけでは復旧せず、暗号文の不正は自動破棄しない。Microsoftアクセストークン・更新トークンは引き続きCMが管理する。

参照実装の実通信なしテストと型検査:

```bash
node --experimental-strip-types --test clients/connection-session/typescript/connection-session.test.ts
node apps/portal/node_modules/typescript/bin/tsc --noEmit --strict --target ES2022 \
  --module NodeNext --moduleResolution NodeNext --allowImportingTsExtensions \
  --types node --typeRoots apps/portal/node_modules/@types \
  clients/connection-session/typescript/connection-session.ts \
  clients/connection-session/typescript/connection-session.test.ts
```

Nodeの型消去はテスト実行用。本番アプリへ組み込む際は、そのアプリのTypeScriptビルドを使う。

## FastAPI / Python

参照実装は[`scripts/credential_client.py`](../../scripts/credential_client.py)。依存パスへ`scripts`を追加するか、アプリのサーバーモジュールとして同じファイルを配置する。標準ライブラリーだけを使用する。`MemorySessionStore`はテスト用、`FileSessionStore`はWSL/POSIXの単一ホスト診断用。本番では`locked/read/write(expected_revision=...)`を共有DBで実装する。

```python
from fastapi import Depends
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from credential_client import CredentialClient, LoginRequired

@app.get("/api/graph/me")
async def graph_me(principal=Depends(require_verified_principal)):
    # DBや認証へのアクセスは呼び出しアプリの既存実装を使用する。
    subscription = await authorized_subscription_for(principal)
    store = encrypted_shared_store_for(principal, subscription.id, "graph-delegated")
    client = CredentialClient(
        configured_gateway_origin, subscription.primary_key,
        subscription_id=subscription.id,
        expected_subject={"tenantId": principal.tenant_id, "ownerId": principal.object_id},
        store=store,
    )
    result = await run_in_threadpool(client.get_api, "/tools/graph/v1/me")
    if isinstance(result, LoginRequired):
        # continuationは秘密を保持する。オブジェクト全体をJSONへ変換しない。
        return JSONResponse({"status": "need_login", "loginUrl": result.login_url}, status_code=409,
                            headers={"Cache-Control": "no-store"})
    return result
```

`require_verified_principal`等はアプリの統合箇所であり、この例だけでは独立起動しない。認証取引を別HTTP要求で照会するときも、同じ検証済み本人からstoreを選び、そのstoreの`authentication`からtransactionId/pollSecretを取り出して`client.status()`へ渡す。HTTP引数で任意の本人や取引を指定させない。

Python参照実装は読み取りGET専用。初回認証後の`LoginRequired.resume()`は一度だけ再開する。成功済みGETの後のローテーション失敗は業務結果を返したまま`session_update_pending=True`とし、次回は確定要求だけを先に送る。

## PHP

PHPでもHTTP契約は同じ。フレームワークの検証済みログイン利用者からtid/oidを取得し、共有DBの利用者別ロック内で、暗号化した状態を比較更新する。セッションをPHPのプロセス内配列や単一サーバーの`$_SESSION`だけで共有管理しない。

以下は更新確定部分の組み込み例。`$save`は暗号化DBへの永続・比較更新、`$request`は固定Gateway originへのHTTPS専用呼び出しであり、リダイレクトを追わず本文サイズ・タイムアウトを制限するアプリ側の境界である。参照用のHTTP疑似実装を本番認証として提供しない。

```php
/** 候補保存済みの状態を受け取り、更新確定だけを送る。業務APIは再実行しない。 */
function commitConnectionCandidate(array $state, callable $save, callable $request): array {
    $pending = $state['pending'];
    $retry = $state['rotationCommitAttempted'] ?? false;
    if (!$retry) {
        $state['rotationCommitAttempted'] = true;
        $state = $save($state); // 永続保存とrevisionのCASが成功してから通信する。
    }
    $body = [
        'rotationId' => $pending['rotationId'],
        'candidateSession' => $pending['candidateSession'],
        'fromGeneration' => $pending['fromGeneration'],
    ];
    $proof = $retry ? $pending['candidateSession'] : $state['current'];
    $response = $request('POST', '/auth/connections/session/commit', $proof, $body);
    if ($retry && $response['status'] === 403
        && ($response['json']['error']['code'] ?? '') === 'ConnectionNotAuthorized') {
        $response = $request('POST', '/auth/connections/session/commit', $state['current'], $body);
    }
    if ($response['status'] === 409
        && ($response['json']['error']['code'] ?? '') === 'SessionRotationExpired') {
        unset($state['pending'], $state['rotationCommitAttempted']);
        return $save($state); // 現行値を保持。次の業務要求で新しい候補を取得する。
    }
    if ($response['status'] !== 200) {
        // Supersededなら共有DBを再読。他の失敗も秘密をログへ出さず候補を保持する。
        throw new RuntimeException('ConnectionSessionCommitFailed');
    }
    $meta = $response['json'];
    if ($meta['protocolVersion'] !== 2 || $meta['sessionId'] !== $state['sessionId']
        || $meta['generation'] !== $pending['generation']
        || $meta['absoluteExpiresAt'] !== $state['metadata']['absoluteExpiresAt']) {
        throw new RuntimeException('InvalidConnectionSessionMetadata');
    }
    $state['current'] = $pending['candidateSession'];
    $state['generation'] = $meta['generation'];
    $state['metadata'] = $meta;
    unset($state['pending'], $state['rotationCommitAttempted']);
    return $save($state);
}
```

この断片だけを汎用SDKとして公開せず、初回409の検証、本人照合、ヘッダーの型・世代・セッションID確認、版数比較をアプリの共通HTTP層へ実装する。TypeScript/Pythonと同じ異常系をテストする。PHP実行環境がない開発端末では、この断片の実行確認まで完了したとは扱わない。

## Dify

既製のHTTPノードへ本人セッションを固定登録する構成は使用しない。次のどちらかに共通接続管理を組み込む。

- 認証済み利用者を検証できる中継BFFを設け、DifyのToolはそのAPIを呼ぶ。
- カスタムTool/プラグインが、信頼できる利用者コンテキストとバックエンド共有storeを使って同じ契約を扱う。

Difyの任意入力`user`、ワークフロー引数、UPN文字列をEntra本人の証明にしない。SSO連携等で確認済みEntra本人へ結び付けられない構成では、個人Graph接続を選べない。購読キーだけではエンドユーザーを確定できない。

認証が必要なとき、Toolは画面へ`loginUrl`と「認証後に再実行」の案内だけを返す。対応する独自フロントがある場合は、そのフロントがアプリ自身の完了確認APIを呼ぶ。pollSecret・仮セッション・更新候補をTool出力やトレースへ含めない。自動再開できるかどうかはDify側のTool/表示実装の責務であり、APIMが画面を強制操作する仕組みではない。

バックグラウンド実行のワークフローで無操作期限を維持するための呼び出しを行わない。日次更新はユーザー操作に伴う業務要求の応答で処理する。

## 共通エラーと確認先

| 状態 | クライアントの処理 |
| --- | --- |
| 409`ConnectionRequired` | 仮値保存→認証URL表示→バックエンド完了確認→本人照合 |
| 409`SessionUpdateRequired` | 保存・確定後に元要求を一度再試行。業務実行前の拒否だけが対象 |
| 409`SessionRotationExpired` | 候補だけを捨て、現行値で次の業務要求へ進む |
| 409`SessionRotationSuperseded` | 共有保存先の新世代を読み直す。再認証にしない |
| 403`ConnectionClaimsRequired` | 条件付きアクセス要求を無視せず利用停止。CMで扱える範囲を確認し、自動認証ループにしない |
| 401/403、503等 | 認可失敗・障害を識別し、無条件にログインへ誘導しない |

公開の契約は [本人接続手順](../setup/credential-manager-login.md)、実APIM/CMを使う受入条件・結果は [動作テスト仕様書](../../INTEGRATION_TESTS.md)を参照する。PortalログアウトとGraph接続の失効操作を結び付ける範囲は実装と試験で区別する。
