# PIIの固定要求とマスキング結果

PIIの入力、固定Language要求、提供元結果、最終応答のJSON解析では、日時形式も文字列のまま保持します。`2000-01-02T03:04:05.000Z`という合成例をDate型へ変換して400／502へ誤分類したり、末尾の`.000`を落としたりすることを防ぎます。APIMで利用できる正規表現でJSONの文字列値へ一時的に`~`を足し、`JObject.Parse`後に一つだけ戻します。項目名・コメントを保持し、両正規表現には2秒のタイムアウトを設けます。本文読取りの回数とpreserveContent、型・空白・言語・サイズ・文書ID・モデル版・警告の条件、内部500と外部400／413／422／502、`loggingOptOut:true`は維持します。

最終の本文復元を`output`区画へ置き、PythonとBicepが同じ式を生成します。新しいBicep関数は追加しません。実PIIでの文字列保持はAzure反映後に確認します。

関連58試験は全件成功し、失敗・エラー・スキップはありません。この中に文字列保持の4試験・112合成ケースを含み、ISO日時・小数秒・offset・Unicode・escape・既存の先頭`~`、入力と提供元の型不備、サイズ境界・文書ID・モデル版・警告・安全なエラー、整数idのMCP対象範囲と本文読取りを確認しました。既存PII／MCP、同名区画所有、XML展開、配備／保護sourceの一致と容量、Razor構文も確認済みです。Bicepの関数数は従来と同じ58で、上限64以内です。ローカル検証済みで、この文字列保全修正のAzure反映・実経路受入は未実施です。

MOD-131ではresultの内部設定取得を、提供元応答の不備による502と分けました。設定JSONは従来どおり本文前、モデル版は外部の形・件数・IDと外部モデル版castの後、許可警告一覧は切詰め警告の後という取得順と短絡を維持します。内部取得中だけ再送出し、固定PolicyId `core-pii-result-settings`を既存500／`PII_SETTINGS_INVALID`／`PiiProcessingFailed`へ渡します。設定JSON失敗は応答read0、後半getter失敗はread1、非200は設定・応答read0です。許可警告一覧のJSON nullは既存JArray cast自体の失敗で500、キー欠落はcast後の既存Anyで502になります。

正式設定ではモデル版が必須文字列です。内外の版が両方欠落／nullで一致と扱われる経路を閉じるため、request-settingsとresultの内部モデル版だけString・非空を必須として500へ分けました。これはMOD-129で一時維持した欠落／null／数値castの判断を更新します。外部だけの版欠落・外部object／array castは502を維持し、版一覧・範囲や公開入力の追加検査は加えません。audit-settingsの既存castは今回の対象外です。

関連38件の初回は42.363秒で1失敗・他37成功でした。独立レビューの外部cast先行を補正して再実行した38件も、開始時点のJSON null期待が古いままで1失敗・他37成功（48.860秒）でした。JSON nullの実cast挙動に期待を揃えたPII19件は6.177秒、最終の関連38件は47.159秒でそれぞれ全件成功しました。設定解析5不備・内部モデル6不備×外部版の有無、外部cast2対照、警告getterと既存短絡、完全一致／近似／null／空ID、既存の安全なエラー・サイズ・認証、配備／保護source一致・容量・構文・所有を確認しました。ローカル整理済みでAzure未反映です。

MOD-130では監査用の内部設定解析と既存modelVersion castを`audit-settings`専用setterへ分け、固定PolicyId `core-pii-audit-settings`だけ既存の内部500へ渡します。既存の公開結果監査後・`core-pii` trace前に取得するため、設定取得は従来のpiiResult解析後から、その解析前へ移ります。正常は設定read1・追加の要求／応答本文read0を維持し、piiResult・inputBytes・detectedCount等の失敗は一般502を維持します。型・範囲の検査は追加せず、モデル版の欠落・null・数値の既存castを維持します。

初回は区画登録順の不一致で試験開始前のimportに失敗し、同名C#の順序へ補正しました。再実行37件は43.441秒で全件成功しました。内部7不備・既存モデル版3対照・非設定4不備・固定ID対照、先行認証／回数枠と既存エラー、配備／保護source一致・容量・構文・所有を確認しました。ローカル整理済みでAzure未反映です。

MOD-129では、要求組立て用の内部JSON解析と既存modelVersion castを`request-settings`区画と専用setterへ分けました。固定PolicyId `core-pii-request-settings`の取得・変換失敗だけ、既存の500／`PII_SETTINGS_INVALID`監査と`PiiProcessingFailed`へ渡します。固定要求は準備した値を使い、本文読取り・組立ての例外は一般502を維持します。正常時の本文・設定読取り各1回は維持し、内部設定の失敗時は設定を先に取得するため本文read1から0へ変わります。新しい型・範囲検査を加えず、モデル版の欠落・nullと数値の既存文字列変換を維持します。

関連36件は51.109秒で全件成功しました。設定7不備の本文read0／設定read1・監査1回、正常とモデル版3対照の本文read1／設定read1、本文失敗502、完全一致／近似／null／空ID、既存401／429／502／504、別のPII APIサイズ契約、配備／保護source一致・容量・構文・区画所有を確認しました。ローカル整理済みでAzure未反映です。

