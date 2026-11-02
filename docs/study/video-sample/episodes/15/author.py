"""Author episode 15 only; run from any directory. No network calls."""
import copy
import json
import re
from pathlib import Path

P = Path(__file__).resolve().parent
s = json.loads((P / 'script.json').read_text())
s['status'] = 'draft'
s['firstCut']['omitTurnIds']=['lesson15-turn-051-mako']
s['editorialNote']='ディスク不足で結果を保存できなかった導入051は再送せず省略。未送信052に状況説明を加え、範囲を維持する。'
s['description'] = 'APIキーの発行を、入力と許可の確認、SQL予約、APIM作成、SQL確定・監査へ結び付ける。'
chapter_data = [
 ('opening','今回の問い','発行ボタンの後に何が残るかを問う'),
 ('purpose','発行でそろえる二つの状態','台帳とAPIM実体を区別する'),
 ('input','入力と現在の利用許可','入力・現在所属・登録済みの承認を照合する'),
 ('reserve','SQLへ作成中の予約を残す','重複・発行枠・会社を確認してcreatingを確定する'),
 ('apim','APIMの照会から確定へ','GET・不在時PUT・結果照合・activeと監査を追う'),
 ('recovery','途中で失敗したときの再開','同じ依頼IDと同じ入力で再照会する理由を説明する'),
 ('case','一つの発行を確認先へ結び付ける','架空例で入力から完了までを追う'),
 ('check','三つの確認問題','状態・再試行・秘密値の誤解を確認する'),
 ('summary','発行の順番と次の問い','確認・予約・作成・確定を振り返る'),
]
s['chapters'] = [{'id':i,'title':title,'goal':goal,'sourceRefs':[{'file':'../03-subscription-management.html','anchor':'key-create'}]} for i,title,goal in chapter_data]
menu=['発行入力','許可の確認','SQL予約','APIM作成','確定と監査','再試行']
views={}
def base(key,title,section,notice):
 return {'layout':'settings','viewId':'lesson15-'+key,'areaLabel':'Portal のサービスと管理','title':title,'kind':'APIキー発行','section':section,'menu':menu,'notice':notice,'subtitle':'リポジトリの定義に基づく教材用ビュー','footer':'実環境の取得画面ではない。会社・チーム・用途の例は架空。'}
def fields(key,title,section,rows,notice):
 v=base(key,title,section,notice);v['fields']=[{'id':i,'label':l,'value':value} for i,l,value in rows];views[key]=v
def table(key,title,section,rows,notice,width=365):
 v=base(key,title,section,notice);v['table']={'columns':[{'label':'確認する対象','width':width},{'label':'役割・保存する意味','width':972-width}],'rows':[{'id':i,'cells':[l,value]} for i,l,value in rows]};views[key]=v
fields('states','発行完了は、台帳と実体の対応で確認','確定と監査',[
 ('portal','Portal','本人・入力・許可を確認し、処理をつなぐ'),('sql','SQL','会社・所有・用途・状態・発行監査'),('apim','APIM','製品への利用許可と、主キー・副キー'),('result','発行の結果','SQL active と APIMの対応を確認')],'APIMサブスクリプションは、API製品への利用許可')
table('tables','発行に関わる五つの表','SQL予約',[
 ('group','portalApiKeyGroup','所有グループの承認・Graph観測状態'),('product','portalApiKeyProduct','グループごとの製品承認・発行上限'),('key','portalApiKey','用途・会社・所有・ID・依頼ID・状態'),('company','portalCompany','費用帰属に使う会社の分類'),('audit','portalApiKeyAudit','発行などの操作履歴')],'秘密のキー実値は、この五つの表へ保存しない')
