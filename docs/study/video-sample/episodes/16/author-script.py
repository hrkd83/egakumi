#!/usr/bin/env python3
"""Author episode 16 without changing shared files or invoking generation."""
from pathlib import Path
import copy
import json
import re
import textwrap

HERE = Path(__file__).resolve().parent
script = json.loads((HERE / 'script.json').read_text())
script['status'] = 'draft'
script['description'] = 'カタログの生成・閲覧と試用の実行を追い、台帳ID、APIMサブスクリプションID、秘密キーを使い分けます。'
refs = [
    {'file': '../03-subscription-management.html', 'anchor': 'catalog'},
    {'file': '../03-subscription-management.html', 'anchor': 'execution'},
    {'file': '../../../apps/portal/scripts/prepare-model-catalog.ts'},
    {'file': '../../../apps/portal/lib/model-catalog.ts'},
    {'file': '../../../apps/portal/lib/model-key-access.ts'},
    {'file': '../../../apps/portal/lib/api-keys/service.ts'},
    {'file': '../../../apps/portal/lib/api-keys/repository.ts'},
    {'file': '../../../apps/portal/lib/api-keys/apim.ts'},
    {'file': '../../../apps/portal/lib/model-gateway.ts'},
    {'file': '../../../apps/portal/lib/model-request.ts'},
    {'file': '../../../apps/portal/lib/model-response.ts'},
    {'file': '../../../apps/portal/lib/realtime-relay.ts'},
    {'file': '../../../apps/portal/lib/pii-mcp-gateway.ts'},
]
script['sourceReferences'] = refs
chapter_specs = [
 ('opening', '今回の問い', '仕様が見えることと実行できることの違いを知る', refs[:2]),
 ('catalog', 'カタログはどこから来るか', '元定義、生成JSON、画面表示と変更経路を説明する', [refs[0], refs[2], refs[3]]),
 ('identity', '台帳ID・実ID・秘密キー', '選択するIDとAPIM実体と認証するキーを区別する', [refs[1], refs[4], refs[5]]),
 ('execution', '試用要求を一つ追う', '候補表示、実行時再確認、キー取得、Gateway送信を追う', [refs[1], refs[4], refs[5], refs[7], refs[8]]),
 ('response', '結果と保存範囲を確認する', '応答形式、SQLの保存範囲、サービスごとの差異を確認する', [refs[1], refs[8], refs[10], refs[11], refs[12]]),
 ('check', '三つの確認問題', '変更元、ID、実行時再確認を自分で判断する', refs[:2]),
 ('summary', 'まとめと次の問い', '仕様確認から結果表示までを説明し次回へつなぐ', refs[:2]),
]
script['chapters'] = [{'id': a, 'title': b, 'goal': c, 'sourceRefs': d} for a,b,c,d in chapter_specs]
views = {}

def view(key, title, section, fields, notice='リポジトリ定義に基づく教材。稼働環境の実測ではありません。'):
    views[key] = {'layout':'settings','viewId':key,'areaLabel':'Portal / 仕様確認とAPI試用',
                  'heading':title,'title':title,'kind':'Portalの処理','section':section,
                  'menu':['仕様確認','IDの対応','候補選択','実行前確認','要求と結果','確認問題'],
                  'notice':notice,'fields':[{'id':i,'label':label,'value':value} for i,label,value in fields],
                  'highlightFields':[]}

view('catalog-purpose','見ることと使うことを分ける','仕様確認',[
 ('read','仕様を見る','API・操作・入力条件を確認'),('execute','試用する','本人が利用できる対象で実行'),
 ('basis','今回の根拠','資料とリポジトリ定義')])
view('catalog-build-flow','API定義からカタログを生成する','仕様確認',[
 ('source','生成元','API定義・公開名・モデル能力情報'),('build','ビルド前','カタログJSONを生成'),
 ('bundle','Portal','生成JSONを同梱して表示')])
view('catalog-display','カテゴリから仕様と試用へ進む','仕様確認',[
 ('select','選ぶ軸','カテゴリ → 提供元 → モデル・操作'),('guide','確認する内容','ガイド・HTTPメソッド・公開パス'),
 ('condition','入力条件','許可モデル・形式・入力上限'),('sql','SQLを使う処理','ログイン確認')])
view('catalog-change','APIやモデルを追加する場所','仕様確認',[
 ('api','APIの元定義','apps/gateway/apim/apis'),('capability','能力情報','model-capabilities.json'),
 ('binding','公開名の置換','public-bindings.json'),('release','反映手順','元定義の変更 → 再生成 → 配備')])
view('identity-three','三つの値を対応付ける','IDの対応',[
 ('ledger','台帳ID','portalApiKey.id / 画面で選択'),('subscription','APIMサブスクリプションID','apimSubscriptionId / 実体の識別'),
 ('secret','主キー','APIMから取得 / Gatewayで認証'),('relation','対応の保管先','SQL台帳が台帳IDと実IDを対応付ける')])