MOD-121では、outboundの監査順序を説明へ揃えました。親HTTPの`BACKEND_RESPONSE`は提供元の状態を記録し、PII操作はその後に公開結果へ変換して、その状態を`PII_REDACTED`／`PII_PROCESSING_FAILED`へ記録します。続く`core-pii` traceは既存の固定サイズ・検出件数・モデル版を記録します。変換で200が502などに変わるため、提供元の状態と公開結果の監査を両方維持します。順序・回数・本文読取り・出力は変えず、XMLのコメント除外後の構造・属性・実式が完全一致しました。文書のみの整理で新規試験は追加せず、Azure反映は未実施です。

MOD-120では、inputの内部設定解析と既存のサイズ・言語値の取得を、本文用の400 catchの前へ分けました。設定の取得・解析・既存long変換が失敗した場合だけ、固定PolicyId `core-pii-input-validation`から500へ停止し、`policy-error`／`PII_SETTINGS_INVALID`を1回記録します。公開500には新しい内部専用コード`PiiProcessingFailed`を使い、要求IDとno-storeを維持します。設定の型・範囲検査を追加せず、null・空・配列でない言語一覧の従来の扱い、本文の受信バイト→JSON→compactサイズ検査、400／413を維持します。

関連28件は18.717秒で全件成功しました。内部不備9対照の本文読取り0回・設定読取り1回・要求未組立・監査1回、正常と400／413の読取り順と回数、PolicyId完全一致だけの500、既存401／429／502／504を実式で確認しました。Razor・区画所有・全protocolの配備／保護source・容量も確認済みです。request／audit区画の設定取得失敗は既存の一般502、result内の設定catchは既存`PiiInvalidUpstreamResult`の502として残り、別棚卸で扱います。ローカル整理済みで、Azure反映・実PII受入は未実施です。

MOD-119では、inputの全項目名を`Any`で調べる1条件だけを削除しました。項目数2件と必須`text`／`language`文字列の条件を残すため、追加項目・欠落・型違いの拒否は維持します。空白・言語・サイズ・本文読取り・要求変換・例外処理も変更しません。既存15件は3.323秒で全件成功しました。ローカル整理済みで、Azure反映・実PII受入は未実施です。

`pii.xml`はPII REST操作へ登録する正本です。同名C#は入力・内部設定・要求・応答・監査の区画を固定の`BEGIN`／`END`で管理し、短い`@(...)`の単式はXMLへ保持します。ファイル全体を一つのC#式として使いません。

| 区画 | XMLでの役割 | marker |
|---|---|---|
| `input` | `piiInputStatus`：本文・言語・サイズの入力検査 | `__PII_INPUT_STATUS_CSHARP__` |
| `request-settings` | `piiRequestModelVersion`：要求用の内部モデル版取得 | `__PII_REQUEST_SETTINGS_CSHARP__` |
| `request` | inboundの本文：Language向け固定要求の組立て | `__PII_REQUEST_CSHARP__` |
| `retry` | `piiRetryAfter`：下流429の待ち時間ヘッダー | `__PII_RETRY_AFTER_CSHARP__` |
| `result` | `piiResult`：公開するマスキング結果と安全な失敗の組立て | `__PII_RESULT_CSHARP__` |
| `output` | outboundの本文：文字列を保った固定結果の復元 | `__PII_OUTPUT_CSHARP__` |
| `audit-settings` | `piiAuditModelVersion`：監査用の内部モデル版取得 | `__PII_AUDIT_SETTINGS_CSHARP__` |
| `audit` | `core-pii`のtrace：固定のサイズ・件数・モデル版の記録 | `__PII_AUDIT_CSHARP__` |

inboundは親の認証・共通入力検査を継承してから、公開の`text`／`language`の項目・型・空白・言語・サイズを検査します。元の受信バイト数とcompact JSONの上限、固定Language要求の1 MB上限はそれぞれ維持します。入力は正規化・切詰めをせず、操作が`PiiEntityRecognition`の1文書要求を作り、`loggingOptOut:true`、指定モデル版、`TextElements_v8`を設定します。

outboundは親を継承してから提供元の結果を検査し、マスキング済み本文と言語・検出件数・モデル版・許可された警告だけを返します。document ID・型・件数・モデル版・切詰め警告の照合と、失敗時の固定コード・状態、下流429の安全な`Retry-After`を維持します。元の個人情報・entity内容・下流の生エラーは応答や監査へ出しません。

応答変換後の監査、既存no-store、独自on-errorの順序と安全なエラー本文を維持します。共通Entraトークン検証の失敗だけを共通401へ渡す処理と、固定PolicyIdの回数枠超過を共通429へ渡す処理も変更しません。生成MCPはこのREST操作を参照し、そのJSON-RPCやSSEをこの操作で変換しません。

MOD-113で入力・サイズ・外部応答の検査を含む5区画をBicepとPythonへ結線し、fixture・Razor・同名区画所有を正規XML／C#へ揃え、直下の旧XMLを撤去しました。構造・属性・実式トークンはfull／compact／omitで移設前と一致しました。API設定・共通Entra／429処理の置換はC#展開後の従来順を維持します。

関連50件が34.927秒で全件成功しました。失敗・エラー・スキップはありません。配備／保護source・容量、実C#による入力・外部応答・認証エラーの対照を確認済みです。Nativeサイズ式の直接XML読取りも正規rendererへ揃えた後、PII単独の別15件が2.672秒で全件成功しました。別実行を一回の全件成功へ合算しません。Azure反映と実PII受入は未実施です。この時点で残した重複項目名検査はMOD-119、inputの内部設定取得の分類はMOD-120で整理しました。親と専用監査の説明、他区画の設定取得失敗は続く個別項目で扱います。
