import json,re,copy
from pathlib import Path
P=Path(__file__).parent
s=json.loads((P/'script.json').read_text())
s['status']='ready-for-generation'
s['learningObjectives']=[
 '所有グループの配備時の承認と、Graphによる現在の観測を分けて説明できる。',
 'SQL台帳とAPIM実体をIDで照合し、更新・再作成・削除・対象外を区別できる。',
 '不存在と取得不能を区別し、不一致や変更時の確認先を選べる。']
chap=[
 ('opening','今回の問い','一覧の不一致を見たとき、すぐ削除せず確認する対象を選ぶ。','group-observation'),
 ('purpose','三つの状態を分けて見る','Entra組織・SQL台帳・APIM実体の保存範囲を区別する。','group-observation'),
 ('observation','承認と観測を分ける','観測状態・15分Job・取得不能時の確認先を説明する。','group-observation'),
 ('sync','SQLとAPIMを照合する','IDに基づく差分と、同期の操作・キーへの影響を判断する。','subscription-sync'),
 ('apply','確認してから反映する','最大10件・5分期限・再検証・監査・結果照会の意味を説明する。','subscription-sync'),
 ('investigation','不一致から確認先を選ぶ','取得不能・再作成・部分成功を具体例で切り分ける。','subscription-sync'),
 ('maintenance','変更する機能を一続きで確認','画面・入力・SQL・APIM・権限・テストの確認範囲を選ぶ。','maintenance'),
 ('check','三つの問いで確かめる','未知・キー変更・外部変更の取消について判断できる。','subscription-sync'),
 ('summary','まとめと次回の問い','観測・台帳・実体を整理し、本人接続の学習へ進む。','maintenance')]
s['chapters']=[{'id':i,'title':t,'goal':g,'sourceRefs':[{'file':'../03-subscription-management.html','anchor':a}]} for i,t,g,a in chap]
extra=[
 ('../../../apps/portal/lib/api-key-administration/service.ts','所有グループ観測の実行'),
 ('../../../apps/portal/lib/api-keys/graph.ts','観測の四状態とエラーの扱い'),
 ('../../../apps/portal/lib/api-keys/repository.ts','観測の保存と時刻競合'),
 ('../../../apps/portal/deploy/group-reconciliation-job.bicep','15分周期の観測Job'),
 ('../../../apps/portal/scripts/reconcile-api-key-groups.ts','Job結果と終了コード'),
 ('../../../apps/portal/lib/subscription-sync/differences.ts','差分の分類'),
 ('../../../apps/portal/lib/subscription-sync/service.ts','確認と実行の再検証'),
 ('../../../apps/portal/lib/subscription-sync/repository.ts','ロックと監査の保存')]
existing_refs={r['file'] for r in s['sourceReferences']}
s['sourceReferences'] += [{'file':f,'purpose':p} for f,p in extra if f not in existing_refs]
V={}
def view(key,title,section,rows,notice,cols=('確認する対象','保存内容・判断')):
 V[key]={'layout':'settings','viewId':'lesson18-'+key,'areaLabel':'Portalの状態管理 / 教材用の設定メニュー',
 'title':title,'kind':'Portal管理','section':section,'menu':['状態の分担','組織の観測','APIM同期','反映と監査','変更時の確認'],
 'subtitle':'実装・資料に基づく説明用の表示','footer':'リポジトリ定義に基づく教材例 / 稼働環境の取得結果ではありません',
 'notice':notice,'table':{'columns':[{'label':cols[0],'width':300},{'label':cols[1],'width':672}],
 'rows':[{'id':rid,'cells':[label,val]} for rid,label,val in rows]},'highlightRows':[]}
view('roles','組織・台帳・利用実体を分ける','状態の分担',[
 ('directory','Entra / Graph','グループの存在・名前・セキュリティ属性'),
 ('registry','SQL台帳','会社・所有グループ・製品・状態・APIM ID'),
 ('runtime','APIM実体','API利用の許可・状態・主キーと副キー'),
 ('link','照合に使うID','グループID / apimSubscriptionId')], '同じ「状態」でも、確認する場所と目的が違う')