view('execution-choices','試用候補を絞り込む','候補選択',[
 ('graph','Microsoft Graph','本人の現在所属を照会'),('sql','SQL','所有グループ・製品承認・台帳状態'),
 ('api','APIM','製品に対象APIが含まれるか'),('return','画面へ返す','台帳ID・表示名・会社名・グループ名'),
 ('secret','この段階の秘密キー','取得しない')])
view('execution-flow','通常RESTの試用要求を追う','実行前確認',[
 ('browser','ブラウザー → Portal','台帳IDとサービス入力'),('authorize','Portalサーバー','ロックと現在の利用許可を再確認'),
 ('arm','ARM管理API','実体確認 → 製品API確認 → 主キー取得'),('gateway','固定Gateway','主キーを付けて送信'),
 ('result','Portal → ブラウザー','検証した実行結果')])
view('execution-lock','選んだ後にも利用許可を確認する','実行前確認',[
 ('id','受信した台帳ID','本人を含む条件で対象をロック'),('graph','再確認する所属','現在のMicrosoft Graph所属'),
 ('sql','再確認するSQL','承認・所有・会社・台帳状態'),('time','確認する時点','候補表示時と実行時を分ける')])
view('execution-arm','管理APIで主キーを取得する','実行前確認',[
 ('get','1 / APIM GET','実ID・製品scope・active状態を確認'),('product','2 / 製品内API確認','対象APIの所属を確認'),
 ('secret','3 / listSecrets','APIMのprimaryKeyを取得'),('scope','取得値の利用範囲','サーバー内部の実行だけ')])
view('execution-input','テキスト生成の入力と送信先','要求と結果',[
 ('request','Portalへの要求','apiKeyId＋モデル・操作・本文'),('id','apiKeyIdの意味','portalApiKey.id（台帳ID）'),
 ('validate','サーバーで検証','本人・送信元・形式・容量・モデル'),('fixed','Gateway送信先','設定済みのoriginと登録済みパス'),
 ('key','認証に使う値','サーバーで取得した主キー')])
view('execution-response','Gatewayから結果が返るまで','要求と結果',[
 ('receive','受け取る形式','サービスに応じたJSON・ストリーム・ファイル'),('validate','Portal側の確認','形式・容量・応答内容を検証'),
 ('display','画面で確認','生成文・使用量・実行結果'),('id','照合用の情報','返る場合は検証済みの要求ID')])
view('execution-storage','実行とSQLの保存範囲','要求と結果',[
 ('ledger','SQLにあるもの','台帳・承認・会社・所有グループ'),('observation','必要に応じて保存','グループ登録と観測'),
 ('notstored','専用SQL表がないもの','試用の入力・実行結果・キー実値'),('outside','別途確認する対象','APIMや下流サービス側の記録')])
view('execution-services','サービスごとの応答と接続方式','要求と結果',[
 ('rest','テキストなど','JSON・ストリーム等をサービス別に処理'),('realtime','Realtime音声','中継サーバーが接続を受け内部認可'),
 ('document','文書解析','開始と待機をAPIMポリシーが担当'),('mcp','PII MCP','初期化 → 一覧 → 呼出 → 切断')])
view('execution-trouble','止まった段階から確認先を選ぶ','要求と結果',[
 ('catalog','仕様が古い','元定義・生成JSON・配備を確認'),('choice','候補が見えない','現在所属・承認・台帳・製品内API'),
 ('execution','選択後に実行できない','実行時の再認可・APIM実体・入力条件'),('response','Gateway以降の失敗','返却結果・検証済み要求IDで照合')])
view('quiz-catalog','確認1 / 追加したモデルが見えない','確認問題',[
 ('a','案A','生成済みJSONだけを直す'),('b','案B','元定義を直し、再生成・配備する'),
 ('c','案C','試用の秘密キーを取り替える')], '問い：カタログへ継続して反映するには、どこを変更しますか？')
view('quiz-id','確認2 / 画面から何を送るか','確認問題',[
 ('a','案A','APIMの主キー'),('b','案B','APIMサブスクリプションID'),('c','案C','SQL台帳のportalApiKey.id')],
 '問い：通常のPortal試用で、apiKeyIdへ入る値を選びます。')
view('quiz-recheck','確認3 / 候補が表示された後','確認問題',[
 ('a','案A','表示時の許可があれば、そのまま送信'),('b','案B','実行時に現在の利用許可と実体を再確認'),
 ('c','案C','画面の表示名を秘密キーとして使う')], '問い：表示後に所属や状態が変わった場合、どう扱いますか？')
