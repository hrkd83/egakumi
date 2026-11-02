from pathlib import Path
import json,re,copy
P=Path(__file__).resolve().parent
s=json.loads((P/'script.json').read_text())
s.update(description='Portalの操作を、本人認証・グループ所有・費用帰属に分け、Portal・SQL・APIMの確認先へ結び付ける。',status='ready-for-generation')
ref=lambda anchor:{'file':'../03-subscription-management.html','anchor':anchor}
s['chapters']=[{'id':i,'title':t,'goal':g,'sourceRefs':[ref(a)]} for i,t,g,a in [
('opening','今回の問い','会社と管理権限を区別する入口を作る','purpose'),
('roles','Portal・SQL・APIMの分担','操作の入口、台帳、キー実値の担当を説明する','purpose'),
('signin','本人とログイン状態','Entraの本人確認とSQLセッションを結び付ける','signin'),
('ownership','所有グループと管理対象','現在所属とSQLの登録を照合する理由を説明する','signin'),
('company','会社と費用帰属','会社の登録・変更・選択停止の意味を区別する','company'),
('case','一つのキーを三つの軸で見る','本人・所有グループ・費用帰属を具体例へ適用する','signin'),
('check','確認問題','役割と操作の効果を自分の言葉で判断する','company'),
('summary','まとめと次回の問い','確認順序をまとめAPIキー発行へつなぐ','purpose')]]
menu=['役割分担','本人認証','所有グループ','会社情報','確認問題','まとめ']
views={}
def fields(k,title,section,rows,notice):
 views[k]={'layout':'settings','viewId':'lesson14-'+k,'areaLabel':'Portalのサービスと管理','title':title,'kind':'Portal管理','section':section,'menu':menu,'subtitle':'本人・管理対象・費用帰属を分けて確認する','footer':'現行リポジトリ定義に基づく教材用の設定ビュー','notice':notice,'fields':[{'id':i,'label':l,'value':v} for i,l,v in rows],'highlightFields':[]}
def table(k,title,section,columns,rows,notice):
 views[k]={'layout':'settings','viewId':'lesson14-'+k,'areaLabel':'Portalのサービスと管理','title':title,'kind':'Portal管理','section':section,'menu':menu,'subtitle':'本人・管理対象・費用帰属を分けて確認する','footer':'現行リポジトリ定義に基づく教材用の設定ビュー','notice':notice,'table':{'columns':[{'label':l,'width':w} for l,w in columns],'rows':[{'id':i,'cells':c} for i,c in rows]},'highlightRows':[]}