view('approval','所有グループの承認と観測','組織の観測',[
 ('approval','配備で決める承認','所有グループと製品の利用を認める設定'),
 ('observed','Graphからの観測','現在の表示名・状態・確認日時・エラー'),
 ('store','portalApiKeyGroup','承認情報と観測情報を分けて保存'),
 ('effect','観測Jobの範囲','承認・所有関係・APIM実体は変更しない')], '存在を確認できても、それだけで利用を承認しない')
view('statuses','Graph観測の四つの状態','組織の観測',[
 ('active','active','現在のグループ条件を満たす'),
 ('ineligible','ineligible','存在するが名前規則・属性などの条件外'),
 ('deleted','deleted','グループIDのGETで明示的な404'),
 ('unknown','unknown','403・429・通信障害などで判断できない')], 'グループのactiveと、APIMのactiveは別の状態',('directoryStatus','観測から言えること'))
view('job','定期Jobで観測を更新する','組織の観測',[
 ('schedule','起動間隔','15分ごと'),
 ('batch','対象の選択','未確認・古い確認から最大100件'),
 ('parallel','Graph照会','グループIDごとに最大4件並行'),
 ('save','SQL保存','最新の名前規則と確認時刻を考慮'),
 ('result','終了結果','unknownあり: partial / 設定・SQL例外: failed')], '全件を一度に更新するとは限らない')
view('group-check','「不明」のときの確認順','組織の観測',[
 ('observe','観測欄','directoryStatus・確認日時・エラー'),
 ('job','Jobの実行履歴','完了日時・確認件数・不明件数'),
 ('graph','Graphへの照会','実行用ID・権限・通信・応答'),
 ('registry','SQLへの保存','承認・観測・影響するAPIM件数')], '取得不能という結果から、削除済みとは判断しない')
view('compare','SQLの確定値とAPIM現在値を照合','APIM同期',[
 ('id','対応付け','apimSubscriptionIdで同じ実体を探す'),
 ('sql','SQLの確定値','通常は会社名・製品・active / suspended'),
 ('apim','APIMの現在値','表示名・製品scope・状態・存在'),
 ('key','秘密のキー','同期の比較項目には含めない')], '表示名の一致だけでは、同じ対象と判断できない')
view('actions','差分から操作を分類する','APIM同期',[
 ('update','更新','両方に存在し、表示名・製品・状態が違う'),
 ('recreate','再作成','対象になるSQL確定値はあるが、APIM実体がない'),
 ('delete','削除','SQL削除済み / SQL未登録のAPIM実体'),
 ('none','変更なし','比較対象の値が一致している'),
 ('blocked','対象外','作成中・削除中 / 組み込みmasterなど')], '同じ「不一致」でも、利用とキーへの影響が異なる')
view('key-impact','更新と再作成の影響を確認','APIM同期',[
 ('update','既存実体の更新','主キー・副キーを維持する'),
 ('resume','状態をactiveへ更新','停止中の利用を再開し得る'),
 ('recreate','実体の再作成','主キー・副キーが新しくなる'),
 ('delete','実体の削除','対象サブスクリプションを利用できなくする')], '作業前に、呼出アプリへの影響を確認する')
view('preview','対象を選び、反映内容を確認','反映と監査',[
 ('count','一度に選ぶ対象','1〜10件'),
 ('fresh','確認時の照会','SQLとAPIMを再取得する'),
 ('token','確認内容','対象と状態のハッシュに改変検出用の署名'),
 ('expiry','有効期限','確認から5分'),
 ('binding','結び付け','本人・テナント・固定された接続先')], '古い一覧のまま、一括反映しない')
view('apply','実行直前にもう一度確認','反映と監査',[
 ('auth','本人と接続先','管理者所属・正本DB・期限・署名を確認'),
 ('conflict','対象の現在値','ロック内でSQL/APIMを再照会し差分を検出'),
 ('start','開始監査','外部操作を送る前にSQLへ保存'),
 ('operation','APIM操作','更新PATCH / 再作成PUT / 削除DELETE'),
 ('verify','結果の再照会','APIMの状態を取り直して結果を保存')], '確認後の変更を検出したら、一覧と確認からやり直す')
view('results','件ごとの結果と監査を追う','反映と監査',[
 ('table','portalSubscription\nSyncAudit','対象・確認内容・開始と結果を追記'),
 ('success','成功','APIMを再照会して期待状態を確認'),
 ('pending','保留','削除待ちなど、完了を確認できていない状態'),
 ('failed','失敗','結果コードと現在値から確認先を選ぶ'),
 ('rollback','SQLの取消','APIMへ送った変更の取消にはならない')], '複数件が一つのトランザクションで戻るわけではない')