view('lesson-summary','仕様確認から試用までのまとめ','要求と結果',[
 ('catalog','仕様の経路','元定義 → 生成JSON → Portal表示'),('identity','三つの値','台帳ID / 実APIM ID / 秘密キー'),
 ('execution','実行の経路','再認可 → ARM取得 → 固定Gateway'),('next','第17回','発行後のキー管理と利用制限')])

turns = []
def speech(text):
    replacements = [
      ('portalApiKey.id','ポータルエーピーアイキーのアイディー'),
      ('apimSubscriptionId','エーピーアイエムサブスクリプションアイディー'),
      ('apiKeyId','エーピーアイキーアイディー'),('primaryKey','プライマリーキー'),
      ('listSecrets','リストシークレッツ'),('Microsoft Graph','マイクロソフトグラフ'),
      ('Realtime','リアルタイム'),('Gateway','ゲートウェイ'),('Portal','ポータル'),
      ('JSON','ジェイソン'),('HTTP','エイチティーティーピー'),('REST','レスト'),
      ('APIM','エーピーアイエム'),('SQL','エスキューエル'),('ARM','アーム'),
      ('API','エーピーアイ'),('MCP','エムシーピー'),('PII','ピーアイアイ'),
      ('GET','ゲット'),('active','アクティブ'),('scope','スコープ'),('ID','アイディー'),
    ]
    for a,b in replacements: text = text.replace(a,b)
    return text

def chunk(text):
    # Keep Japanese sentence boundaries, then split long sentences into <= 2 lines.
    sentences = [x for x in re.findall(r'[^。！？]+[。！？]?|[。！？]',text) if x]
    out=[]
    for sentence in sentences:
        while len(sentence)>64:
            options=[sentence.rfind(p,15,62)+1 for p in ('、','：','→')]
            at=max(options)
            if at<=15: at=60
            part,sentence=sentence[:at],sentence[at:]
            out.append({'caption':part,'lines':textwrap.wrap(part,width=32),'weight':len(part)})
        if sentence: out.append({'caption':sentence,'lines':textwrap.wrap(sentence,width=32),'weight':len(sentence)})
    return out

def add(speaker,chapter,key,text,highlight=(),delivery=None):
    visual = {'layout':'hologram','heading':script['topic']} if key is None else copy.deepcopy(views[key])
    if key: visual['highlightFields']=list(highlight)
    chunks=chunk(text)
    turns.append({'id':f'lesson16-turn-{len(turns)+1:03}-{speaker}', 'speaker':speaker,'chapterId':chapter,
                  'speech':speech(text),'caption':text,'subtitleLines':chunks[0]['lines'],
                  'subtitleChunks':chunks,'gapAfterSeconds':.14,
                  'delivery':delivery or {'mako':'視聴者の疑問や理解を相手へ自然に確かめる。',
                     'manabu':'目的と利用者への意味を、直前の問いへの返答として穏やかに整理する。',
                     'ikumi':'実装の順序と値の違いを、会話の相手へ明瞭に説明する。'}[speaker],
                  'visual':visual})

# Opening: a new episode-specific exchange, 177 caption characters.
add('mako','opening',None,'仕様を見て、使えそうなAPIを選びました。試用するには、表示されたキーをコピーすればいいんですか？')
add('manabu','opening',None,'Portalの試用では、使いたい対象を選びます。仕様を読めることと、今の本人が実行できることは分けて考えましょう。')
add('ikumi','opening',None,'今日はカタログの出所と、試用の裏側を追います。台帳のID、APIMの実ID、秘密キーも区別します。')
add('mako','opening',None,'似た名前の値を整理すると、画面から何が送られるかも分かりそうですね。')

# 1. Purpose and source of the catalog.
add('mako','catalog','catalog-purpose','まず、仕様を調べる画面と、APIを試す画面はつながっていますよね。どちらも、キーの台帳を読んで表示しているんでしょうか？')
add('manabu','catalog','catalog-purpose','役割が違います。仕様確認は、どんな機能があり、何を入力するのかを知る入口です。試用は、本人が利用できる対象を選び、実際の要求を送る操作です。',['read','execute'])
add('ikumi','catalog','catalog-purpose','カタログ本体はPortalに同梱した定義を読みます。SQLの台帳から仕様を組み立てるわけではありません。一方、試用では台帳や承認の確認が必要です。',['read'])
add('mako','catalog','catalog-purpose','見るための定義と、使うための許可ですね。今日は資料と実装から、その二つの経路をたどります。稼働環境を操作した実演とは区別して見ていきましょう。',['basis'])