fields('input','発行フォームで登録する情報','発行入力',[
 ('owner','所有グループ','現在所属し、利用が承認されたグループ'),('company','会社','有効な会社を選択：費用の帰属先'),('purpose','用途名・クライアント種別','用途と利用元の分類を登録'),('optional','システム名・チーム名','任意の補足属性'),('contact','連絡先','棚卸し・問い合わせ先'),('product','通常発行の製品','released-apis：サーバー側で固定')],'所有と操作権限、会社への費用帰属を分ける')
fields('permission','表示時と操作時の許可を分けて確認','許可の確認',[
 ('identity','本人と要求','セッション本人・Origin・本文形式とサイズ'),('contract','入力の契約','必須項目・形式・許可する項目を検査'),('graph','Graphの現在情報','グループの適格性と、本人の現在所属'),('approval','SQLの承認','グループ・製品が有効で、発行が許可される'),('unknown','照会が不明','確認不能として発行を進めない')],'承認は登録済み設定の照合。発行ごとの人的承認ではない')
fields('reserve','SQL予約で重複と発行枠をそろえる','SQL予約',[
 ('request','同じ依頼','本人と依頼IDに対応する既存行を確認'),('same','再試行の入力','同じ入力なら予約を再利用。変更なら競合'),('cap','グループ＋製品の発行枠','削除済み以外の台帳行を数える'),('company','有効会社','予約時にも有効性を確認'),('commit','予約の確定','creating と対応IDを先に保存')],'予約の確定は、APIMの作成完了を意味しない')
fields('identifiers','依頼ID・台帳ID・APIM IDを分ける','SQL予約',[
 ('request','依頼ID','同じ発行依頼の再試行を識別'),('ledger','台帳ID：id','SQLの対象行を識別'),('apim','apimSubscriptionId','APIM側を照会・操作するためのID'),('new','通常の新規発行','台帳IDとAPIM IDに同じ値を保存'),('secret','主キー・副キー','API利用時の秘密値。上のIDとは別')],'IDの役割を分ける。同じ値でも同じ意味とは限らない')
fields('apim','対象を照会し、不在の場合だけ作成','APIM作成',[
 ('lock','排他と再確認','対象行をロックし、現在所属・承認を再確認'),('get','GET','保存済みAPIM IDで存在を照会'),('put','不在のときだけPUT','同じID・製品・会社名・activeで作成'),('verify','結果の照合','ID・製品scope・表示名・active状態'),('commit','SQLの確定','active更新とcreate監査を同時に確定')],'既存のAPIM実体を、再試行で無条件に上書きしない')
fields('names','用途名とAPIM表示名の確認先','APIM作成',[
 ('usage','SQLの用途名','利用者が入力する利用目的'),('name','通常のAPIM表示名','会社マスタの会社名'),('scope','製品scope','released-apis に対応するスコープ'),('state','APIMの状態','active であることを照合')],'似た名前の項目でも、照合する意味と保存先が違う')
fields('finish','active と発行監査を一緒に確定','確定と監査',[
 ('before','確定の前提','APIMのID・製品・表示名・状態を照合済み'),('state','SQL台帳','creating から active へ更新'),('audit','発行監査','create 操作・操作者・状態・日時'),('transaction','SQLの同じ処理単位','台帳更新と監査を一緒に確定'),('secret','秘密の保存先','主キー・副キーはAPIM。発行応答に含めない')],'SQLの取消だけでは、成功済みAPIM操作は戻らない')
fields('retry','同じ依頼を再開するために保つもの','再試行',[
 ('same','同じ依頼ID＋同じ入力','保存済みの予約とAPIM IDへ戻る'),('new','別の依頼ID','別の発行要求になり得る'),('changed','同じ依頼ID＋違う入力','競合として扱う'),('recheck','再確認','現在所属・承認・APIM実体を照合'),('unknown','応答を受け取れないとき','失敗と決め付けず、保存先ごとに確認')],'画面の通信結果と、SQL・APIMの保存状態を分けて見る')