view('case','教材例: 会社名変更後の表示名差分','APIM同期',[
 ('sql','SQLの確定値','会社名: 新会社名 / 状態: active'),
 ('apim','APIMの現在値','表示名: 旧会社名 / 状態: active'),
 ('identity','同一性','同じapimSubscriptionIdで照合'),
 ('action','選ぶ操作','既存実体の表示名を更新'),
 ('verify','完了確認','再照会した表示名と同期監査を確認')], '例示データ / 実環境での操作結果ではありません')
view('unavailable','「取得不能」と「不存在」を分ける','APIM同期',[
 ('sql','SQL一覧を取得不能','SyncRegistryUnavailable / SQL接続・権限'),
 ('apim','APIM一覧を取得不能','UpstreamUnavailable / APIM接続・権限'),
 ('audit','監査を取得不能','SyncAuditUnavailable / 監査の参照先'),
 ('missing','完全取得後に実体なし','対象IDを照合し、再作成などを判断')], '取得に失敗した一覧を、空の一覧として扱わない')
view('change-input','画面と許可条件を変えるとき','変更時の確認',[
 ('display','表示項目','components / lib/i18n'),
 ('input','入力の通り道','contracts → http → service → repository → SQL'),
 ('permit','利用を認める条件','service / graph / group-policy / 承認設定'),
 ('last','実行直前の確認','一覧表示時だけでなく実行時にも確認')], '見た目の変更でも、保存や許可条件への影響を確認する')
view('change-store','SQLとAPIMを変えるとき','変更時の確認',[
 ('sql','SQLの表・列','sql / repository / migrate-auth.ts'),
 ('grant','SQLの実行用権限','auth-runtime-grants.tsも確認'),
 ('apim','APIM操作','api-keys/apim.ts / API・製品・ポリシー'),
 ('identity','外部操作の権限','実行用IDの権限と配備設定'),
 ('deploy','配備・Job','SQL準備とアプリ更新の順序')], '定義を追加するだけでは、実行権限は付かない')
view('tests','確認用テストの分担','変更時の確認',[
 ('http','HTTPテスト','入力拒否・本人・権限の入口'),
 ('service','Service / 外部APIテスト','許可条件・外部要求・失敗時の振る舞い'),
 ('sql','*.sql.test.ts','実SQLの保存・競合・権限'),
 ('run','SQLテストの実行','通常は省略 / ローカルSQL専用の明示設定')], '通常テストの成功だけで、実SQLの権限確認済みとはしない')
view('quiz1','確認1: Graphから403が返った','組織の観測',[
 ('a','候補A','削除済みと判断し、APIM実体を削除'),
 ('b','候補B','unknownと観測日時・エラーを確認'),
 ('c','候補C','観測できなかったので、新規に承認')], 'どの判断を選び、次にどこを確認しますか？',('選択肢','判断'))
view('quiz2','確認2: SQLにはあり、APIMにはない','APIM同期',[
 ('condition','前提','一覧は完全取得済み / SQLは有効な確定値'),
 ('a','候補A','再作成しても既存キーをそのまま使う'),
 ('b','候補B','再作成によるキー変更を確認して進める'),
 ('c','候補C','APIMがないのでSQLの所有だけ変更')], '呼出アプリに何を伝える必要がありますか？',('前提・選択肢','確認内容'))
view('quiz3','確認3: APIM反映後、SQLで例外','反映と監査',[
 ('situation','状況','APIMへ操作を送信 / SQL側で保存失敗'),
 ('a','候補A','SQLを取り消せば、APIMも必ず元に戻る'),
 ('b','候補B','APIM現在値と開始監査を再確認する'),
 ('c','候補C','結果不明のまま同じ確認内容を連打する')], '外部操作の結果を、何で確かめますか？',('状況・選択肢','判断'))
view('summary','確認する対象と判断を結び付ける','状態の分担',[
 ('observe','組織の観測','承認と観測を分離 / 不明を不存在にしない'),
 ('sync','SQLとAPIM','IDで照合 / 操作とキーへの影響を区別'),
 ('apply','反映と結果','再確認・開始監査・再照会で追跡'),
 ('change','変更時の確認','入力・保存・外部設定・権限・テスト')], '次回: APIキーだけでは本人を区別できない理由')