add('mako','catalog','catalog-build-flow','では、同梱されるカタログの元はどこですか？画面の説明を手で書き足しているのか、Gatewayの定義から作っているのかが気になります。')
add('ikumi','catalog','catalog-build-flow','Modelのカタログは、GatewayのAPI定義から作ります。操作の名前やHTTPメソッド、公開パスに加え、許可モデルや入力条件を持つ定義です。',['source'])
add('manabu','catalog','catalog-build-flow','Gatewayの配備と仕様表示が、同じ元データを参照する形です。利用者が見ている使い方と、公開する契約を対応付けるためですね。',['source'])
add('ikumi','catalog','catalog-build-flow','ビルド前の処理で、そのAPI定義に公開名の置換値とモデルの能力情報を合わせ、カタログJSONを生成します。そのJSONをPortalへ同梱して表示します。',['build','bundle'])
add('mako','catalog','catalog-build-flow','API定義から生成して、Portalに入る、という順ですね。ページを開くたびに、すべてのモデルへ問い合わせて仕様を集める経路ではないと分かりました。',['bundle'])

add('mako','catalog','catalog-display','使う人は、どんな順で目的のAPIへ進むとよいですか？一覧にたくさんの提供元や操作があると、モデル名だけで選びたくなります。')
add('ikumi','catalog','catalog-display','Modelはカテゴリ、提供元、モデルや操作の順に整理されています。URLのカテゴリと提供元も照合し、選んだ内容に応じたガイド、仕様、試用パネルを表示します。',['select'])
add('manabu','catalog','catalog-display','モデル名だけでなく、何をする操作か、どう入力するかを確認します。HTTPメソッドや公開パス、許可モデル、入力上限を読んでから試すと、比較する条件もそろいます。',['guide','condition'])
add('ikumi','catalog','catalog-display','Tool一覧にはアプリ内の定義も使います。カタログ本体を保存するSQL表はありませんが、ログイン確認にはSQLを使います。仕様表示に関わるSQLが一切ない、とは言えません。',['sql'])

add('mako','catalog','catalog-change','新しいモデルを足したいときは、同梱JSONの行を増やせば終わりですか？画面に出るデータなら、そこを直すのが早そうにも見えます。')
add('ikumi','catalog','catalog-change','変更元はGatewayのAPI定義です。モデル能力情報や公開名の置換が関係する場合は、それらも整合させます。生成済みJSONだけを直すと、次の生成で上書きされます。',['api','capability','binding'])
add('manabu','catalog','catalog-change','追加の目的は、画面に名前を見せることだけではありません。公開する操作と入力条件を、実際に配備する契約とそろえて利用者へ届けることです。',['release'])
add('mako','catalog','catalog-change','元定義を変更して、再生成し、配備まで確認するんですね。画面に出ない問題も、台帳を直す前に、生成と反映の経路から調べられます。',['release'])

# 2. Three distinct values.
add('mako','identity','identity-three','ここから試用ですね。台帳ID、APIMサブスクリプションID、キーと名前が増えました。同じ対象を指すなら、どれを送ってもよいのでしょうか？')
add('ikumi','identity','identity-three','別の値です。一つ目の台帳IDは、SQLの一件を選ぶためのportalApiKey.idです。Portalの選択欄と、実行要求のapiKeyIdには、この台帳IDを使います。',['ledger'])
add('manabu','identity','identity-three','たとえば、利用者は用途や会社、所有グループを見て対象を選びます。その選択をサーバーへ伝える番号が台帳IDです。秘密を入力したという意味にはなりません。',['ledger'])
add('ikumi','identity','identity-three','二つ目は、実際のAPIMサブスクリプションを識別するIDです。SQL台帳のapimSubscriptionIdに対応を持ちます。台帳IDと同じ文字列だとは仮定しません。',['subscription','relation'])
add('ikumi','identity','identity-three','三つ目が認証に使う秘密キーです。今回の試用ではAPIMから主キーを取得し、サーバー内でGatewayへの要求に使います。IDをキーとして送っても認証にはなりません。',['secret'])
add('mako','identity','identity-three','台帳IDで選び、対応する実IDでAPIMへ照会し、取得したキーで認証する。選択、実体の識別、認証という三つの役割で整理できました。',['ledger','subscription','secret'])

# 3. Candidate selection and execution path.
add('mako','execution','execution-choices','試用パネルに並ぶ候補は、ログインした人の全キーですか？仕様が表示されているAPIなら、どの対象を選んでも使えるのでしょうか？')
add('ikumi','execution','execution-choices','候補は絞り込みます。Microsoft Graphで本人の現在所属を確認し、SQLの所有グループ、製品承認、台帳を照合します。必要なグループの登録や観測も保存します。',['graph','sql'])
add('ikumi','execution','execution-choices','さらに、台帳でactiveの対象について、その製品に今回のAPIが含まれるかをAPIMで確認します。使いたいAPIと製品の対応も、候補に関係するんです。',['api'])
add('manabu','execution','execution-choices','選択欄には、選べる対象を判断するための表示名、会社名、グループ名と台帳IDを返します。秘密キーをまとめてブラウザーへ渡す段階ではありません。',['return','secret'])
add('mako','execution','execution-choices','候補が見えるだけでは、主キーを取得済みとは言えないんですね。APIMの各サブスクリプションが今もactiveかは、実行する時点でも確認が必要だと覚えておきます。',['secret'])