fields('case','架空例：A社向け分析バッチのキー','発行入力',[
 ('owner','所有','分析チーム：操作する本人は現在メンバー'),('company','費用帰属','A社：有効な会社として登録済み'),('usage','用途','月次分析バッチ：custom-system'),('permission','製品の利用','released-apis 承認済み、発行枠あり'),('request','依頼','入力と依頼IDを保って発行を追う')],'架空例。実在する会社・グループ・キーを示すものではない')
table('files','途中で止まった処理と担当ファイル','再試行',[
 ('form','create-key-form.ts','入力の同一性と依頼IDの再利用'),('http','http.ts / contracts.ts','本人・要求の検査と入力契約'),('service','service.ts','認可から予約・APIM・確定への順序'),('repository','repository.ts','予約・排他・発行枠・状態・監査'),('apim','apim.ts / graph.ts','APIM管理APIと、Graph現在情報の照会')],'止まった段階に対応する担当ファイルを確認する')
fields('quiz-state','確認1：creating から分かること','再試行',[
 ('question','問い','creating ならAPIMは必ず未作成か？'),('a','選択肢A','はい。APIMはまだ存在しない'),('b','選択肢B','いいえ。APIM作成済みの場合もある'),('c','選択肢C','active の別名なので発行完了である')],'予約の状態と、APIM実体の存在を分けて考える')
fields('quiz-retry','確認2：APIM成功後にSQL確定が失敗','再試行',[
 ('question','問い','どの依頼を使って再開する？'),('a','選択肢A','必ず別の依頼IDで新しく発行する'),('b','選択肢B','同じ依頼IDと同じ入力で再試行する'),('c','選択肢C','SQLを取り消せばAPIMも消える')],'保存した予約と、APIM側の状態を対応付けて考える')
fields('quiz-secret','確認3：発行応答に秘密キーはある？','確定と監査',[
 ('question','問い','SQL active なら主キーもSQLにある？'),('a','選択肢A','主キーはSQLに保存し、発行応答にも含める'),('b','選択肢B','SQLは台帳・監査。実値はAPIMで保持'),('c','選択肢C','依頼ID・台帳ID・主キーは、同じ秘密値')],'保存目的を手掛かりに答える')
fields('summary','入力から発行履歴までを一本につなぐ','確定と監査',[
 ('check','確認','本人・入力・現在所属・登録済みの承認'),('reserve','予約','重複・発行枠・有効会社を確認しcreating'),('apim','作成と照合','GET → 不在ならPUT → ID・scope・状態確認'),('finish','確定と監査','SQL active とcreate監査を一緒に確定'),('retry','再開','同じ依頼ID・同じ入力で同じ実体を確認')],'次回：APIの仕様を調べ、発行したキーでAPIを試す')
for k in ['issue-flow','recovery-flow']:
 views[k]={'layout':'svg','viewId':'lesson15-'+k,'svg':'diagrams/'+k+'.svg'}

replacements = [
 ('APIMサブスクリプションID','エーピーアイエム サブスクリプション アイディー'),('APIMサブスクリプション','エーピーアイエム サブスクリプション'),
 ('apimSubscriptionId','エーピーアイエム サブスクリプション アイディー'),('released-apis','リリースド エーピーアイズ'),('custom-system','カスタム システム'),
 ('APIキー','エーピーアイキー'),('APIM','エーピーアイエム'),('API','エーピーアイ'),('SQL','エスキューエル'),('Portal','ポータル'),('Graph','グラフ'),('Entra','エントラ'),
 ('GET','ゲット'),('PUT','プット'),('Origin','オリジン'),('JSON','ジェイソン'),('active','アクティブ'),('creating','クリエイティング'),('create','クリエイト'),('scope','スコープ'),('ID','アイディー'),
]
replacements.sort(key=lambda pair:-len(pair[0]))
def chunks(text):
 # Pack clauses, retaining technical terms and splitting long clauses after particles.
 units=re.findall(r'.*?[、。？！]|.+$',text)
 lines=[];line=''
 for unit in units:
  if len(unit)>28:
   if line:lines.append(line);line=''
   while len(unit)>28:
    tokens=re.findall(r'APIMサブスクリプション(?:ID)?|クライアント種別|[A-Za-z0-9][A-Za-z0-9_.+/-]*|.',unit)
    candidates=[];length=0;lastfit=0
    for token in tokens:
     length+=len(token)
     if length>28:break
     lastfit=length
     if length>=12 and token in ['は','が','を','に','と','で','へ']:candidates.append(length)
    cut=candidates[-1] if candidates else max(1,lastfit)
    lines.append(unit[:cut]);unit=unit[cut:]
  if line and len(line)+len(unit)>28:lines.append(line);line=''
  line+=unit
  if unit.endswith(('。','？','！')):lines.append(line);line=''
 if line:lines.append(line)
 return [{'caption':''.join(lines[i:i+2]),'lines':lines[i:i+2],'weight':len(''.join(lines[i:i+2]))} for i in range(0,len(lines),2)]