turns=[]
def add(ch,v,sp,text,hl=()):
 chunks=text.split('|'); cap=''.join(chunks)
 # Use short clause groups, two lines per cue, with ascii-aware widths.
 def units(t): return sum(.58 if ord(c)<128 else 1 for c in t)
 out=[]
 for c in chunks:
  pieces=[]; cur=''
  for token in re.findall(r'.*?[、。？！?！]|.+$',c):
   if cur and units(cur+token)>56: pieces.append(cur);cur=token
   else:cur+=token
  if cur:pieces.append(cur)
  for piece in pieces:
   lines=[];line=''
   for char in piece:
    if line and units(line+char)>29:lines.append(line);line=char
    else:line+=char
   if line:lines.append(line)
   if len(lines)>2: raise ValueError(piece)
   out.append({'caption':piece,'lines':lines,'weight':len(piece)})
 speech=cap
 for a,b in [('APIM','エーピーアイエム'),('API','エーピーアイ'),('SQL','エスキューエル'),('Entra','エントラ'),('Graph','グラフ'),('Portal','ポータル'),('Job','ジョブ'),('ID','アイディー'),('GET','ゲット'),('PATCH','パッチ'),('PUT','プット'),('DELETE','デリート'),('HTTP','エイチティーティーピー'),('Service','サービス'),('scope','スコープ'),('active','アクティブ'),('suspended','サスペンデッド'),('ineligible','イネリジブル'),('deleted','デリーテッド'),('unknown','アンノウン'),('partial','パーシャル'),('failed','フェイルド'),('master','マスター')]: speech=speech.replace(a,b)
 visual={'layout':'hologram','heading':s['topic']} if ch=='opening' else copy.deepcopy(V[v])
 if ch!='opening':visual['highlightRows']=list(hl)
 i=len(turns)+1
 delivery={'mako':'問いを同僚へ自然に投げかける。納得や確認は短く、通常の速さで話す。', 'manabu':'目的と判断理由を、相手への落ち着いた返答として話す。通常速度。', 'ikumi':'質問へ明瞭に答え、仕組みと確認先を穏やかに説明する。通常速度。'}[sp]
 t={'id':f'lesson18-turn-{i:03d}-{sp}','speaker':sp,'chapterId':ch,'speech':speech,'caption':cap,'subtitleLines':out[0]['lines'],'subtitleChunks':out,'gapAfterSeconds':0.18,'delivery':delivery,'visual':visual}
 turns.append(t)