add('mako','execution','execution-flow','テキスト生成を一回試すとして、送信から結果までを追いたいです。ブラウザーからは、選んだ台帳IDと文章をPortalへ送るところが出発点ですか？')
add('ikumi','execution','execution-flow','はい。モデルや操作など、サービスに必要な入力も送ります。Portalは本人と入力条件を確認したうえで、台帳IDを使って対象をロックし、現在の利用許可を確認します。',['browser','authorize'])
add('manabu','execution','execution-flow','表示された候補をそのまま信用するのではなく、使う瞬間に判断するわけですね。画面を開いてから所属や承認、状態が変わることもあるからです。',['authorize'])
add('ikumi','execution','execution-flow','次にARMの管理APIで、実際のAPIMサブスクリプションと製品内のAPIを確かめ、主キーを取得します。その主キーを、固定されたGatewayへの要求に付けます。',['arm','gateway'])
add('ikumi','execution','execution-flow','Gatewayから返る応答は、サービスごとの処理で読み取って検証し、結果をブラウザーへ返します。秘密キーを結果へ同梱する経路にはしません。',['result'])
add('mako','execution','execution-flow','ブラウザーとGatewayの間で、Portalサーバーが選択を実体へ結び付けるんですね。管理APIでの確認と、API本体の実行も、別の通信として追えました。',['arm','gateway','result'])

add('mako','execution','execution-lock','対象をロックするというのは、試用が終わるまで、その人の画面全体を使えなくする意味ですか？何を安定させるための処理でしょうか？')
add('ikumi','execution','execution-lock','ロックするのは選んだ台帳の対象です。対応するレコードを使って、現在所属、所有グループの承認、会社や台帳の状態などを確認し、使える資格情報を解決します。',['id','graph','sql'])
add('manabu','execution','execution-lock','候補表示と実行の間に利用許可が変わっても、古い画面の状態だけで通さないための確認ですね。ログインできることだけで、すべての選択対象を使えるわけではありません。',['time'])
add('ikumi','execution','execution-lock','このロックは、モデル実行の前に解放します。長い生成処理の完了まで台帳のロックを持ち続ける、と説明しないことも大切です。実行前の確認と実行を区別します。',['id','time'])

add('mako','execution','execution-arm','ARMの参照には、どんな順番がありますか？主キーさえ取れれば送れそうですが、まずAPIMサブスクリプションを読む理由を教えてください。')
add('ikumi','execution','execution-arm','通常はまずGETで実体を取得し、台帳とのIDや製品scopeの対応とactive状態を確認します。次に、その製品に実行対象のAPIが含まれることを確認します。',['get','product'])
add('ikumi','execution','execution-arm','条件を満たしてからlistSecretsでprimaryKeyを取得します。基本は、この三つの管理API参照です。主キーを得ることと、利用許可を確認することを一つに省略しません。',['secret'])
add('manabu','execution','execution-arm','取得した値はサーバー内だけで使います。利用者が別の実IDや秘密キーへ差し替えて試す操作ではありません。途中で確認できなければ、許可を推測して先へ進めない形です。',['scope'])

add('mako','execution','execution-input','テキスト生成の入力でapiKeyIdという名前を見ると、秘密キーを入れる欄に見えますね。実際には、何が要求本文に入っているんですか？')
add('ikumi','execution','execution-input','apiKeyIdには台帳IDが入ります。ほかにモデルや操作、入力文などを送り、Portal側で登録済みの組合せや形式、容量などを検証します。秘密キー実値は入力させません。',['request','id','validate'])
add('manabu','execution','execution-input','名前にキーとあっても、その値が秘密とは限らないんですね。説明や問い合わせでは、項目名だけで決め付けず、台帳IDを送ったのか、実値を扱う操作なのかを確かめます。',['id'])
add('ikumi','execution','execution-input','送信先も利用者が自由なURLで指定するのではなく、設定済みGatewayのoriginと、登録された操作のパスから決まります。サーバーで解決した主キーを、その要求へ付けます。',['fixed','key'])
add('mako','execution','execution-input','選んだモデルに対応する操作と、入力の条件を先に読む意味がつながりました。使える台帳を選んでも、入力形式やモデルの組合せが違えば、そのまま送れるとは限りませんね。',['validate'])