turns=[]
def t(who,chapter,text,view=None,hi=()):
 v=copy.deepcopy(views[view]) if view else {'layout':'hologram','heading':s['topic']}
 if v['layout']=='settings':v['highlightRows' if 'table' in v else 'highlightFields']=list(hi)
 speech=text
 # One substitution pass avoids replacing API inside an already expanded APIM pronunciation.
 pattern='|'.join(re.escape(a) for a,b in replacements)
 speech=re.sub(pattern,lambda m:dict(replacements)[m.group()],speech)
 cc=chunks(text)
 turns.append({'id':f'lesson15-turn-{len(turns)+1:03}-{who}','speaker':who,'chapterId':chapter,'speech':speech,'caption':text,'subtitleLines':cc[0]['lines'],'subtitleChunks':cc,'gapAfterSeconds':.14,'delivery':{'mako':'視聴者の疑問を相手に確かめる。直前の返答を受け、問いと納得を自然に表す。','manabu':'操作の目的と判断の理由を、同僚への返答として落ち着いて伝える。','ikumi':'直前の問いの焦点に答え、処理の順番と確認先を明瞭に説明する。'}[who],'visual':v})

t('mako','opening','APIキーの発行ボタンを押したあと、画面がエラーになりました。もう一度押したら、キーが二つできてしまうんでしょうか。')
t('manabu','opening','気になりますよね。画面に返事が来たかどうかと、裏側で何が保存されたかは、分けて確認する必要があります。')
t('ikumi','opening','今日は入力の確認から、SQLの予約、APIMの作成、台帳の確定まで追います。同じ依頼を途中から再開できる仕組みが、今回の鍵です。')
t('mako','purpose','まず発行完了とは、何がそろった状態なんでしょう。秘密の文字列を作って返せば、それで終わりではないんですね。','states')
t('manabu','purpose','はい。誰が管理し、どの会社の利用で、何の用途かを後から確認できることが必要です。利用許可の実体と、その管理台帳の対応をそろえます。','states',['sql','apim'])
t('ikumi','purpose','APIMでは、APIをまとめた製品に対する利用許可を、APIMサブスクリプションとして作ります。SQLには所有や会社、状態などの台帳と、発行の履歴を残します。','states',['sql','apim'])
t('mako','purpose','ここでいうサブスクリプションは、Azureの課金やリソース管理の単位ではなく、APIMのAPI利用許可ですね。Portalが両方の処理をつなぐわけですか。','states')
t('ikumi','purpose','その通りです。Portalが本人と入力を確認し、SQLとAPIMを順に呼びます。主キーと副キーの実値はAPIMが保持し、SQLは管理の情報を保持します。','states',['portal','sql','apim'])
t('mako','purpose','図の四つの段階を追ってみましょう。確認、予約、APIM作成、SQL確定。APIMへ行く前に、SQLへ予約を残すのが特徴ですね。','issue-flow')
t('ikumi','purpose','先に作成中の行を確定すると、処理が途中で止まっても、同じ依頼に対応するIDへ戻れます。最後はAPIMの結果を照合し、SQLの利用可能状態と監査を確定します。','issue-flow')
t('manabu','purpose','発行ボタンの裏で一回だけ全部が保存される、と考えると、障害時に判断を誤ります。どこまで保存されたかを、段階に沿って説明できるようにしましょう。','issue-flow')
t('mako','input','その前に、発行フォームで何を登録するかを確認します。所有グループと会社は、どちらも組織に見えますが、目的は違うんでしたね。','input')
t('manabu','input','所有グループは、キーをチームで管理するためです。会社は、その利用の費用をどこへ帰属させるかという分類です。会社を選んだだけで、管理権限は得られません。','input',['owner','company'])
t('ikumi','input','用途名とクライアント種別、連絡先も登録します。システム名とチーム名は任意の補足です。棚卸しや問い合わせで、何の利用か、誰へ確認するかをたどる情報になります。','input',['purpose','optional','contact'])
t('mako','input','クライアント種別は利用元の分類ですね。例えば自作のシステムを選ぶと、それだけで特別なAPIの権限が付く、という意味ではありませんか。','input')
t('ikumi','input','はい、統計のための分類で、所有権や認可を決める項目ではありません。通常の発行では、製品はサーバー側でreleased-apisに固定し、任意の製品を入力させません。','input',['purpose','product'])
t('mako','input','フォームに候補が表示されていても、送信された情報をそのまま信じるわけではない。入口ではどんな確認をしますか。','permission')
t('ikumi','input','本人のセッション、送信元、本文の形式とサイズ、入力の契約を検査します。ブラウザーが送った名前から本人を作るのではなく、確認済みの本人情報を使います。','permission',['identity','contract'])
t('mako','input','ログインが確認できたら、あとは会社と用途が埋まっていればよさそうにも見えます。まだ確認する条件があるんですね。','permission')
t('ikumi','input','Graphでグループの適格性と本人の現在所属を確認します。SQLに登録されたグループと製品の承認も照合します。ログインできることと、そのグループで発行できることは別です。','permission',['graph','approval'])
t('manabu','input','ここでいう承認は、登録済みの設定を確認することです。発行のたびに人へ申請を回して、返事を待つ承認ワークフローとして説明しないようにしましょう。','permission',['approval'])
t('mako','input','もしGraphの照会に失敗して、所属が分からなかったら、前に見えた候補を使って発行を続けるんでしょうか。','permission')
t('ikumi','input','続けません。確認不能として止めます。不明という結果を、所属していない、あるいはグループが削除されたという結果に置き換えないことも大切です。','permission',['unknown'])
t('mako','reserve','次は保存するデータです。この五つの表は、同じような名前でも、何を記録しているかを分けて見るんですね。','tables')
t('ikumi','reserve','グループの表は、所有グループの承認とGraphで観測した状態です。製品の表は、グループごとに使える製品と、発行できる数の上限を記録します。','tables',['group','product'])
t('manabu','reserve','キー台帳には用途、所有、会社、対応するIDと状態が残ります。会社の表は費用帰属の分類、監査の表は操作の履歴です。秘密値の置き場を五つに分散する話ではありません。','tables',['key','company','audit'])
t('mako','reserve','予約の中では、まず同じ依頼が来ていないかを見るんですか。同じ人が同じフォームを再送したときの扱いを教えてください。','reserve')
t('ikumi','reserve','本人と依頼IDに対応する既存の行を確認します。同じ入力ならその予約を再利用しますが、所有グループや会社、用途などを変えて同じ依頼IDを送ると、競合になります。','reserve',['request','same'])
t('manabu','reserve','同じ依頼の続きなのか、新しい発行なのかを識別するためです。画面の再試行だけで新しい許可が増えると、利用者も管理者も状況を追いにくくなりますからね。','reserve',['request'])
t('mako','reserve','発行上限は、今使えるactiveのキーだけを数えますか。それなら作成中や停止中は、枠から外れていそうですが。','reserve')
t('ikumi','reserve','いいえ。グループと製品ごとに、削除済み以外の台帳行を数えます。作成中、停止中、削除処理中も含みます。同時発行で上限を超えないよう、枠の確認も排他的に行います。','reserve',['cap'])
t('mako','reserve','会社が有効かどうかも、候補を表示したときだけでなく、この予約の段階で確認する。確認のあとに会社が無効になる場合も考えるわけですね。','reserve')
t('ikumi','reserve','はい。予約の処理で会社の行もロックして有効性を確認します。条件がそろうと、対応するIDと入力を持ったcreatingの行を保存し、予約を先に確定します。','reserve',['company','commit'])
t('mako','reserve','IDがいくつか出てきたので整理しましょう。依頼ID、SQLの台帳ID、APIMサブスクリプションIDは、それぞれ何のためにありますか。','identifiers')
t('ikumi','reserve','依頼IDは同じ発行要求を識別します。台帳IDはSQLの対象行、APIMサブスクリプションIDはAPIMの実体を識別します。秘密の主キーや副キーとは、どれも役割が違います。','identifiers',['request','ledger','apim','secret'])
t('mako','reserve','それなら台帳IDとAPIM側のIDは、値も必ず別になるんですか。見比べたときに同じなら、不具合だと思ってよいのでしょうか。','identifiers')
t('ikumi','reserve','通常の新規発行では、両方に同じ値を保存します。ただし別の項目として保持し、APIMの照会には保存済みのAPIM側IDを使います。値が同じでも、役割まで一つにしないんですね。','identifiers',['ledger','apim','new'])
t('manabu','reserve','依頼をたどるためのIDと、管理する対象をたどるIDを分けておくと、再送や照合の説明がつながります。IDそのものをAPI利用時の秘密値として扱わないことも確認してください。','identifiers',['request','secret'])
t('mako','apim','予約が残ったら、次にAPIMの作成へ進みます。SQLの行があるので、所属の確認はもう終わりでよいのでしょうか。','apim')
t('ikumi','apim','対象の台帳をロックしたあと、現在所属と承認をもう一度確認します。そのうえで保存済みのAPIM IDをGETし、すでに同じ実体が存在するか調べます。','apim',['lock','get'])
t('mako','apim','いきなりPUTするのではなく、先にGETする。前の試行で、APIMの作成だけは終わっている可能性があるからですね。','apim')
t('ikumi','apim','その通りです。不在なら同じIDで作成します。すでに存在すれば、その結果を使って照合します。再試行のたびに別のIDで作ったり、既存の実体を無条件に上書きしたりしません。','apim',['get','put','verify'])
t('manabu','apim','作成済みかどうかだけでなく、今回の予約に対応する利用許可なのかが必要です。別の製品や違う状態の実体を、見つかったから成功と扱うわけではありません。','apim',['verify'])
t('mako','apim','照合する表示名も少し注意が必要そうです。フォームの用途名が、APIMにそのまま表示されると考えてよいですか。','names')
t('ikumi','apim','通常のAPIM表示名は会社マスタの会社名です。SQLの用途名は、利用者が入力した利用目的です。二つを混同せず、ID、製品のスコープ、表示名、active状態を照合します。','names',['usage','name','scope','state'])
t('mako','apim','APIMの結果が合っていたら、最後にSQLを利用可能へ変える。ここで発行監査も残るんですね。','finish')
t('ikumi','apim','はい。台帳をactiveへ更新し、createの監査を同じSQLの処理単位で確定します。操作した本人や状態、日時を後から確認できるよう、台帳と履歴をそろえます。','finish',['state','audit','transaction'])
t('manabu','apim','発行完了を、管理対象と履歴の両方から説明できる状態にするんです。ただし、このSQLの確定と、外部のAPIM操作までが一つの取消可能な処理になるわけではありません。','finish',['transaction'])
t('mako','apim','発行応答では、その場で主キーと副キーも返しますか。台帳が完成したなら、秘密値も一緒に渡しそうに見えます。','finish')
t('ikumi','apim','発行応答には秘密値を含めません。主キーと副キーはAPIMが生成して保持し、キー表示やAPI試用など必要な別の処理で取得します。SQL台帳へ実値は保存しません。','finish',['secret'])
t('mako','recovery','ここから、最初のエラーの話へ戻ります。図ではAPIMの作成は成功したのに、最後のSQL確定で失敗しています。何が残るんでしょう。','recovery-flow')
t('ikumi','recovery','APIM作成のあと、SQLの確定で失敗した場合です。先に確定したSQLの予約がcreatingとして残り、APIM側には作成済みの実体が残り得ます。後段のSQLを取り消しても、成功したAPIM操作まで巻き戻るわけではありません。','recovery-flow')
t('mako','recovery','つまりcreatingという表示だけで、APIMはまだ未作成だ、と決め付けてはいけない。SQLの状態と、APIMの存在は分けて確認するんですね。','recovery-flow')
t('ikumi','recovery','はい。同じ依頼IDと同じ入力で再試行し、保存済みのAPIM IDをGETします。作成済みの結果が条件に合えば、SQLのactive更新と発行監査を確定する段階へ進めます。','recovery-flow')
t('manabu','recovery','この図の二つの保存先を見てください。片方の失敗を、両方が何もしていない状態だと解釈しないことです。残った予約が、同じ処理へ戻るための手掛かりになります。','recovery-flow')
t('mako','recovery','画面でタイムアウトした場合も、必ず発行に失敗したとは言えませんか。成功の返事だけを受け取れなかった可能性もありますよね。','retry')
t('ikumi','recovery','その可能性もあります。同じ入力には同じ依頼IDを再利用するよう、フォーム側で扱います。再試行でも現在の許可と保存状態を確認するので、前の許可を無条件に使うわけではありません。','retry',['same','recheck','unknown'])
t('mako','recovery','早く直したくて、用途名を少し変えて同じ依頼IDで送ったらどうでしょう。入力が変わっても、前の続きを探してくれますか。','retry')
t('ikumi','recovery','その組み合わせは競合になります。別の依頼IDを発行すれば、新しい発行要求になり得ます。障害からの再開と、新しいキーの発行を、同じ操作として混ぜないことが大切です。','retry',['new','changed'])
t('manabu','recovery','問い合わせでは、どの依頼で、どの台帳とAPIM実体に対応するのかをそろえます。画面のエラー文だけで新規発行を重ねず、段階と保存先を確認する判断につなげましょう。','retry',['same','unknown'])
t('mako','case','架空の例で通してみます。A社向けの月次分析バッチ用に、分析チームがキーを発行します。本人はチームの現在メンバー、製品は承認済みで枠もあります。','case')
t('manabu','case','A社は費用の帰属先、分析チームはキーの所有者です。月次分析バッチという用途と連絡先を登録して、後から何の利用かをたどれるようにします。','case',['owner','company','usage'])
t('mako','case','この入力を送ると、確認のあとSQLに予約が残り、APIMを照会する。図の順番を一つずつたどってください。','issue-flow')
t('ikumi','case','まず現在所属と登録済みの承認、入力を確認します。予約では重複、有効会社、発行枠を確認してcreatingを確定。対象の排他と再確認のあと、同じAPIM IDをGETします。','issue-flow')
t('ikumi','case','不在なら、製品と会社名、active状態を指定して作成します。結果のIDや製品スコープ、表示名と状態が合えば、SQLをactiveへ更新し、発行監査と一緒に確定します。','issue-flow')
t('mako','case','途中で止まった相談を受けたら、どのファイルから追えばよいでしょう。全部のソースを最初から読むのは大変ですよね。','files')
t('ikumi','case','要求の入口と入力契約、順序を決めるサービス、予約と監査を扱うリポジトリー、APIMとGraphの照会へ分けます。依頼IDの再利用はフォーム側も確認します。','files',['form','http','service','repository','apim'])
t('manabu','case','今どの段階かを先に決めると、確認先を選べます。SQLの予約がある、APIMは作成済み、最後の確定が未完了、と具体的に言えれば、単なる発行エラーより状況が伝わります。','files')
t('mako','check','確認問題です。台帳がcreatingなら、APIMサブスクリプションは必ず未作成。Aはその通り、Bは作成済みの場合もある、Cは発行完了の別名。どれでしょう。','quiz-state')
t('ikumi','check','Bです。creatingは予約の状態です。APIMが未作成の場合も、作成済みで後段のSQL確定が終わっていない場合もあります。実体は保存済みのIDで照会して判断します。','quiz-state',['b'])
t('manabu','check','SQLの状態を、APIMまで含めた一つの成功や失敗に読み替えない。二つの保存先を分けて考えることが、この問題のポイントです。','quiz-state',['b'])
t('mako','check','二問目です。APIMは成功し、SQLの最後の確定で失敗しました。Aは別の依頼IDで発行、Bは同じ依頼IDと入力で再試行、CはSQL取消でAPIMも消える。どれですか。','quiz-retry')
t('ikumi','check','Bです。同じ予約へ戻ってAPIMを再照会し、条件を確認して確定を続けます。SQLの取消では成功済みのAPIMは戻らず、別の依頼IDは新規発行になり得ます。','quiz-retry',['b'])
t('mako','check','最後です。SQLがactiveになったら、主キーもSQLに保存されていて、発行応答に含まれる。そう考えてよいでしょうか。','quiz-secret')
t('ikumi','check','いいえ。SQLは台帳と監査、APIMは主キーと副キーの実値を保持します。発行応答に秘密値は含めず、必要な表示や試用の処理でAPIMから取得します。','quiz-secret',['b'])
t('mako','summary','最初の問いへ戻りましょう。発行画面がエラーでも、何も保存されていないとは限らない。同じ依頼と、二つの保存先を追う必要があるんですね。','summary')
t('manabu','summary','はい。誰が管理し、どの会社の何の利用かを台帳に残し、対応する利用許可をAPIMに作ります。発行履歴までそろえて、後から状況を確認できることが目的です。','summary',['reserve','finish'])
t('ikumi','summary','本人、入力、現在所属、承認を確認し、SQL予約、APIM照会と作成、SQL確定と監査の順です。途中で止まれば、同じ依頼IDと同じ入力で、保存済みの実体を再確認します。','summary',['check','reserve','apim','finish','retry'])
t('mako','summary','次回は、使いたいAPIの仕様を調べて、発行したキーでAPIを試します。カタログと試用の経路、IDと秘密キーの役割を、実際の操作へ結び付けていきましょう。','summary')

s['turns']=turns
s['sourceReferences']=[{'file':'../03-subscription-management.html','anchor':'key-create'}]+[{'file':'../../../apps/portal/'+f} for f in [
 'components/api-keys/create-key-form.ts','components/api-keys/create-key-dialog.tsx','lib/api-keys/http.ts','lib/api-keys/contracts.ts','lib/api-keys/service.ts','lib/api-keys/repository.ts','lib/api-keys/apim.ts','lib/api-keys/graph.ts']]
(P/'script.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(turns),'captionChars':sum(len(t['caption']) for t in turns),'speechChars':sum(len(t['speech']) for t in turns),'views':len(views),'openingChars':sum(len(t['caption']) for t in turns if t['chapterId']=='opening')},ensure_ascii=False))