# Opening: a topic-specific hook, no recycled introductions.
add('opening',None,'mako','所有グループが「不明」になったら、そのグループのAPIキーは、すぐに止まるんですか？')
add('opening',None,'manabu','そこは分けて考えたいですね。組織を確認した結果と、実際に利用を止める操作は別です。')
add('opening',None,'ikumi','今日は、組織の観測、SQLの台帳、APIMの実体を照らし合わせます。不一致でも、すぐ削除とは限りません。')
add('opening',None,'mako','マナブさん、イクミさんと、状態を見て次の確認先を選べるようにしていきます。')
# Three sources of state.
add('purpose','roles','mako','まず、同じAPIキーのことなのに、なぜ三つの場所を見る必要があるんでしょう？ 一覧を一つ見れば足りませんか。')
add('purpose','roles','manabu','知りたいことが違うからです。誰が所有してよいか、何を登録したか、いま何が利用できるか。それぞれの根拠を分けて追える必要があります。',('directory','registry','runtime'))
add('purpose','roles','ikumi','Entraのグループは組織の現在の情報です。SQLには会社、所有グループ、製品、状態などの台帳があり、APIMにはAPI利用の許可とキーの実体があります。',('directory','registry','runtime'))
add('purpose','roles','mako','Portalの表示だけで、組織も実体も全部同じ状態だと思わず、どの情報を見ているか確かめるんですね。')
add('purpose','roles','ikumi','はい。名前が同じというだけでも足りません。グループはグループID、SQLとAPIMはAPIMサブスクリプションIDで対応付けます。ここで扱うのはリポジトリ定義に基づく説明です。',('link',))
# Approval versus directory observation.
add('observation','approval','mako','では、所有グループの健全性確認から。Graphでグループが見つかれば、利用を承認したことになりますか？')
add('observation','approval','manabu','承認には、組織として利用を認めるという判断が含まれます。存在の確認だけでは、その判断まで済んだことにはなりません。',('approval',))
add('observation','approval','ikumi','配備で決めた承認と、Graphで見た現在の表示名や状態を、SQLの同じグループの記録に分けて持ちます。観測には確認日時やエラーも残します。',('observed','store'))
add('observation','approval','mako','同じ記録にあっても、「認めている」と「いまこう見えた」は、別の欄として読むんですね。')
add('observation','approval','ikumi','そのとおりです。観測を更新する処理は、承認や所有関係を書き換えません。APIMサブスクリプションを自動停止したり、削除したりする処理でもありません。',('effect',))
# Four observation statuses.
add('observation','statuses','mako','観測結果には、具体的にどんな状態がありますか。「不明」と「削除済み」が、特に紛らわしく感じます。')
add('observation','statuses','ikumi','四つあります。activeは現在のグループ条件を満たす状態。ineligibleは存在しますが、名前規則やセキュリティ属性などの条件を満たさない状態です。',('active','ineligible'))
add('observation','statuses','manabu','存在するだけでは、所有グループとして適切とは限らないわけですね。承認の話とは別に、現在の条件も見る必要があります。',('ineligible',))
add('observation','statuses','ikumi','deletedは、そのグループIDへのGETで、明示的に404が返った場合です。403、429、通信障害などで確かめられなければ、unknownとして残します。',('deleted','unknown'))
add('observation','statuses','mako','取得できないから削除済み、と決めない。逆にactiveでも、APIMの利用が有効だと言い切るものではないんですね。')
add('observation','statuses','ikumi','はい。これはグループの観測状態です。APIMにもactiveという状態名がありますが、対象も意味も違います。どこの状態かを添えて読むと混同を防げます。',('active',))
# Scheduled reconciliation.
add('observation','job','mako','組織の情報が変わったとき、この観測は、いつ、どれくらいの範囲で更新されるんでしょう？')
add('observation','job','ikumi','定期Jobは15分ごとです。SQLに登録されたグループから、未確認や確認日時の古いものを優先して、最大100件を選びます。多ければ次回以降へ順送りします。',('schedule','batch'))
add('observation','job','manabu','つまり、15分たてば必ずすべてが最新という約束ではないんですね。画面の状態と一緒に、確認日時を見る理由が分かりました。',('batch',))
add('observation','job','ikumi','Graphへの照会は最大4件並行です。保存時には最新の名前規則を確認し、古い時刻の観測で上書きしません。表示名を取得できないときは、前の表示名を保持します。',('parallel','save'))
add('observation','job','ikumi','結果にunknownが残ればpartial、設定やSQLの例外ならfailedとして、終了コードは1です。Jobの終了だけでなく、完了日時や確認件数、不明件数も読みます。',('result',))
# Directory investigation.
add('observation','group-check','mako','今朝からunknownのまま、という相談なら、最初にどこを見ればよいですか？ 見えている表示名は残っています。')
add('observation','group-check','ikumi','観測の確認日時とエラーを見て、Jobの実行履歴と照合します。表示名が残っていても、今回取得できた証拠にはなりません。古い値を保持している可能性があります。',('observe','job'))
add('observation','group-check','manabu','組織を削除したのか、照会できなかったのかを分けるんですね。影響するAPIMサブスクリプション数も見て、調べる対象を絞ります。',('registry',))
add('observation','group-check','ikumi','Graphの応答、実行用IDの権限、通信、SQLへの保存を順に確かめます。unknownを理由に承認や実体を勝手に変更せず、まず観測できない原因を調べます。',('graph','registry'))
# SQL/APIM comparison.
add('sync','compare','mako','次はAPIMとの同期です。これは、APIMにあるものを全部SQLへ取り込んで、一覧をそろえる機能ですか？')
add('sync','compare','manabu','この同期は、SQLの確定した内容にAPIMを合わせるために、管理者が差分を確認する機能です。表示をそろえるだけでなく、利用への影響も伴います。',('sql','apim'))
add('sync','compare','ikumi','APIMサブスクリプションIDで照合し、表示名、製品、状態、存在を比較します。通常の表示名の元は会社名です。配備で作る検証用では用途名を使います。',('id','sql','apim'))
add('sync','compare','mako','所有グループ名が変わったから、APIMの表示名も必ず同じ名前にする、ということではないんですね。')
add('sync','compare','ikumi','はい。比較する値の出所を確かめます。この同期では秘密のキー実値を比較のために取得しません。SQL台帳にも、キー実値そのものを保存するわけではありません。',('key',))
# Operation categories.
add('sync','actions','mako','一覧に差分が出たら、どの操作になるんでしょう？ 何か違っていたら、全部作り直すわけではないですよね。')
add('sync','actions','ikumi','両方に実体があり、表示名、製品、状態だけが違うなら更新です。対象になるSQLの確定値があり、APIM実体がなければ再作成の対象になります。',('update','recreate'))
add('sync','actions','manabu','更新と再作成では、呼び出す側への影響が違いますね。一覧の「不一致」という言葉だけで、作業を決めないようにします。')
add('sync','actions','ikumi','SQLで削除済みなのにAPIMが残る場合は削除です。SQLに未登録でAPIMにだけある実体も、削除の候補です。必ず対象を見て確認してから反映します。',('delete',))
add('sync','actions','mako','登録されていない実体も見えるけれど、確認する前に自動で消されるわけではないんですね。では作成途中のものは？')
add('sync','actions','ikumi','作成中や削除中などの途中状態は通常の同期対象外です。全APIを使える組み込みのmasterも対象外にします。値が一致するものは、変更なしです。',('blocked','none'))
# Consequences for callers.
add('sync','key-impact','mako','利用者へ伝える点も整理したいです。更新、再作成、削除では、それぞれ何が変わりますか？')
add('sync','key-impact','ikumi','既存実体のメタデータ更新では、主キーと副キーを維持します。ただし状態をactiveへ戻す更新なら、停止していた利用を再開し得ます。値の変更でも影響を見ます。',('update','resume'))
add('sync','key-impact','manabu','キーが変わらなければ影響なし、とは限らないんですね。停止を解除してよいかも、作業として確認する必要があります。',('resume',))
add('sync','key-impact','ikumi','再作成では、主キーも副キーも新しくなります。呼出アプリで使うキーの更新が必要です。削除では、その実体によるAPI利用ができなくなります。',('recreate','delete'))
# Preview plan.
add('apply','preview','mako','影響を分かったうえで対象を選ぶんですね。反映の確認画面には、どんな仕組みが入っていますか？')
add('apply','preview','ikumi','一度に選べるのは最大10件です。確認の時点でSQLとAPIMを再照会し、選んだ対象とそのときの状態をまとめます。一覧を見た時点の情報だけでは進めません。',('count','fresh'))
add('apply','preview','manabu','大量の差分を勢いで反映せず、自分が確認できる範囲の作業にするわけですね。確認したあと、翌日そのまま実行できますか？')
add('apply','preview','ikumi','確認内容には改変を検出する署名と、5分の期限があります。本人、テナント、固定された接続先にも結び付いているので、別の人や別の接続先へ流用できません。',('token','expiry','binding'))
add('apply','preview','mako','期限が切れたら、同じ確認画面にこだわらず、最新の一覧と確認を取り直すんですね。時間を置いた作業ほど大切そうです。',('expiry',))
# Apply safeguards.
add('apply','apply','mako','では、確認から1分で実行した場合は、もう値が変わっていないとして、そのままAPIMへ送るんでしょうか？')
add('apply','apply','ikumi','実行直前にも確かめます。管理者の所属、正本のSQL接続、署名、期限を確認し、対象のロック内でSQLとAPIMを取り直します。確認後の変更があれば、競合として止めます。',('auth','conflict'))
add('apply','apply','manabu','確認した内容と違う作業をしないためですね。直前にほかの管理者が変えたものへ、古い判断を上書きしないようにします。',('conflict',))
add('apply','apply','ikumi','そして、APIMへ操作を送る前に、開始監査をSQLへ保存します。更新はPATCH、再作成はPUT、削除はDELETEです。送った後はAPIMを再照会します。',('start','operation','verify'))
add('apply','apply','mako','送信が終わった、だけを成功とはせず、実体の状態を確かめるんですね。通信が途中で切れたら、同じ内容を押し直せばよいですか？')
add('apply','apply','ikumi','同じ確認内容の再送は拒否する仕組みです。結果が不明なら現在値と監査を確認し、新しい一覧と確認からやり直します。繰り返し押して、結果を推測しません。',('start','verify'))
# Outcomes.
add('apply','results','mako','10件のうち、数件だけ成功することもありますか？ 一つでも失敗すれば、全部元に戻ると思っていました。')
add('apply','results','ikumi','結果はサブスクリプション単位です。成功、削除待ちなどの保留、失敗を、確認した対象や開始記録と一緒に監査へ残します。全件が一括で戻る処理ではありません。',('table','success','pending','failed'))
add('apply','results','manabu','成功した対象までまとめてやり直すと、余計な操作になりますね。利用者への連絡も、件ごとの結果を根拠にします。',('success','failed'))
add('apply','results','ikumi','SQLとAPIMは別の場所です。SQL側で処理を取り消しても、すでにAPIMへ送った変更までは戻りません。再照会した現在値と監査を合わせて、残った作業を判断します。',('rollback',))
# Walkthrough.
add('investigation','case','mako','具体例で追わせてください。会社名を変えたあと、APIMだけ旧会社名のまま。どこから確認しますか？')
add('investigation','case','ikumi','これは教材上の例です。同じAPIMサブスクリプションIDかを確かめ、SQLの会社名と確定状態、APIMの現在の表示名と状態を比べます。名前だけで別物と判断しません。',('identity','sql','apim'))
add('investigation','case','manabu','両方ともactiveで、表示名だけの差なら更新ですね。呼出アプリへ新しいキーを配る作業まで、必要になるんでしょうか？')
add('investigation','case','ikumi','この条件なら既存実体の表示名更新で、主キーと副キーを維持します。対象と内容を確認して反映し、APIMの再照会と同期監査で結果を確かめます。',('action','verify'))
add('investigation','case','mako','同じ会社名変更でも、APIM実体が本当に消えているなら再作成の判断になり、キーの扱いも変わるんですね。状態ごとに選びます。')
# Error vs absence.
add('investigation','unavailable','mako','では、APIMの一覧を取得できなかったときは、実体がないとして再作成の候補を出してよいですか？')
add('investigation','unavailable','ikumi','取得失敗は空の一覧ではありません。SQL一覧、APIM一覧、監査の取得は、それぞれ取得不能としてエラーにします。必要な一覧を完全に取得してから、存在を比較します。',('sql','apim','audit'))
add('investigation','unavailable','manabu','情報がないのか、情報を読めないのかで、次の行動が変わるんですね。不足した情報を、変更操作で埋めようとしないようにします。')
add('investigation','unavailable','ikumi','SQLなら接続先と権限、APIMなら接続と実行用IDの権限や応答、監査なら参照先を確かめます。取得を回復して対象IDを照合してから、再作成や削除を判断します。',('sql','apim','audit','missing'))
# Maintenance boundaries.
add('maintenance','change-input','mako','調査の結果、Portalの機能を直す必要が出たら、どこまで一緒に見直しますか。画面の項目だけ直せばよい場合もありますよね。')
add('maintenance','change-input','ikumi','表示はcomponentsと翻訳を見ます。入力項目なら、契約、HTTP受付、サービス、保存処理、SQLまでたどります。画面だけ変えても、受付や保存の対応は自動で増えません。',('display','input'))
add('maintenance','change-input','manabu','利用を認める条件を変えるなら、一覧に表示できるかだけでなく、実際の操作を許可してよいかまで確認する必要がありますね。',('permit','last'))
add('maintenance','change-input','ikumi','はい。APIキーのサービス、Graph照会、グループの条件、承認設定を一緒に確認します。実行直前の再確認も対象です。表示時と実行時で条件が食い違わないようにします。',('permit','last'))
add('maintenance','change-store','mako','SQLの列やAPIMへの操作を変える場合も、同じように、保存先と外部側までつなげて見ますか？')
add('maintenance','change-store','ikumi','SQLなら定義、利用する保存処理、移行スクリプトに加え、実行用権限を確認します。列や表を作っただけでは、Portalが使うIDへ必要な権限は付きません。',('sql','grant'))
add('maintenance','change-store','manabu','開発者の手元で動くことと、配備したIDで動くことは、別に確かめる必要があるんですね。',('grant','identity'))
add('maintenance','change-store','ikumi','APIM操作ならクライアントの処理と、影響するAPI定義、製品、ポリシー、実行用IDの権限を見ます。配備やJobなら、SQL準備からアプリ更新までの順序も確認します。',('apim','identity','deploy'))
# Verification of changes.
add('maintenance','tests','mako','修正後のテストは、何を確かめればよいですか。通常のテストが全部成功したら、SQLの保存も確認できたと言えますか？')
add('maintenance','tests','ikumi','HTTPやサービス、外部APIのテストでは、入力の拒否、許可条件、送る要求、失敗時の動きを確かめます。実SQLの保存、競合、権限は、SQL用テストの担当です。',('http','service','sql'))
add('maintenance','tests','manabu','試験ごとに確かめられる範囲を分けるんですね。成功という結果だけでなく、どの確認を実際に実行したかが大事です。')
add('maintenance','tests','ikumi','SQL用テストは通常の実行では省略されます。ローカルSQL専用の明示設定で実行する仕組みです。今回の動画も、稼働環境で操作や検証を実行した報告ではありません。',('run',))
# Three quick checks, no answer highlight on the questions.
add('check','quiz1','mako','確認問題です。Graphから403が返り、所有グループが不明になりました。実体を削除する、unknownを調べる、新規承認する。どれを選びますか？')
add('check','quiz1','manabu','unknownを調べる、を選びます。取得できないことから、削除済みとも、承認が必要とも決められません。まず観測日時、エラー、Jobの結果を見ます。',('b',))
add('check','quiz1','ikumi','そのうえでGraphの権限や通信を確認します。観測JobはAPIM実体を自動で止めません。組織の観測と利用を変える操作を分ける、という判断です。',('b',))
add('check','quiz2','mako','二問目です。SQLには対象になる確定値があり、完全取得したAPIM一覧に対象がありません。再作成するなら、呼出アプリには何を伝えますか？')
add('check','quiz2','manabu','キーが変わることを伝えます。表示名の更新とは違って、再作成では主キーも副キーも新しくなるので、利用する側の更新が必要です。',('b',))
add('check','quiz2','ikumi','正解です。取得不能で一覧が見えない場合とは前提が違います。不存在を確かめ、再作成の影響を理解してから、対象を選んで確認します。',('condition','b'))
add('check','quiz3','mako','三問目です。APIMへ反映したあと、SQLの保存で例外が起きました。SQLを取り消せば、APIMも必ず元に戻ると言えますか？')
add('check','quiz3','ikumi','言えません。APIMの現在値と開始監査を再確認します。外部操作はすでに反映されている可能性があり、SQLの取消だけで、その変更まで戻るわけではありません。',('b',))
add('check','quiz3','manabu','結果不明のまま繰り返さず、件ごとに確かめるんですね。成功、保留、失敗を根拠に、残った作業と利用者への連絡を決めます。',('b',))
# Wrap and bridge.
add('summary','summary','mako','今日は、状態がそろわないとき、どこを見ればよいかを整理しました。最初の「不明ならすぐ停止」という疑問も、分けて判断できそうです。')
add('summary','summary','manabu','組織の承認と現在の観測を分け、取得不能を不存在と混同しない。次に、SQLの登録とAPIMの利用実体をIDで比べ、利用への影響を判断します。',('observe','sync'))
add('summary','summary','ikumi','反映は、内容の確認、実行直前の再確認、開始監査、APIMの再照会まで追います。変更する際は、入力、保存、外部設定、権限とテストを一続きで確認します。',('apply','change'))
add('summary','summary','mako','これでPortalの管理を一巡しました。次回は、APIキーだけでは本人を区別できない理由です。共有のキーと、一人ひとりの接続は、どう結び付くんでしょう。')
add('summary','summary','manabu','利用の許可と本人の接続を分けるところから、Credential Brokerの役割へ進みます。今回はここまでです。')
s['turns']=turns
(P/'script.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(turns),'captionChars':sum(len(t['caption']) for t in turns),'speechChars':sum(len(t['speech']) for t in turns),'viewIds':len(V),'chunks':sum(len(t['subtitleChunks']) for t in turns)},ensure_ascii=False))
