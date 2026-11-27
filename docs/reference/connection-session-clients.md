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

購読キーで本人接続を使うクライアントは、このページの開始・完了確認・更新・失効の契約をバックエンドに実装します。PortalはBearerを使うため、購読キー向けのTypeScript参照実装は現在のPortal資材に含まれません。Gateway.Invokeを利用できるアプリは、OAuthで取得した本人のトークンを送信してGraphのOBO経路を利用できます。

## FastAPI / Python

参照実装は[`scripts/clients/credential_client.py`](../../scripts/clients/credential_client.py)。依存パスへ`scripts`を追加するか、アプリのサーバーモジュールとして同じファイルを配置する。標準ライブラリーだけを使用する。`MemorySessionStore`はテスト用、`FileSessionStore`はWSL/POSIXの単一ホスト診断用。本番では`locked/read/write(expected_revision=...)`を共有DBで実装する。

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

公開の契約は [本人接続手順](credential-manager-login.md)、実APIM/CMを使う受入条件・結果は [動作テスト仕様書](../testing/integration-tests.md)を参照する。PortalログアウトとGraph接続の失効操作を結び付ける範囲は実装と試験で区別する。