# 4. Response, data persistence, service differences, and troubleshooting.
add('mako','response','execution-response','Gatewayへ送れたら、応答は何でもそのまま画面に表示しますか？文章の生成なら、返るデータは全部同じ形だと思っていました。')
add('ikumi','response','execution-response','JSONで完了結果を受ける場合も、ストリームを順に読む場合もあります。画像や音声などではファイルも扱います。サービスごとの形式を読み、必要な検証をして画面へ返します。',['receive','validate'])
add('manabu','response','execution-response','使う側は、結果だけでなく、選んだ操作と表示される情報が合っているかを確かめます。テキスト生成なら、生成文や、返された場合の使用量などが判断の材料になります。',['display'])
add('ikumi','response','execution-response','照合用の要求IDが返る場合は、長さや文字種を確認したものを使います。失敗した上流の本文を、秘密を含むか確認せず丸ごと画面へ返す、と説明してはいけません。',['id','validate'])
add('mako','response','execution-response','結果を見るところにも、サービス別の処理があるんですね。失敗時は、どこまで到達したかと、照合できる要求IDを結び付けると調べやすくなりそうです。',['id'])

add('mako','response','execution-storage','試用の入力や生成結果は、あとでSQLの台帳から読み直せますか？選択した対象の管理データと一緒に、実行履歴も入るのでしょうか？')
add('ikumi','response','execution-storage','PortalのSQLには、台帳や承認、会社、所有グループなどがあります。候補の確認では必要なグループ登録や観測を保存しますが、試用入力、結果、キー実値を保存する専用表はありません。',['ledger','observation','notstored'])
add('manabu','response','execution-storage','だから、台帳があることと、試用内容を後から復元できることは別です。実行した文章の保管場所として台帳を案内すると、実際の保存範囲と食い違ってしまいます。',['notstored'])
add('ikumi','response','execution-storage','一方で、PortalのSQLに専用表がないことだけから、APIMや下流のログにも記録がないとは判断できません。記録の有無と範囲は、そのサービスの設定を別に確認します。',['outside'])

add('mako','response','execution-services','ここまでの通常RESTの流れは、すべての試用へそのまま当てはめられますか？音声の双方向通信やToolも、同じ一回の要求なのでしょうか？')
add('ikumi','response','execution-services','共通するのは、本人の利用許可と選択対象を確認する考え方です。入力と応答はサービス別です。テキスト、埋め込み、画像、音声などで、組み立てる要求や読み取る形式が変わります。',['rest'])
add('ikumi','response','execution-services','Realtime音声は長い双方向の接続です。中継サーバーが接続を受け、内部APIで認可して音声処理へ進みます。通常のテキスト生成のHTTP処理を、そのまま一式使う説明にはしません。',['realtime'])
add('ikumi','response','execution-services','文書解析の開始と待機はAPIMポリシーが担当します。PII MCPでは、初期化、ツール一覧、呼出し、切断を一回の試用で行います。サービス固有の段取りも確認してください。',['document','mcp'])
add('manabu','response','execution-services','同じ試用ボタンでも、裏側の通信の回数や続き方は違うんですね。まず共通の認可を押さえ、選んだサービスのガイドで、入力と完了の見方を補うと整理できます。',['rest','realtime','document','mcp'])

add('mako','response','execution-trouble','現場で試用できないと言われたら、何から見ますか？仕様が古いのか、候補がないのか、実行が止まったのかを分けて聞くとよさそうですね。')
add('ikumi','response','execution-trouble','その通りです。仕様が古ければ元定義、生成物、配備を見ます。候補が見えなければ、現在所属、SQLの承認と台帳状態、対象APIが製品に含まれるかを確かめます。',['catalog','choice'])
add('ikumi','response','execution-trouble','選べたのに実行できない場合は、実行時の再認可、APIM実体の状態や対応、サービスの入力条件を追います。Gatewayへ届いた後なら、返却結果と要求IDで処理段階を照合します。',['execution','response'])
add('manabu','response','execution-trouble','同じ「使えない」でも確認先が違いますね。秘密キーを問い合わせ本文へ貼るより、いつ、どの操作で、どの段階まで進んだかを共有すると、原因を絞り込めます。',['catalog','choice','execution','response'])

# 5. Questions: no answer outline during question turns.
add('mako','check','quiz-catalog','確認問題です。新しいモデルをカタログへ継続して反映したい場合、どの案を選びますか？生成済みJSONだけの修正、元定義からの再生成と配備、秘密キーの取替えの三つです。')
add('manabu','check','quiz-catalog','元定義を変更して、再生成と配備へ進む案Bです。カタログは、公開する契約を表示するためのものなので、画面だけでなく、元データとの対応を保つ必要があります。',['b'])
add('ikumi','check','quiz-catalog','API定義に加え、変更に応じてモデル能力情報や公開名の置換値も確認します。生成済みJSONだけの直接編集は、次回の生成で消えることがあります。',['b'])
add('mako','check','quiz-catalog','画面へ出たかだけで終わらず、どの元定義から生成され、どの配備に入ったかまで確認する、という答えですね。',['b'])

