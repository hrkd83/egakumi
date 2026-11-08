# Entraテストデータの準備手順（GUI）

[動作テスト仕様](integration-tests.md)の認証・グループ認可を確認するため、Microsoft Entra管理センターで専用ユーザーとグループを準備します。[構築テストの事前準備](construction-tests.md#30-pre公開手順による事前準備)でも、実在・所属・入力の対応を確認します。本書は手順と独立した架空の例であり、作成済み・認証済み・試験合格を示す記録ではありません。

実際のUPN、Object ID、所属、結果、画像は、[共通ルール](../../AGENTS.md)に従い**`.local/<environment>/evidence/`**で管理します。資格情報は同じ環境の`secrets/`または承認済みの秘密管理先へ保護保存し、公開用の例やGitへ実値を転記しません。`.azure/`はazd専用であり、fixtureの記録を置きません。

## 1. 対象テナント・名前・保持範囲を決める

1. Microsoft Entra管理センターで、[共用アプリ](../setup/02-entra.md)とPortalの認証用テナントを選びます。Azureの構築先テナントと異なる場合も、ユーザーとグループは認証先に準備します。
2. **Entra ID → ドメイン名**で、UPNへ使用するドメインがそのテナントで検証済みであることを確認します。以下の`example.com`は架空の説明用であり、入力時は選択可能な自社の検証済みドメインへ置き換えます。
3. ユーザー一覧・グループ一覧で候補名の既存利用を調べます。既存を使う場合はObject ID・用途・現在の所属を照合してから採用し、同名という理由だけで更新しません。
4. 今回新規作成する対象、既存を保持する対象、試験後に回収する対象を非公開の対応表へ記録します。ユーザー作成はユーザー管理者、グループ準備はグループ管理者等、必要な権限を持つ担当者が行います。試験ユーザー自身へその管理ロールを付与する手順ではありません。

次の4ユーザーは異なる通常の**Memberユーザー**です。U-A・U-B等は動作テスト仕様と対応する参照名として残し、UPNや表示名には用途が分かる名前を使います。MもPortalの管理機能を試す専用ユーザーで、EntraやAzureの管理者アカウントにはしません。

| 参照名 | 架空のUPN例 | 用途・属性の準備 |
| --- | --- | --- |
| U-A | `example-test-key-issuer@example.com` | キーを発行する標準利用者。名前・会社・部署・役職等に架空の検証値を用意し、Graphの表示確認に使う。 |
| U-B | `example-test-key-recipient@example.com` | U-Aとは別の本人。同一キーの共同利用・移管先を確認する。任意属性の一部は未設定にして表示を比較する。 |
| U-C | `example-test-no-access@example.com` | 適格グループに属さない対照ユーザー。 |
| M | `example-test-portal-admin@example.com` | Portalの基盤管理機能を試す専用ユーザー。G-ADMINのMember所属で認可される状態にする。 |

## 2. 4ユーザーを作成する

[Microsoft公式のユーザー作成手順](https://learn.microsoft.com/en-us/entra/fundamentals/how-to-create-delete-users)に従い、4ユーザーを1人ずつ作成します。

1. **Entra ID → ユーザー → 新しいユーザー → 新しいユーザーの作成**を開きます。
2. **基本**で予定したUPNの名前部分と検証済みドメイン、表示名を入力し、アカウントを有効にします。**プロパティ**でユーザーの種類が**Member**であることを確認し、必要な試験属性だけを設定します。実在の会社・連絡先を架空の試験属性として流用しません。
3. **パスワードの自動生成**でユーザーごとに異なる初期パスワードを用意します。手動入力が必要な場合も、秘密管理ツール等で生成した固有の暗号学的乱数を使い、同じ値を使い回しません。取得できる時点で承認済みの秘密管理先へ保管し、UPNとの対応を公開リポジトリやチャットへ貼り付けません。値が表示される画面は撮影しません。
4. **割り当て**ではディレクトリロールや不要なグループを追加しません。グループ所属は次節で表どおりに設定します。このPortal試験のためのライセンスやメールボックスも追加しません。
5. **レビューと作成**で対象テナント・UPN・Member・有効状態・役割割当なしを照合して作成します。作成後のユーザー概要でObject IDを控え、4人が別のIDであることを非公開の対応表で確認します。既存ユーザーを再利用する場合は、新規作成したとは記録せず同じ条件を照合します。

初回サインイン時のパスワード変更とMFA等の登録は、テナントの方針に従い利用者が行います。試験のためにSecurity Defaultsや条件付きアクセスを無効にしません。初回変更後は保管していた旧初期値でログインせず、利用者が管理する現在の資格情報を使います。作成、初回変更、MFA登録、Portalへのサインイン成功は別の確認として記録します。

## 3. グループとMember所属を設定する

次は`groupNamePattern`を`^EXAMPLE_FIXTURE_`とした合成例です。G-A・G-B・G-H・G-ADMINは一致し、G-Nは一致しません。実際の命名規則へ置き換え、既存G-ADMINを使う場合はその名前も対象条件へ含めます。既存の正規表現がG-ADMINの完全一致だけなら、他の適格グループを追加しても自動では許可されません。採用する名前と正式な設定変更の範囲を先に決め、G-Nが除外されることも確認します。

| 参照名 | 架空のグループ名例 | U-A | U-B | U-C | M | 用途 |
| --- | --- | --- | --- | --- | --- | --- |
| G-A | `EXAMPLE_FIXTURE_KEY_SOURCE` | Member | Member | 非所属 | 非所属 | 所有元・共同利用 |
| G-B | `EXAMPLE_FIXTURE_KEY_DESTINATION` | 非所属 | Member | 非所属 | 非所属 | U-Aが所属しない移管先 |
| G-H | `EXAMPLE_FIXTURE_GROUP_LIFECYCLE` | 非所属 | Member | 非所属 | 非所属 | 名前変更・対象外化・廃止専用 |
| G-N | `OUTSIDE_FIXTURE_INELIGIBLE` | 非所属 | 非所属 | Member | 非所属 | 正規表現に一致しない対照 |
| G-ADMIN | `EXAMPLE_FIXTURE_PLATFORM_ADMIN` | 非所属 | 非所属 | 非所属 | Member | Portalの基盤管理 |

表はこの4ユーザーの期待状態です。G-ADMINに既存の管理担当者が所属している場合、そのMember・Ownerを保持したまま専用MをMemberへ追加します。既存管理者を外す試験にはしません。作成者やOwnerであること、Entra・Azureの管理者権限は、Portalが確認するMember所属の代わりにはなりません。

[Microsoft公式のグループ作成・所属管理手順](https://learn.microsoft.com/en-us/entra/fundamentals/how-to-manage-groups)に従い、対象を1つずつ設定します。

1. **Entra ID → グループ → すべてのグループ**を開きます。既存のG-ADMINは、[PortalのAPIキー所有グループ](../setup/04-params.md#portalのapiキー所有グループportalapikeys)で控えたObject IDと照合します。
2. 新規のグループは**新しいグループ**から、種類を**セキュリティ（Security）**、メンバーシップを**割り当て済み（Assigned）**にします。予定した名前・用途の説明を設定します。ロール割り当て可能なグループは使わず、その設定が表示される場合は**いいえ**を選びます。
3. **メンバー**から表のユーザーをUPN・Object IDで照合して選びます。**所有者**だけを設定してMember追加の代わりにしません。対象テナント・グループ名・種類・予定メンバーを見直して**作成**します。
4. 既存グループへ追加する場合は、そのグループの**メンバー → メンバーの追加**で表の対象者だけを選んで保存します。既存G-ADMINの他の所属や所有者を削除しません。
5. 保存後に**概要／プロパティ**でObject ID・Security・Assignedを照合し、**メンバー**一覧で表の所属と非所属を確認します。U-Cが別の適格グループへ間接所属していないことも確認します。G-Hは通常利用や既存管理者のグループと混用しません。
6. [paramsのPortal設定](../setup/04-params.md#portalのapiキー所有グループportalapikeys)へ渡す実Object ID、正規表現、検証用の承認Productと購読数上限を非公開の設定表へまとめます。G-ADMINは`platformGroupId`と初期所有グループの対応を確認します。G-A・G-B・G-Hの適格性とG-Nの非適格性を同じ正規表現で照合し、GUID書式や`configuration.py validate`の成功だけで実在・所属・正規表現の確認に代えません。

## 4. 保存後の確認と画面エビデンス

ユーザー作成とグループ保存の直後に、非公開の対応表へ新規／再利用の区分と確認済み条件・未確認条件を記録します。権限の付与状態、Member所属、初回サインイン、アプリの認可動作を一つの成功扱いにしません。

画面エビデンスは、今回作成・変更して保存したユーザー概要、グループ概要・Member一覧等に限定します。アプリ内ブラウザーの表示領域を**1920×1080ピクセル**にし、ページ全体の縦長画像にせず、画像の実寸も確認します。パスワード、認証コード、MFAの登録情報、回復情報が表示される画面とサインイン画面は撮影しません。読取だけの確認画面を撮影するために設定を変更しません。画像も対応表も`.local/<environment>/evidence/`へ保護保存し、Git・公開文書・配布物へ含めません。

## 5. 個別の試験で追加する前提と回収

U-Xは許可外テナントの試験を実施するときに、使用権限のある別テナントの検証アカウントを用意します。今回の4ユーザー作成に新規テナントやゲスト招待を含めず、認証用テナントへ招待したゲストを許可外テナントの本人認証の代わりにしません。非セキュリティグループ等の追加対照も、採用ケースに応じて専用に準備します。

会社C-A/C-B、購読K-A/K-B/K-L/K-D、本人接続は、該当テストの正式な作成手順で用意します。試験後は今回所有した専用データだけを、事前に決めた保持・回収範囲に従って処置します。通常利用者の接続や既存G-ADMINの管理者、他の組織のデータへ回収を広げません。準備が済んだだけで、動作テストや構築・撤去を合格にはしません。

### 5-1. KEY-02用の非セキュリティグループを追加する場合

`KEY-02`の`securityEnabled=false`の対照は、前節のSecurityグループとは別に用意します。[Microsoft Graphの作成仕様](https://learn.microsoft.com/en-us/graph/api/group-post-groups?view=graph-rest-1.0)では、Entra管理センター／Azure Portalで作成したグループは初期値が`securityEnabled=true`です。GUIで種類をMicrosoft 365にしただけでは非Securityの確認になりません。以下はGraph APIを使う追加手順であり、GUIによる作成の合格として記録しません。

1. 認証用テナントの管理者と、追加する専用グループ・Owner・Member・副作用・回収方針を確定します。Memberは既存のU-Bなど専用試験ユーザーだけにし、通常の組織グループを変更しません。すでに認証したAzure CLIの管理者委任権限を使い、Azure CLIのTenantと認証用テナントを照合します。権限不足の場合は停止し、Core共用アプリへGraphの書込権限を追加して回避しません。ライセンス割当・購入・試用開始も行いません。
2. 保護済みの`.local/<environment>/`と保存先がシンボリックリンクでないこと、ディレクトリー700・ファイル600になることを確認します。リポジトリルートで次を設定し、要求・応答・エラーをこの小さな専用記録へ保存します。Bearer tokenを取得して表示したり、`--debug`で認証情報を記録したりしません。

   ```sh
   umask 077
   : "${DEPLOY_ENVIRONMENT:?第1章で決めた環境名を設定してください}"
   export FIXTURE_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}/evidence/non-security-fixture"
   mkdir -p "$FIXTURE_DIRECTORY"
   chmod 700 "$FIXTURE_DIRECTORY"
   ```

3. 同名・同じmailNicknameの既存グループと、この保存先の既存記録を確認します。記録がある場合は上書きして新規作成せず、その試行の成否を確認して再開します。新規作成する場合は、次の**独立した架空例**を`$FIXTURE_DIRECTORY/create-group.request.json`へ600で保存し、名前と2つのObject IDを確定した対象へ置き換えます。Microsoft 365のUnifiedグループをPrivate／Assignedで作成し、`securityEnabled:false`を明示します。通常のSecurityグループや、Graphで作成できない配布グループへ読み替えません。

   ```json
   {
     "displayName": "ORG_TEST_NON_SECURITY",
     "description": "Dedicated non-security group for KEY-02 rejection tests",
     "mailNickname": "org-test-non-security",
     "groupTypes": ["Unified"],
     "mailEnabled": true,
     "securityEnabled": false,
     "visibility": "Private",
     "resourceBehaviorOptions": [
       "WelcomeEmailDisabled",
       "ProvisionSiteOnDemand",
       "HideGroupInOutlook"
     ],
     "owners@odata.bind": [
       "https://graph.microsoft.com/v1.0/users/<FIXTURE_OWNER_OBJECT_ID>"
     ],
     "members@odata.bind": [
       "https://graph.microsoft.com/v1.0/users/<U_B_OBJECT_ID>"
     ]
   }
   ```

   [作成時のオプション](https://learn.microsoft.com/en-us/graph/group-set-options)で歓迎メールを無効にし、SharePointサイトをオンデマンド作成にし、Outlookの一覧から隠します。Teamsは作成しません。`mailEnabled:true`は必要であり、テナントのサービスに応じてグループメールボックス等が準備される可能性はあります。Microsoft 365の機能利用権は作成者・テナントの契約に依存するため、この手順ではメールやサイトの利用を試験せず、テナントの制約で作成できなければ条件不足として記録します。[Microsoft 365グループの機能と契約](https://learn.microsoft.com/en-us/microsoft-365/admin/create-groups/office-365-groups)

4. 作成内容を確認してから、[Azure CLIの`az rest`](https://learn.microsoft.com/en-us/cli/azure/reference-index#az-rest)で1回だけ送信します。CLIの既存ログインによる認証を使い、Coreアプリのclient secretは使いません。

   ```sh
   az rest --method post \
     --resource https://graph.microsoft.com \
     --url https://graph.microsoft.com/v1.0/groups \
     --headers Content-Type=application/json \
     --body "@$FIXTURE_DIRECTORY/create-group.request.json" \
     --output json \
     > "$FIXTURE_DIRECTORY/create-group.response.json" \
     2> "$FIXTURE_DIRECTORY/create-group.stderr.txt"
   ```

   終了コードと作成応答のObject IDを確認して控えます。失敗・応答消失時は作成の成否を調べる前にPOSTを繰り返しません。作成済みならそのID・用途・所有者を照合して再利用し、同名の別物を更新しません。
5. 作成応答だけで判定せず、`GET /groups/{id}?$select=id,displayName,mailNickname,groupTypes,mailEnabled,securityEnabled,visibility,resourceBehaviorOptions,resourceProvisioningOptions`で実ID・表示名・mailNickname・`groupTypes`・`mailEnabled`・`securityEnabled:false`・Private・作成時オプションを確認します。作成時オプションは通常GETの既定応答に含まれると仮定せず、`$select`で明示します。`GET /groups/{id}/owners`と[Member一覧](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0)で期待するObject IDだけが含まれることを確認し、ページングがあれば全ページを照合します。小さな読取結果も同じ保護領域へ保存します。保存後のGUI確認は補助であり、種類名だけで`securityEnabled:false`を代用しません。
6. グループ名が現行の`groupNamePattern`に一致しなければ、それだけではSecurity条件の拒否を分離できません。隔離した設定分岐で、元の正規表現と既存の許可対象を控え、この専用名の完全一致だけを許可候補へ追加します。同じparamsを使う[Portalの正式な設定反映](../setup/06-deploy.md#gatewayとportalの個別更新再試行)後、名前一致／不一致とSecurity true／falseの組合せを比較します。名前が一致しても非Securityグループがキー所有・移管候補へ出ず、適格なSecurityグループは候補に出ることを実測します。終了後は元の正規表現を正式手順で反映し、元の候補と拒否条件へ戻ったことを確認します。
7. 保持するか回収するかを事前の方針どおりに処理します。回収する場合は今回の作成記録・所有者・Member・実IDを再照合し、[グループ削除](https://learn.microsoft.com/en-us/graph/api/group-delete?view=graph-rest-1.0)の`DELETE /groups/{id}`でこの1件だけを削除します。現行グループのGET 404と、削除済み項目の状態を記録します。Microsoft 365グループの30日間の復元可能な論理削除保持と、稼働中グループの消失を区別し、既存G-A／G-B／G-H／G-N／G-ADMINや別グループを名前検索で一括削除しません。