fields('three-axes','「誰が」「何を」「どの会社のために」','役割分担',[
('person','本人','Entra IDで確認 → PortalのSQLセッション'),('owner','キーの所有・操作権限','現在の所属グループとSQLの登録を照合'),('company','会社','APIキーの利用に対する費用帰属')],'同じ会社名でも、管理できるキーは同じとは限らない')
table('roles','Portal・SQL・APIMの保存と処理','役割分担',[('構成要素',240),('担当すること',732)],[
('portal',['Portal','画面操作の受付・入力確認・SQL／Graph／APIMの呼出し']),('sql',['SQL','ログイン状態・会社・キー台帳・監査']),('apim',['APIM','APIMサブスクリプションとキー実値・APIの利用許可'])],'キー台帳とキー実値は、確認する場所が異なる')
fields('runtime','Portalが処理をつなぐ','役割分担',[
('http','Portal / Container App','常駐するHTTP処理'),('schema','SQL初期化Job','配備時にDBを準備'),('observe','グループ観測Job','15分ごとに組織の状態を確認'),('identity','外部照会','Entra ID／Graphで本人・現在所属を確認')],'画面処理とJobを分ける。配備の詳細は応用編で扱う')
fields('login','本人確認からログイン状態へ','本人認証',[
('id','Microsoft Entra ID','IDトークンを検証し、テナントID＋Object IDを取得'),('auth','Better Auth','認証とログイン状態の管理'),('session','以後の要求','CookieとSQLセッションの有効期限を照合')],'表示名や会社名を、本人を識別する鍵にしない')
table('auth-data','認証に必要な四つの表','本人認証',[('表',310),('保存する目的',662)],[
('user',['portalUser','テナントID＋Object IDで識別する本人レコード']),('account',['portalAccount','MicrosoftアカウントとUserの対応']),('session',['portalSession','セッショントークンと有効期限。userIdでUserを参照']),('verify',['portalVerification','認証途中の期限付き検証情報'])],'認証用のOAuthトークンは保存前に除く')
fields('authorization','ログイン後、どのキーを管理できるか','所有グループ',[
('graph','現在の所属','本人IDを使ってGraphで照会'),('group','SQLの所有グループ','キーに登録されたEntraグループと対応付け'),('approval','追加の判断','製品承認・キー状態など、操作に必要な条件を照合'),('company','companyId','会社を参照する費用帰属の情報')],'ログインの成功だけで、すべてのキーを操作できるわけではない')
fields('no-direct','個人レコードからキーへの対応','所有グループ',[
('identity','本人の識別','テナントID＋Object ID'),('membership','現在所属の確認','Graphで照会。SQLの固定メンバー表を使わない'),('ownership','キーの所有','Entraグループ'),('relation','SQLの関係','UserとAPIキーを直接結ぶ外部キーはない')],'現在所属と、SQLに登録された所有・承認の対応を見る')
table('company-data','会社情報とキー台帳の対応','会社情報',[('データ',300),('保存する情報・関係',672)],[
('company',['portalCompany','会社ID・会社名・選択可能状態']),('key',['キー台帳のcompanyId','利用会社を参照し、費用帰属を対応付ける']),('audit',['portalCompanyAudit','作成・変更前後・操作者・日時の履歴'])],'会社登録は、利用量集計・金額算定・請求処理を実行しない')
fields('company-operation','会社を登録し、候補として選ぶ','会社情報',[
('admin','操作できる人','基盤管理グループへの現在所属を確認'),('create','会社の登録','会社名の重複を確認して有効な会社を保存'),('candidate','払い出し画面の会社候補','有効な会社だけを返す'),('select','APIキーとの対応','払い出し時にcompanyIdで会社を参照')],'会社を選べることと、グループ所有のキーを管理できることを分ける')
fields('company-update','会社の変更・選択停止で起きること','会社情報',[
('concurrent','更新競合の確認','読込時の更新日時と現在値を比較'),('save','保存','会社情報と変更監査を同じSQL処理で保存'),('disabled','会社の選択停止','新規の選択候補から外す。既存キーの参照は保持'),('rename','会社名の変更','SQLへ保存。通常のAPIM表示名は同期機能で反映')],'会社の選択停止と、APIキー自体の停止は別の操作')
table('case','架空例：A社向けの分析APIキー','所有グループ',[('確認する軸',275),('教材上の状態',697)],[
('person',['本人','担当者はPortalにログイン済み']),('group',['所有グループ','キーの所有グループは「分析チーム」']),('member',['現在所属','担当者が「分析チーム」に所属するかGraphで確認']),('company',['費用帰属','キー台帳のcompanyIdはA社を参照'])],'架空の説明例。実在の利用者・会社・稼働環境の取得値ではない')
fields('check-login','確認1：ログインできたら何でも管理できる？','確認問題',[
('question','問い','同じ会社の二人は、同じキーを必ず管理できる？'),('person','確認A','本人とログイン状態'),('group','確認B','現在所属・所有グループ・製品承認・キー状態'),('company','確認C','会社への費用帰属')],'問いを考える間は、答えの項目を強調しない')
fields('check-disable','確認2：会社を選択停止にしたら？','確認問題',[
('question','問い','既存キーの利用と会社参照はどうなる？'),('choice1','選択肢A','既存キーがすべて自動停止する'),('choice2','選択肢B','新規候補から外れ、既存キーの会社参照は残る'),('choice3','選択肢C','グループ所属が解除される')],'会社の状態・キーの状態・グループ所属を分けて考える')
fields('check-key','確認3：キー実値を確認する場所は？','確認問題',[
('question','問い','SQLに台帳があれば、キー実値もSQLにある？'),('portal','Portal','操作と判断をつなぐ'),('sql','SQL','会社・所有・台帳・監査を保持'),('apim','APIM','APIMサブスクリプションとキー実値を保持')],'保存目的から確認先を選ぶ')
fields('summary','管理の問いを確認先へつなぐ','まとめ',[
('identity','誰が操作しているか','Entraの本人確認＋Cookie／SQLセッション'),('owner','どのキーを管理できるか','Graphの現在所属＋SQLの所有・承認・状態'),('cost','どの会社に費用を帰属させるか','Companyとキー台帳のcompanyId'),('secrets','キー実値はどこか','APIM。SQLは台帳と監査')],'次回：APIキー発行で、SQLとAPIMをどの順に更新するのか')
for k,svg in [('identity-flow','identity-flow.svg'),('roles-flow','roles-flow.svg')]:
 views[k]={'layout':'svg','viewId':'lesson14-'+k,'svg':'diagrams/'+svg}