add('mako','check','quiz-id','二問目です。通常のPortal試用で、apiKeyIdへ送るのはどれでしょう？APIMの主キー、APIMサブスクリプションID、SQL台帳のIDの三つから選んでください。')
add('ikumi','check','quiz-id','案CのSQL台帳のIDです。Portalサーバーがその台帳から実APIMサブスクリプションIDを解決し、現在の利用許可や実体を確認して、主キーを取得します。',['c'])
add('manabu','check','quiz-id','表示名は人が選ぶための情報、台帳IDは選択を伝える値、秘密キーは認証に使う値でした。画面で選んだという行為と、秘密を送信する行為を混同しないでください。',['c'])
add('mako','check','quiz-id','名前にキーとあっても、apiKeyIdは秘密キー実値ではない。三つの値を区別すると、どこで何を取得しているかも説明できますね。',['c'])

add('mako','check','quiz-recheck','最後です。候補を表示した後に所属や状態が変わったかもしれません。表示時の許可で送信する、実行時に再確認する、表示名をキーにする。どれが正しい流れでしょう？')
add('manabu','check','quiz-recheck','案Bです。候補表示は、表示した時点の情報です。実行する本人の現在の所属や承認、選んだ対象の状態は、使う時点でも確認する必要があります。',['b'])
add('ikumi','check','quiz-recheck','台帳IDで対象をロックして再認可し、APIMの実体と製品内APIを確認してから主キーを得ます。候補表示だけで、実行時のAPIM状態まで保証したことにはなりません。',['b'])
add('mako','check','quiz-recheck','仕様が見える、候補に出る、いま実行できる、という段階を分けて考えるんですね。どこで止まったかを聞く理由も、これでつながりました。',['b'])

# 6. Closing conversation precedes the common logo tail.
add('mako','summary','lesson-summary','今日は、仕様を調べてからAPIを試すまでを追いました。最後に、カタログの経路と、試用する経路を、一つずつ振り返っておきましょう。')
add('ikumi','summary','lesson-summary','カタログは元定義からJSONを生成し、Portalへ同梱して表示します。試用は台帳IDで選び、再認可とARMの実体確認を通して主キーを取得し、固定Gatewayへ送ります。',['catalog','identity','execution'])
add('manabu','summary','lesson-summary','台帳ID、実APIMサブスクリプションID、秘密キーの役割を区別してください。候補の表示と実行時の許可も分けると、確認先や問い合わせの情報を適切に選べます。',['identity','execution'])
add('mako','summary','lesson-summary','次回は、発行した後のキー管理です。表示、停止、再開や利用制限を変えたとき、利用にどんな効果があるのかを確認していきます。今回はここまでです。',['next'])

# Tighten repeated framing while preserving each exchange and factual distinction.
revisions = {
 5:'仕様を調べる画面と、APIを試す画面はつながっていますよね。どちらもキーの台帳から表示するんでしょうか？',
 8:'見るための定義と、使うための許可ですね。資料と実装から二つの経路を追います。稼働環境を操作した実演とは区別しましょう。',
 9:'同梱カタログの元はどこですか？画面の説明を手で書くのか、Gatewayの定義から作るのかが気になります。',
 13:'API定義から生成して、Portalに入る順ですね。ページを開くたびに、モデルから仕様を集める経路ではないと分かりました。',
 14:'どんな順で目的のAPIへ進むとよいですか？提供元や操作が並ぶと、モデル名だけで選びたくなります。',
 17:'Tool一覧にはアプリ内の定義も使います。カタログ本体を保存するSQL表はありませんが、ログイン確認にはSQLを使います。',
 18:'モデルを足すときは、同梱JSONに行を増やせば終わりですか？そこを直接直すのが早そうにも見えます。',
 20:'追加では、公開する操作と入力条件を、配備する契約とそろえて利用者へ届けます。画面に名前を見せるだけでは終わらないんですね。',
 22:'台帳ID、APIMサブスクリプションID、秘密キーが出てきました。同じ対象なら、どれを送ってもよいのでしょうか？',
 24:'利用者は用途や会社、所有グループを見て対象を選びます。その選択をサーバーへ伝えるのが台帳IDで、秘密を入力した意味ではありません。',
 25:'二つ目は、実際のAPIMサブスクリプションを識別するIDです。SQL台帳が実IDへの対応を持ちます。台帳IDと同じ値だとは仮定しません。',
 28:'試用候補は、ログインした人の全キーですか？仕様が表示されていれば、どの対象でも使えるのでしょうか？',
 32:'候補が見えても、主キーを取得済みとは言えないんですね。APIMの各実体がactiveかは、実行時にも確認する点が分かりました。',
 39:'ロックは、試用が終わるまで画面を使えなくする意味ですか？何を安定させる処理でしょうか？',
 41:'古い画面の状態だけで通さないためですね。候補表示後に利用許可が変わることもあり、ログインだけですべての対象を使えるわけではありません。',
 42:'このロックはモデル実行前に解放します。長い生成処理の完了まで台帳のロックを持つわけではありません。確認と実行を分けて捉えます。',
 43:'ARM参照の順番を教えてください。主キーの取得前に、APIMサブスクリプションを読む理由は何ですか？',
 45:'条件を満たしてからlistSecretsで主キーを取得します。基本はこの三つの管理API参照です。キー取得と利用許可の確認は、分けて押さえましょう。',
 46:'取得した値はサーバー内で使います。利用者が実IDや秘密キーを差し替える操作ではありません。確認できない場合は、許可を推測して先へ進みません。',
 47:'apiKeyIdという名前は、秘密キーを入れる欄に見えますね。実際には、どんな要求本文になるんですか？',
 49:'名前にキーとあっても、秘密とは限らないんですね。問い合わせでも、項目名だけでなく、台帳IDを送る処理か、実値を扱う処理かを確かめます。',
 51:'操作と入力条件を先に読む意味がつながりました。使える台帳でも、モデルの組合せや形式が違えば送れるとは限りませんね。',
 52:'応答は何でもそのまま表示しますか？文章の生成なら、返るデータは同じ形だと思っていました。',
 54:'選んだ操作と表示が合っているかも確かめます。テキスト生成なら生成文や、返された場合の使用量などが判断の材料になります。',
 55:'照合用の要求IDが返る場合は、長さや文字種を確認して使います。失敗時も、秘密を含み得る上流本文を、そのまま画面へ転送する形にはしません。',
 56:'結果にもサービス別の処理があるんですね。失敗時は到達段階と、照合できる要求IDを結び付けると調べやすそうです。',
 57:'試用の入力や生成結果は、SQLの台帳から読み直せますか？管理データと一緒に実行履歴も入るんでしょうか？',
 58:'PortalのSQLには台帳、承認、会社、所有グループがあり、必要なグループ観測も保存します。一方、試用入力、結果、キー実値を保存する専用表はありません。',
 59:'台帳があることと、試用内容を復元できることは別ですね。実行した文章の保管場所として台帳を案内すると、保存範囲と食い違います。',
 61:'通常RESTの流れは、すべての試用に当てはまりますか？双方向の音声やToolも、一回の要求なのでしょうか？',
 62:'共通するのは、本人の利用許可と選択対象の確認です。入力と応答はサービス別で、テキスト、埋め込み、画像、音声などに応じて形式が変わります。',
 63:'Realtime音声は長い双方向接続です。中継サーバーが接続を受け、内部APIで認可します。通常のテキスト生成のHTTP処理を、そのまま一式使う形ではありません。',
 66:'試用できないと言われたら、何を見ますか？仕様が古い、候補がない、実行が止まる、を分けて聞くとよさそうですね。',
 69:'同じ「使えない」でも確認先が違いますね。秘密キーを貼らず、いつ、どの操作で、どの段階まで進んだかを共有すると原因を絞れます。',
 74:'二問目です。apiKeyIdで送る値はどれでしょう？APIMの主キー、実APIMサブスクリプションID、SQL台帳のIDから選んでください。',
 76:'表示名は人が選ぶ情報、台帳IDは選択を伝える値、秘密キーは認証に使う値でした。画面の選択と、秘密の送信を区別してください。',
 82:'仕様を調べてからAPIを試すまでを追いました。最後に、カタログと試用の二つの経路を振り返りましょう。',
 84:'台帳ID、実APIMサブスクリプションID、秘密キーを区別してください。候補の表示と実行時の許可も分けると、確認先や問い合わせの情報を選べます。',
}
for number,text in revisions.items():
    turn=turns[number-1]
    turn['caption']=text
    turn['speech']=speech(text)
    turn['subtitleChunks']=chunk(text)
    turn['subtitleLines']=turn['subtitleChunks'][0]['lines']

script['turns'] = turns
(HERE/'script.json').write_text(json.dumps(script,ensure_ascii=False,indent=2)+'\n')
summary={'turns':len(turns),'speechCharacters':sum(len(t['speech']) for t in turns),
         'captionCharacters':sum(len(t['caption']) for t in turns),
         'openingCharacters':sum(len(t['speech']) for t in turns if t['chapterId']=='opening'),
         'viewIds':list(views),
         'bodyTurns':len([t for t in turns if t['chapterId']!='opening'])}
print(json.dumps(summary,ensure_ascii=False,indent=2))