turns=[]
replacements=[('Credential Broker','クレデンシャル ブローカー'),('Microsoft','マイクロソフト'),('Gateway','ゲートウェイ'),('Azure','アジュール'),('ID','アイディー'),('Microsoft Entra ID','マイクロソフト エントラ アイディー'),('Entra ID','エントラ アイディー'),('Entra','エントラ'),('Better Auth','ベターオース'),('Object ID','オブジェクト アイディー'),('IDトークン','アイディートークン'),('APIキー','エーピーアイキー'),('API','エーピーアイ'),('APIM','エーピーアイエム'),('Portal','ポータル'),('SQL','エスキューエル'),('Graph','グラフ'),('Cookie','クッキー'),('OAuth','オーオース'),('HTTP','エイチティーティーピー'),('Container App','コンテナーアプリ'),('Job','ジョブ'),('companyId','カンパニーアイディー'),('userId','ユーザーアイディー'),('User','ユーザー'),('Account','アカウント'),('Session','セッション'),('Verification','ベリフィケーション'),('CompanyAudit','カンパニーオーディット'),('Company','カンパニー')]
# Longer identifiers are spoken as Japanese descriptions below, rather than reading table names.
replacements.sort(key=lambda x:-len(x[0]))
def chunks(text,maxlen=29):
 # Sentence and clause boundaries give captions short, readable holds.
 units=re.findall(r'.*?[、。？！?]|.+$',text)
 lines=[];line=''
 for unit in units:
  while len(unit)>maxlen:
   room=maxlen-len(line)
   if room<6 and line:lines.append(line);line='';room=maxlen
   line+=unit[:room];unit=unit[room:];lines.append(line);line=''
  if len(line)+len(unit)>maxlen:
   if line:lines.append(line)
   line=unit
  else:line+=unit
 if line:lines.append(line)
 for i in range(len(lines)-1):
  if re.search(r'[A-Za-z0-9]$',lines[i]) and re.match(r'[A-Za-z0-9]',lines[i+1]):
   token=re.search(r'[A-Za-z0-9]+$',lines[i]).group()
   lines[i]=lines[i][:-len(token)];lines[i+1]=token+lines[i+1]
 return [{'caption':''.join(lines[i:i+2]),'lines':lines[i:i+2],'weight':len(''.join(lines[i:i+2]))} for i in range(0,len(lines),2)]
def t(who,chapter,text,view=None,hi=(),delivery=None):
 v=copy.deepcopy(views[view]) if view else {'layout':'hologram','heading':'誰が何を管理できるのか'}
 if view and v['layout']=='settings':v['highlightRows' if 'table'in v else 'highlightFields']=list(hi)
 speech=text
 for a,b in replacements:speech=speech.replace(a,b)
 cc=chunks(text)
 turns.append({'id':f'lesson14-turn-{len(turns)+1:03}-{who}','speaker':who,'chapterId':chapter,'speech':speech,'caption':text,'subtitleLines':cc[0]['lines'],'subtitleChunks':cc,'gapAfterSeconds':.14,'delivery':delivery or {'mako':'直前の説明を受け、視聴者と一緒に確かめる自然な問いや反応。','manabu':'目的や判断の理由を、同僚への短い返答として落ち着いて伝える。','ikumi':'問いの焦点に答え、仕組みと確認先を明瞭に説明する。'}[who],'visual':v})
# Original opening for this topic. About 35 seconds at normal speech speed.
t('mako','opening','会社が登録されていて、Portalにもログインできた。それなら、その会社のAPIキーは全部管理できるんでしょうか。')
t('manabu','opening','そこは分けて考えたいですね。本人が誰か、どのキーを管理できるか、費用をどの会社へ帰属させるか。それぞれ違う問いなんです。')
t('ikumi','opening','今日は、その三つをPortal、SQL、APIMの役割へ結び付けます。会社名だけで管理権限が決まる、という思い込みから整理しましょう。')
# Roles and runtime.
t('mako','roles','最初に三つの軸をそろえます。本人、キーの所有、会社。画面では一緒に見えても、判断する根拠が違うんですね。','three-axes')
t('manabu','roles','はい。本人は操作した人を識別するため。所有グループはチームでキーを管理するため。会社は、利用の便益に応じた費用負担を整理するためにあります。','three-axes',['person','owner','company'])
t('mako','roles','では、Portalが全部の情報を持って、APIの利用も判断するのでしょうか。三つの構成要素の分担を知りたいです。','roles')
t('ikumi','roles','Portalは操作の入口です。画面から受けた要求について、本人や入力、業務上の条件を確認し、SQL、Graph、APIMを呼び分けます。','roles',['portal'])
t('manabu','roles','利用する人から見ると、一つの窓口で手続きができます。ただ、その裏では、管理するための情報と、APIを利用するためのキーを分けて持ちます。','roles',['portal'])
t('ikumi','roles','SQLはログイン状態、会社、キーの台帳や監査などを持ちます。誰の操作で何が変わったか、どのグループや会社に対応するかを残す場所です。','roles',['sql'])
t('mako','roles','台帳がSQLなら、キーの文字列もSQLから取り出す、と考えやすいですね。実値は、どこが持つんですか。','roles')
t('ikumi','roles','キー実値を持つのはAPIMです。APIMサブスクリプションと、そのキーを管理します。SQLのキー台帳に記録があることと、キー実値をSQLに保存することは別です。','roles',['apim'])
t('manabu','roles','だから、問い合わせを受けたときも、会社や所有の対応ならSQL、キー実体ならAPIM、操作がどう判断されたかならPortal、と確認先を選べます。','roles',['portal','sql','apim'])
t('mako','roles','三つのつながりも確認します。この関連図の矢印は、Portalが保存先や管理先へ処理を依頼する関係ですね。','roles-flow')
t('ikumi','roles','そうです。ブラウザーがSQLへ直接つながる構成としては読みません。また、APIMの管理操作と、公開Gatewayへ送るAPI試用の要求も、接続先が違います。','roles-flow')
t('mako','roles','キーの管理をするたびに、APIM全体を作り直すわけでもないんですね。Portalの裏で常時動くものと、定期処理も分かれていますか。','runtime')
t('ikumi','roles','はい。PortalのContainer Appは常駐するHTTP処理です。SQL初期化Jobは配備時の準備、グループ観測Jobは十五分ごとの組織確認を担当します。','runtime',['http','schema','observe'])
t('manabu','roles','ここでは実行場所の役割まで押さえましょう。通常のキー操作でAPIM全体は再配備しません。発行の順序や、組織の観測結果を整える詳細は後の回へ進みます。','runtime',['http','observe'])
# Authentication.
t('mako','signin','続いて、誰が操作しているかです。Microsoftでログインする処理と、ログイン後の状態を管理する処理は、どう分担していますか。','login')
t('ikumi','signin','Microsoft Entra IDで本人を確認し、Better Authが認証とログイン状態を管理します。IDトークンを検証し、テナントIDとObject IDを取り出します。','login',['id','auth'])
t('mako','signin','本人を見分けるのはメールアドレスや表示名ではなく、その二つのIDの組合せなんですね。テナントも一緒に見るのがポイントですか。','login')
t('ikumi','signin','はい。本人のレコードは、EntraのテナントIDとObject IDで識別します。会社名を本人の識別子にしたり、表示名が同じだから同一人物と判断したりしません。','login',['id'])
t('manabu','signin','操作した人を安定して識別できることが、管理の出発点です。ただ、この段階で分かるのは本人であって、すべてのキーに対する管理権限ではありません。','login',['id'])
t('mako','signin','本人確認の後は、要求するたびに最初からMicrosoftのログイン画面を通るんでしょうか。','login')
t('ikumi','signin','以後はCookieと、SQLにあるセッションの有効期限を照合して本人を識別します。ブラウザー側の情報と、サーバー側で管理するログイン状態を対応させます。','login',['session'])
t('mako','signin','ここで認証用の表を見ます。四つとも本人情報というより、それぞれ保存目的があるんですね。まずUserとAccountは、どう違いますか。','auth-data')
t('ikumi','signin','Userは本人のレコードです。AccountはMicrosoftアカウントとの対応を持ち、userIdでUserを参照します。本人そのものと、認証元との対応を分けています。','auth-data',['user','account'])
t('mako','signin','Sessionは、その人がログイン中であることを扱う表ですね。Verificationも、長期保存する資格情報なんですか。','auth-data')
t('ikumi','signin','Sessionはセッショントークンと有効期限を持ち、同じくUserを参照します。Verificationは認証途中の期限付き検証情報です。OAuthトークンを保存する表ではありません。','auth-data',['session','verify'])
t('manabu','signin','認証で使う情報を、何でも残しているわけではないんですね。この実装ではOAuthトークンを保存前に除きます。ログイン状態の管理と、外部APIへの本人接続も区別します。','auth-data',['verify'])
t('mako','signin','なるほど。今日はPortalの本人確認が対象です。Graphを本人として使うための接続の詳細は、Credential Brokerの回で学ぶ、という範囲ですね。','auth-data')
# Current membership and authorization.
t('mako','ownership','ログイン中だと分かった後で、どのキーを管理できるかを決めます。個人のUserレコードに、担当キーの一覧が直接つながっているんですか。','no-direct')
t('ikumi','ownership','いいえ。APIキーの所有者はEntraグループです。本人とAPIキーを直接結ぶSQLの外部キーはなく、所属メンバー一覧を保存する表もありません。','no-direct',['ownership','relation'])
t('manabu','ownership','個人の担当物として閉じるより、チームで管理を引き継ぐための所有単位だと考えると分かりやすいです。会社を選ぶこととも、役割が違います。','no-direct',['ownership'])
t('mako','ownership','では、本人からグループ所有のキーへ、どうたどるのでしょう。この関連図で、本人からキーまで、確認する順に追ってみたいです。','identity-flow')
t('ikumi','ownership','最初に、セッションから識別した本人IDを使い、Graphで現在の所属グループを照会します。次に、そのグループと、SQLに登録されたキーの所有グループを対応させます。','identity-flow')
t('ikumi','ownership','そのうえで、製品承認やキー状態など、操作に必要な条件を照合します。所属だけで判断せず、対象の操作に必要な条件がそろっていることを確かめます。','identity-flow')
t('mako','ownership','ログイン時に一度調べた所属を、ずっと持ち続けるわけではないんですね。「現在の所属」という言葉が効いてきます。','identity-flow')
t('ikumi','ownership','はい。操作時にGraphで確認する点が大事です。SQLに固定のメンバー表を持ち、それだけを読んで判断する構成と混同しないようにします。','identity-flow')
t('manabu','ownership','組織の担当が変われば、誰が管理できるかも見直す必要があります。本人は同じでも、現在どのグループにいるかは別の状態として確認するわけです。','identity-flow')
t('mako','ownership','画面へ戻って判断材料を整理します。所有グループが一致したら、製品承認とキー状態も確認。その一方で、会社は何に使うのでしょう。','authorization')
t('ikumi','ownership','キーに登録されたcompanyIdから利用会社を参照します。この値は費用帰属の対応です。会社名が同じ、という理由だけでキーの管理権限を与える実装ではありません。','authorization',['company'])
t('mako','ownership','会社が同じ同僚でも、所有グループへの現在所属が違えば、管理対象が同じとは限らない。会社名とグループ名を似せていても、別々に見るんですね。','authorization')
t('manabu','ownership','そうです。会社は費用の行き先、グループはチームとしての所有と操作権限です。画面上で会社が見えることを、操作できる根拠へ置き換えないのが判断の要点です。','authorization',['group','company'])
t('ikumi','ownership','なお、ここでいうAPIMサブスクリプションは、APIの利用許可を管理する単位です。Azureリソースの課金や管理に使うAzureサブスクリプションとは区別してください。','authorization')
# Company.
t('mako','company','次に会社情報です。権限そのものではないのに、キーに会社を登録するのは、どんな目的があるのでしょうか。','company-data')
t('manabu','company','利用の便益を受ける会社と、費用を負担する会社がずれないようにするためです。配賦や請求に必要な対応情報として、どの会社のためのキーかを残します。','company-data',['company','key'])
t('mako','company','会社を登録したら、利用量の集計や請求まで、この機能が自動で済ませてくれるのでしょうか。','company-data')
t('manabu','company','そこまでは行いません。会社登録機能が持つのは費用帰属の対応情報です。利用量の集計、金額の算定、請求処理そのものとは範囲を分けます。','company-data',['key'])
t('ikumi','company','データでは、会社ID、会社名、選択可能状態を会社の表へ保存します。キー台帳のcompanyIdが会社を参照し、会社の監査表には変更前後、操作者、日時などを残します。','company-data',['company','key','audit'])
t('mako','company','会社を登録したり変更したりするのは、Portalへログインできる人なら誰でもよい、ということですか。','company-operation')
t('ikumi','company','会社管理では、基盤管理グループへの現在所属を確認します。本人の確認に加えて、会社情報を管理するための所属も確かめるので、ログインだけで判断しません。','company-operation',['admin'])
t('manabu','company','会社一覧を整える責任と、その会社向けのキーをチームで管理する責任は、見る対象が違います。誰のどの操作を認めるか、機能ごとに分けて考えましょう。','company-operation',['admin'])
t('mako','company','登録の流れは、名前を入力した後に重複を確認して保存。その後、払い出し画面には、どの会社が選択肢として出てきますか。','company-operation')
t('ikumi','company','有効な会社だけを候補として返します。APIキーを払い出すときは、選んだ会社をcompanyIdで参照します。会社の作成履歴も、登録と一緒に保存します。','company-operation',['create','candidate','select'])
t('mako','company','二人の基盤管理者が同じ会社を開いていて、片方が先に変更した場合はどうしますか。古い画面から上書きしてよいのでしょうか。','company-update')
t('ikumi','company','読込時の更新日時と現在値を比較して、更新の競合を確認します。会社情報と、その変更前後の監査は同じSQL処理で保存します。変更と履歴の対応を保つためです。','company-update',['concurrent','save'])
t('mako','company','会社を今後は選ばせたくない場合、選択停止にしますよね。既に発行してあるキーも、まとめて利用できなくなるのでしょうか。','company-update')
t('ikumi','company','選択停止後も、既存キーの会社参照は保持します。新しく選ぶ候補から外す操作であり、APIキー自体を停止する操作とは分かれています。','company-update',['disabled'])
t('manabu','company','過去の利用や発行済みキーが、どの会社に対応するかは残す必要があります。候補から外すことと、過去の対応を消すことを、同じ扱いにしないんですね。','company-update',['disabled'])
t('mako','company','会社名を変更した場合も、SQLの会社情報と、APIMに表示される名前は、その場で必ず一緒に変わりますか。','company-update')
t('ikumi','company','この会社管理機能が保存するのはSQLです。通常のAPIMサブスクリプションの表示名との差分は、APIMサブスクリプション同期機能で反映します。確認先と反映の段階を分けます。','company-update',['rename'])
# Concrete case.
t('mako','case','一つの架空例で整理します。A社向けの分析APIキーがあり、所有グループは分析チーム。担当者はPortalへログイン済みです。何から見ますか。','case')
t('ikumi','case','まず、ログイン中の本人を識別できているかです。次にGraphで、その人が現在、分析チームに所属しているかを確認し、SQLの所有グループや必要な承認、キー状態を照合します。','case',['person','group','member'])
t('manabu','case','そして、費用をどこに帰属させるかは、キー台帳の会社参照でA社へ対応させます。本人の所属会社を推測して、会社の登録値を置き換える話ではありません。','case',['company'])
t('mako','case','A社の別の担当者がログインできても、それだけでは、このキーを管理できると決まらない。分析チームへの現在所属などを別に確かめるわけですね。','case')
t('ikumi','case','その通りです。もし管理できないという相談なら、本人の識別、現在所属、SQLの所有グループと承認や状態を、同じ対象キーについて照合します。会社名の一致だけで終えません。','case',['person','member','group'])
t('mako','case','担当者がチームを異動しても、SQLのUserからキーを直接ぶら下げ直す、という説明にはならない。現在所属を見る意味がここにもありますね。','case')
t('manabu','case','はい。誰が使うか、誰が管理するか、どの会社のための利用かを、一つの名前にまとめないことです。問い合わせでも、どの軸の話かを先にそろえると確認しやすくなります。','case',['person','group','company'])
# Three quizzes, neutral questions and highlights only after answers.
t('mako','check','確認問題です。同じ会社の二人がPortalにログインできました。二人とも同じAPIキーを必ず管理できる、と言ってよいでしょうか。','check-login',delivery='確認問題を提示する。答えの先取りをせず、問いのあとに自然な短い間を取る。')
t('manabu','check','言い切れません。本人としてログインできることと、対象キーの管理権限は別です。現在所属するグループと、キーの所有グループなどの条件を確認します。','check-login',['group'])
t('ikumi','check','会社の一致は費用帰属の対応として見ます。製品承認やキー状態も含め、操作に必要な条件を照合することまでが答えです。','check-login',['group','company'])
t('mako','check','二問目です。会社を選択停止にしました。A、既存キーもすべて自動停止。B、新規候補から外れ、既存キーの会社参照は残る。C、グループ所属が解除。どれでしょう。','check-disable',delivery='三つの選択肢を聞き分けられるよう、区切りを取って落ち着いて問う。')
t('manabu','check','答えはBです。今後の選択候補から外すことと、既存キーの利用を止めることは別です。グループ所属を解除する操作でもありません。','check-disable',['choice2'])
t('ikumi','check','会社の監査と既存キーの参照は、変更の意味をたどる手掛かりです。選択停止を、会社参照の削除やAPIキーの停止として説明しないようにします。','check-disable',['choice2'])
t('mako','check','最後です。SQLにキーの台帳がありました。では、そのキー実値もSQLに保存されていると判断してよいでしょうか。確認する場所を答えてください。','check-key')
t('ikumi','check','キー実値を保持する場所はAPIMです。SQLは会社や所有などの台帳と監査、Portalは操作の入口です。台帳の存在とキー実体の保存先を区別します。','check-key',['sql','apim'])
# Summary and transition to lesson 15.
t('mako','summary','今日の問いへ戻りましょう。会社が登録されていて、Portalへログインできても、それだけでは、その会社のキーを全部管理できるとは限らないんですね。','summary')
t('manabu','summary','はい。本人、所有グループ、費用帰属の三つを分けます。会社は便益と費用負担を対応付ける情報です。キーをチームで管理する判断は、現在所属と所有グループを軸に見ます。','summary',['owner','cost'])
t('ikumi','summary','確認先も対応させてください。本人はEntraとSQLセッション、現在所属はGraph、所有や会社の台帳と監査はSQL、APIMサブスクリプションとキー実値はAPIMです。','summary',['identity','owner','secrets'])
t('mako','summary','次回は、この分担のまま、APIキーを発行する一連の処理を追います。SQLの予約からAPIMの作成、SQLの確定へ、順番をそろえる理由を見ていきましょう。','summary')
s['turns']=turns
s['sourceReferences'] += [{'file':'../../../apps/portal/'+f} for f in ['lib/auth-identity.ts','lib/auth-session.ts','lib/auth-schema.ts','lib/api-keys/graph.ts','lib/api-keys/service.ts','lib/company-administration/service.ts','lib/company-administration/repository.ts']]
(P/'script.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(turns),'captionChars':sum(len(t['caption']) for t in turns),'speechChars':sum(len(t['speech']) for t in turns),'openingChars':sum(len(t['caption']) for t in turns if t['chapterId']=='opening'),'views':len(views)},ensure_ascii=False))
