"""Episode-local authored dialogue and stable teaching settings views."""
import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
source = json.loads((HERE / 'script.json').read_text())
source['status'] = 'draft'
source['sourceReferences'] = [
    {'file': '../02-external-protection.html', 'anchor': a}
    for a in ['apim-scope', 'apim-authentication', 'apim-contract', 'apim-input']
] + [
    {'file': '../../../apps/gateway/apim/' + f, 'purpose': p}
    for f, p in [
        ('policies/service.xml', '標準検証後の共通入口'),
        ('policies/gateway.xml', 'API共通契約と入力検査'),
        ('apis/openai-inference-v1.json', 'chatとvisionの契約'),
        ('policies/pii.xml', 'Operation固有検査'),
        ('policies/speech-ssml-guard.xml', 'baseより前の宣言ガード'),
        ('policies/document-analysis.xml', '文書入力とinboundでの実行')
    ]
]
chapters = [
 ('opening','今回の問い','キーが有効でも拒否される理由を問いにする','apim-authentication'),
 ('identity','入口のキーは何を証明するか','標準検証・送り方・実行主体を区別する','apim-authentication'),
 ('contract','Operation・model・profileの契約','chatとvisionの具体例で入力契約を説明する','apim-contract'),
 ('input','入力検査の範囲を確かめる','共通サイズ検査と専用ガードの分担を説明する','apim-input'),
 ('pipeline','ポリシーの実行順を追う','baseによる継承と途中停止の位置を追う','apim-scope'),
 ('check','三つの要求で理解を確かめる','キー・入力契約・実行位置から判断する','apim-contract'),
 ('summary','まとめと次の問い','到達点を整理して利用制限と下流認証へつなぐ','apim-scope')
]
source['chapters'] = [dict(id=i,title=t,goal=g,sourceRefs=[dict(file='../02-external-protection.html',anchor=a)]) for i,t,g,a in chapters]

def view(key, title, section, rows, notice='', columns=None):
    return dict(layout='settings',viewId='lesson11-'+key,heading=section,
                areaLabel='APIM 教材用設定',title=title,kind='公開定義に基づく教材',
                section=section,menu=['入口認証','API契約','入力検査','実行順','確認問題'],
                notice=notice,table=dict(columns=[dict(label=x,width=w) for x,w in (columns or [('確認項目',300),('設定・判断',672)])],
                                        rows=[dict(id=i,cells=c) for i,c in rows]),highlightRows=[])

views = {}
views['gates'] = view('gates','通常の業務API | 確認する三段階','入口認証',[
 ('key',['キーの標準検証','有効なAPIMサブスクリプションとAPI範囲']),
 ('contract',['APIの入力契約','Operation・model・profileの条件']),
 ('order',['ポリシー実行順','どの段階で拒否・準備・呼出しを行うか'])], 'WAF通過や有効なキーだけで、業務処理の成功は決まらない')
views['standard'] = view('standard','通常業務API | APIMサブスクリプション','入口認証',[
 ('required',['API登録','subscriptionRequired: true']),
 ('standard',['APIM標準検証','キーの有効性・状態・APIへの利用範囲']),
 ('common',['共通入口ポリシー','送信形式と検証済みSubscription ID']),
 ('product',['通常キーのAPI範囲','released-apis製品への関連付け'])], '未releaseのAPIも、検証用製品に所属する保護APIとして配備され得る')
views['key-format'] = view('key-format','共通入口 | キーの送り方','入口認証',[
 ('header',['公開設定例のヘッダー','api-key : <キー値>']),
 ('one',['個数と値','設定したキー用ヘッダーが1個・空でない']),
 ('query',['URLのquery','subscription-key / api-key を拒否']),
 ('alias',['任意の別名・Bearer','自動変換して受け入れる実装ではない'])], 'core-subscription-key-headerとAPI側の設定をそろえる')
views['principal'] = view('principal','APIMの実行主体 | 個人ログインと区別','入口認証',[
 ('id',['検証済みの主体','subscription:<APIMサブスクリプションID>']),
 ('pair',['主キーと副キー','同じ主体・同じ利用枠']),
 ('person',['個人の本人確認','Portalログインや追加の本人接続は別処理']),
 ('jwt',['共通入口の前提','利用者Entra JWTを一律に検証する構成ではない'])], 'キーの実値とAPIMサブスクリプションIDも区別する')
views['ingress'] = view('ingress','共通入口 | AppGW経由の確認','入口認証',[
 ('enabled',['入口証明を有効化','core-l7-ingress-enabled']),
 ('proof',['AppGWが設定する値','X-Core-L7-Ingress の値と一意性']),
 ('deny',['不一致の場合','403で停止']),
 ('grpc',['native gRPC','登録した専用TCP経路として扱う'])], '入口経路の確認と、利用者個人の本人確認を混同しない')
views['definition'] = view('definition','API JSON | 公開契約の正本','API契約',[
 ('operation',['公開操作','HTTP方式・公開パス・Operation']),
 ('constraints',['操作ごとの制約','authorization.operationConstraints']),
 ('deploy',['配備時の反映','BicepがAPIポリシーへ条件を埋め込む']),
 ('scope',['二つの利用範囲','API範囲はSubscription / Product、入力はAPI契約'])], '入力契約は、APIMサブスクリプションごとの権限表と同じものではない')
views['chat'] = view('chat','OpenAI HTTP | Chat Completions','API契約',[
 ('method',['方式・Operation','POST / chat-completions']),
 ('path',['公開パス','/models/openai/v1/chat/completions']),
 ('profile',['既定profile','chat / text入力']),
 ('size',['本文上限','32 KiB = 32,768 bytes']),
 ('model',['model','選択profileの許可一覧と照合'])], 'ヘッダーでprofileを省略した場合も、既定の契約で検査する')
views['vision'] = view('vision','同じChat Operation | profileの比較','API契約',[
 ('select',['指定','省略時 chat','X-Egakumi-Profile: vision']),
 ('input',['入力種別','text','text + image']),
 ('model',['許可model','chatの許可一覧','gpt-6.1-sol']),
 ('size',['本文上限','32,768 bytes','2,000,000 bytes'])], '資料・API定義にある契約の比較。稼働環境の取得値ではない', [('確認項目',180),('chat',330),('vision',462)])
views['match'] = view('match','要求とprofile | 条件を組み合わせて照合','API契約',[
 ('unique',['profile指定','一意で、定義済みのprofileを選ぶ']),
 ('model',['model','選択profileの許可一覧に含まれる']),
 ('type',['入力種別','text / image / file / audio等の許可条件']),
 ('bytes',['本文・指定項目','実バイト数と、設定された項目の条件']),
 ('quota',['利用枠との関係','入力profileの変更で利用枠は切り替わらない'])], 'すべてのAPIで、全入力属性を一律に検査する仕組みではない')
views['destination'] = view('destination','API契約 | 転送先も定義へ結び付ける','API契約',[
 ('registered',['転送先','登録済みbackendとOperation']),
 ('uri',['上流URI','Operation・profileに対応する定義']),
 ('fixed',['固定契約の例','Graph /me、文書解析の結果照会先']),
 ('free',['要求の値','任意の下流ホストを自由に指定させない'])], '入力profileは任意URLへの転送機能ではない')
views['schema'] = view('schema','実行時の検査 | 登録schemaとの違い','入力検査',[
 ('registered',['APIのschema登録','仕様として登録すること']),
 ('common',['共通JSON経路','実本文サイズ・設定した数値や選択肢等']),
 ('validate',['通常Operationの検査','validate-content：サイズ強制のみ']),
 ('mime',['JSON Content-Type','共通経路で一律に検査するわけではない'])], '完全なschema検証や、すべての属性の検査と説明しない')
views['pii'] = view('pii','PII | 専用ガードの代表例','入力検査',[
 ('body',['公開入力','JSON {text, language} / 32 KiB']),
 ('fields',['許可する項目','text と language だけ']),
 ('checks',['専用検査','型・空文字・許可言語・不要query']),
 ('convert',['検査後の変換','Language API用の要求に組み直す'])], '共通入力検査に加え、Operationに固有の処理を確認する')
views['other-guards'] = view('other-guards','形式ごとの入力 | 専用ガード','入力検査',[
 ('speech',['Speech TTS','SSML 8 KiB / DTD・ENTITYの実宣言を拒否']),
 ('document',['文書解析','PDF・画像 1 MiB / MIME・実サイズ・先頭形式']),
 ('image',['OpenAI画像編集','multipart本文全体 4 MiB / boundary等も含む']),
 ('downstream',['検査の限界','SSML全体の意味・schemaは下流の検査も必要'])], '代表例に絞る。全MIME・全エラーコードの一覧は参照資料へ残す')
views['waf'] = view('waf','前段とAPIM | 上限の測り方をそろえる','入力検査',[
 ('image',['画像編集','WAF: ファイル部分 4 MiB','APIM: 本文全体 4 MiB']),
 ('vision',['vision','WAF: 2000 KiB','APIM: 2,000,000 bytes']),
 ('document',['文書','WAF: 1025 KiB','APIM: 1 MiB'])], '同じ数値・似た単位でも、許容する要求が同一とは判断しない',[('対象',156),('WAFの受入枠',408),('APIMの契約上限',408)])
views['streams'] = view('streams','検査範囲 | 接続と内容を区別','入力検査',[
 ('ws',['WebSocket','接続開始時のonHandshake']),
 ('grpc',['gRPC','ストリーム内容の全検査とはしない']),
 ('rtc',['WebRTC','音声メディアをHTTP入力ガードで全検査しない']),
 ('sse',['通常RESTのSSE応答','中継を維持。全イベントschemaの検証ではない'])], '今回の主例は通常REST。通信方式ごとの検査範囲を確認する')
views['finish'] = view('finish','停止・例外・業務呼出し | 実行位置','実行順',[
 ('rest',['通常REST','inbound → backendのforward-request → outbound']),
 ('document',['文書解析','inboundのsend-requestで開始・照会']),
 ('return',['明示的な拒否・終了','return-responseが以降を停止']),
 ('error',['実行例外等','on-errorへ分岐'])], 'return-responseが常にoutboundやon-errorを通るとは限らない')
views['q1'] = view('q1','確認1 | 同じキーで画像を送る','確認問題',[
 ('key',['前提','有効なキー / 同じChat Operation']),
 ('request',['今回の要求','画像あり / profile指定を省略']),
 ('a',['候補A','キーが有効なら画像も通る']),
 ('b',['候補B','既定profileと入力種別を照合する'])], '前提のキー、profile、入力種別をもとに判断する')
views['q2'] = view('q2','確認2 | 登録schemaを信用してよい？','確認問題',[
 ('request',['担当者の説明','schemaを登録したので全属性を実行時に検証する']),
 ('a',['確認先A','登録schemaの有無だけを見る']),
 ('b',['確認先B','実行するXMLと専用ガードの条件を見る'])], 'schema登録と実行時検査の関係を確認する')
views['q3'] = view('q3','確認3 | PIIの固有検査で拒否','確認問題',[
 ('request',['今回の要求','共通入口とAPI処理の後、PII専用検査で拒否']),
 ('a',['判断A','拒否されたので、計数も準備も絶対に未実行']),
 ('b',['判断B','baseの位置と、拒否までの処理を確認する'])], '専用検査で拒否された要求の到達段階を考える')
views['summary'] = view('summary','第11回 | 要求を説明する三つの問い','確認問題',[
 ('key',['誰の利用か','標準検証済みのAPIMサブスクリプション']),
 ('contract',['何を送れるか','Operation・model・profileと入力条件']),
 ('order',['どこまで進んだか','base・固有検査・途中停止の実行位置']),
 ('next',['次回の問い','利用回数・トークン量・下流認証を分ける'])], '公開設定例とリポジトリ定義に基づく教材。稼働環境の実測ではない')

turns=[]
pronunciations = [
 ('chat-completions','チャット・コンプリーションズ'),
 ('IDPS','アイディーピーエス'),('Speech TTS','スピーチ・ティーティーエス'),
 ('base','ベース'),('query','クエリー'),
 ('X-Egakumi-Profile','エックス・エガクミ・プロファイル'),
 ('X-Core-L7-Ingress','エックス・コア・エルセブン・イングレス'),
 ('subscriptionRequired','サブスクリプション・リクワイアード'),
 ('context.Subscription.Id','コンテキストのサブスクリプション・アイディー'),
 ('operationConstraints','オペレーション・コンストレインツ'),
 ('validate-content','バリデート・コンテント'),
 ('return-response','リターン・レスポンス'),('forward-request','フォワード・リクエスト'),
 ('send-request','センド・リクエスト'),('onHandshake','オン・ハンドシェイク'),
 ('on-error','オン・エラー'),('Content-Type','コンテント・タイプ'),
 ('api-key','エーピーアイ・キー'),('subscription-key','サブスクリプション・キー'),
 ('released-apis','リリースド・エーピーアイズ'),('Bearer','ベアラー'),
 ('gpt-6.1-sol','ジーピーティー・ろくてんいち・ソル'),
 ('<base />','ベース'),('profile','プロファイル'),('vision','ビジョン'),('chat','チャット'),
 ('model','モデル'),('Operation','オペレーション'),('Product','プロダクト'),
 ('Subscription','サブスクリプション'),('inbound','インバウンド'),('outbound','アウトバウンド'),
 ('backend','バックエンド'),('true','トゥルー'),('text','テキスト'),('language','ランゲージ'),
 ('multipart','マルチパート'),('boundary','バウンダリー'),('schema','スキーマ'),
 ('bytes','バイト'),('KiB','キビバイト'),('MiB','メビバイト'),
 ('AppGW','アップゲートウェイ'),('Entra','エントラ'),('JWT','ジェイダブリューティー'),
 ('SSML','エスエスエムエル'),('DTD','ディーティーディー'),('ENTITY','エンティティー'),
 ('PII','ピーアイアイ'),('MIME','マイム'),('JSON','ジェイソン'),('Bicep','バイセップ'),
 ('XML','エックスエムエル'),('HTTP','エイチティーティーピー'),('REST','レスト'),
 ('SSE','エスエスイー'),('URI','ユーアールアイ'),('URL','ユーアールエル'),
 ('gRPC','ジーアールピーシー'),('TCP','ティーシーピー'),('WebRTC','ウェブアールティーシー'),
 ('WebSocket','ウェブソケット'),('Graph','グラフ'),('Language API','ランゲージ・エーピーアイ'),
 ('accepted','アクセプテッド'),('API','エーピーアイ'),('APIM','エーピーアイエム'),
 ('ID','アイディー'),('WAF','ワフ'),('POST','ポスト'),('PDF','ピーディーエフ'),
]
# Longest first prevents API from altering APIM, and compound identifiers from splitting.
pronunciations.sort(key=lambda p: -len(p[0]))
pronunciation_pattern = re.compile('|'.join(re.escape(a) for a,b in pronunciations))
pronunciation_map = dict(pronunciations)

def chunks(text):
    # Keep Latin identifiers together and every displayed word in the timed captions.
    pieces = re.findall(r'.+?(?:。|？|！|$)',text)
    result=[]
    for piece in pieces:
        lines=[]
        current=''
        for token in re.findall(r'[A-Za-z0-9_/-]+|.',piece):
            if len(current)+len(token)>32 and current and token not in '、。？！':
                lines.append(current)
                current=''
            current+=token
        if current: lines.append(current)
        for i in range(0,len(lines),2):
            page=lines[i:i+2]
            block=''.join(page)
            result.append(dict(caption=block,lines=page,weight=len(block)))
    return result

def add(chapter, key, speaker, caption, highlight=None, speech=None):
    visual=copy.deepcopy(views[key]) if key in views else dict(layout='hologram',heading=source['topic'])
    if key=='order':
        visual=dict(layout='svg',viewId='lesson11-policy-order',heading='通常RESTの実行順',svg='diagrams/policy-order'+(f'-{highlight}' if highlight else '')+'.svg')
    elif key=='inheritance':
        visual=dict(layout='svg',viewId='lesson11-inheritance',heading='baseの位置で親の処理へ入り、元の位置へ戻る',svg='diagrams/inheritance.svg')
    elif highlight:
        visual['highlightRows']=[highlight] if isinstance(highlight,str) else highlight
    c=chunks(caption)
    turns.append(dict(id=f'lesson11-turn-{len(turns)+1:03}-{speaker}',speaker=speaker,chapterId=chapter,
                      speech=speech or pronunciation_pattern.sub(lambda m:pronunciation_map[m[0]],caption),caption=caption,
                      subtitleLines=c[0]['lines'],subtitleChunks=c,gapAfterSeconds=.16,
                      delivery={'mako':'直前の話を受けて、疑問や理解を短く自然に確かめる。',
                                'manabu':'目的と判断の理由を、同僚への返答として穏やかに話す。',
                                'ikumi':'質問に答える会話として、技術用語の前後を軽く区切り、通常速度で明瞭に話す。'}[speaker],visual=visual))

dialogue = [
('opening','opening',[
 ('mako','有効なAPIキーを付けたのに、画像を送ると拒否される。イクミさん、これって認証の失敗なんですか？'),
 ('ikumi','必ずしもそうではありません。キーの確認と、そのAPIへ何を送れるかという入力の契約は、別々に判断しています。'),
 ('manabu','今日は、使えるキー、送れる入力、処理の順番をつなぎましょう。拒否されたとき、どこまで進んだかを説明できるのがゴールです。'),
 ('mako','キーさえあれば全部通る、ではないんですね。一つの要求で確かめていきましょう。')]),
('identity','gates',[
 ('mako','前回はFirewallの許可とIDPSの検知を分けました。そこを通ってAPIMへ着けば、次は何を確認するんですか？'),
 ('manabu','まず、どのAPIMサブスクリプションの利用なのか。次に、その操作で許された入力なのか。そして、処理がどの順番で動くかです。','key'),
 ('ikumi','WAFやFirewallは入口の防御です。APIMではAPIごとの契約を判断します。前段を通ったことも、有効なキーを持つことも、最終的な業務成功の証明にはなりません。','contract'),
 ('mako','入口の通過、APIの契約、業務の結果を、段階ごとに見るわけですね。','order')]),
('identity','standard',[
 ('mako','まずキーです。この教材の通常の業務APIでは、何が認証を担当していますか？'),
 ('ikumi','APIはsubscriptionRequiredをtrueにして登録します。APIMの標準検証が、キーの有効性や状態、そのAPIを使える範囲を、カスタムポリシーより先に確認します。','required'),
 ('manabu','使えるAPIの範囲も大事ですね。有効なキーだからといって、基盤に登録された全APIを使えるとは限りません。','standard'),
 ('ikumi','通常キーはreleased-apis製品に関連したAPIを使います。未releaseのAPIも、検証用製品に属する保護APIとして配備され得ます。未releaseを匿名公開という意味にしないでください。','product')]),
('identity','key-format',[
 ('mako','キーの値さえ正しければ、送り方は何でもよいんでしょうか。URLに入れる方法も見かけます。'),
 ('ikumi','公開設定例はapi-keyヘッダーです。API側と共通入口側で名前をそろえ、共通ポリシーは、設定したヘッダーが一個で空でないことと、検証済みのIDを確認します。','header'),
 ('manabu','URLのqueryに入れたsubscription-keyやapi-keyは拒否します。便利そうな別の送り方を、利用者の判断で足してよい契約ではありません。','query'),
 ('ikumi','任意の別名ヘッダーやBearer形式も、自動的に変換して受け入れる実装ではありません。キーの値と、公開した送り方の両方をそろえます。','alias')]),
('identity','principal',[
 ('mako','検証済みIDというのは、APIを使っている本人のユーザーIDですか？'),
 ('ikumi','ここで確定するのはAPIMサブスクリプションIDです。実行主体を、subscriptionとそのIDで表します。キーの実値そのものを、本人のIDとして扱うわけではありません。','id'),
 ('manabu','主キーと副キーは同じAPIMサブスクリプションに属するので、同じ主体、同じ利用枠です。副キーへ替えれば別の利用者になる、とは考えません。','pair'),
 ('ikumi','Portalのログイン、追加の本人接続、下流への認証は別処理です。共通入口で利用者のEntra JWTを一律に検証している、とも説明しません。','person')]),
('identity','ingress',[
 ('mako','本人確認とは別に、AppGWを通ったかも確認するんですね。'),
 ('ikumi','入口証明を有効にした定義では、AppGWが設定する専用ヘッダーの値と一意性を確認し、不一致なら403で停止します。APIキーの検証とは違う、入口経路の確認です。','proof'),
 ('manabu','この値を知っていれば本人だ、という意味ではありません。どの経路から来たかと、どのAPIMサブスクリプションの利用かを、混ぜずに読みます。','deny'),
 ('ikumi','またnative gRPCは、登録した専用TCP経路として扱います。すべての通信方式が、通常のHTTP要求と同じ入口確認を通るとは説明しません。','grpc')]),
('contract','definition',[
 ('mako','次は、キーが有効でも入力を拒否する条件です。どこに正本がありますか？'),
 ('ikumi','各APIのJSON定義です。公開パス、HTTP方式、Operation、認可モード、入力制約を持ちます。操作ごとの条件を、配備時にBicepがAPIポリシーへ埋め込みます。','constraints'),
 ('manabu','APIMサブスクリプションや製品が、使えるAPIの範囲を決める。それに加えてAPIの契約が、その操作へ送れる入力を決める。二つを分けると整理できます。','scope'),
 ('mako','認証できたから、入力の中身も何でも自由になる、というわけではないんですね。','operation')]),
('contract','chat',[
 ('mako','最初の疑問に戻りましょう。Chat Completionsへ送る場合、何をそろえますか？'),
 ('ikumi','公開パスに対するPOSTが、この例のchat-completionsというOperationです。同じパスでも、HTTP方式と登録された操作を含めて契約を見ます。','method'),
 ('ikumi','profileを省略したときの既定値はchatです。テキスト入力を受け、本文上限は32キビバイト、実際の数値では三万二千七百六十八バイトです。','size'),
 ('manabu','modelも、そのprofileの許可一覧と照合します。本文が小さければ、どんなモデル名でも受け付ける、という条件ではありません。','model')]),
('contract','vision',[
 ('mako','では、同じChatの操作へ画像を足すときは、何を変えるんでしょう。'),
 ('ikumi','入力profileとしてvisionを選びます。専用ヘッダーで指定し、画像を許可する入力種別と、そのprofileの許可モデルをそろえます。キーの変更で画像対応にするわけではありません。','select'),
 ('ikumi','この公開定義のvisionは、テキストと画像を許可し、モデルはgpt-6.1-solです。本文上限は二百万バイト。chatの本文上限とは別の値で検査します。','model'),
 ('manabu','profileだけvisionにしても、モデルや入力種別が合わなければ通りません。画面の列をひとまとまりの契約として読んでください。','input')]),
('contract','match',[
 ('mako','画像が通らないとき、profileをとりあえず書き換えて試す、だけでは足りなさそうです。'),
 ('ikumi','はい。指定が一意で、存在するprofileか。そのprofileでmodelと入力種別が許可されるか。本文の実バイト数や、指定された項目の条件にも合うかを順に照合します。','unique'),
 ('manabu','ここでいうprofileは入力契約です。利用上限を持つ、APIMサブスクリプションプロファイルとは別物です。名前が似ていても、管理する対象が違います。','quota'),
 ('mako','入力profileのヘッダーを替えても、同じAPIMサブスクリプションの利用枠は切り替わらない。ここは区別して覚えます。','quota')]),
('contract','destination',[
 ('mako','profileを選べるなら、下流のURLも利用者が自由に選べるんですか？'),
 ('ikumi','転送には、登録済みbackendとOperation、profileに対応する上流URIを使います。modelやprofileの値から、任意の下流ホストへ飛ばせる構成にはしません。','uri'),
 ('manabu','Graphの本人向け操作や文書解析の結果照会も、固定した契約に限定します。どのサービスへ何を依頼するかまで、公開契約と結び付ける考え方です。','fixed')]),
('input','schema',[
 ('mako','APIにはschemaも登録されていますよね。それなら、JSONの全項目を完全に検証してくれるんでしょうか。'),
 ('ikumi','登録と実行時の検証は分けます。共通JSON経路で見るのは、保持した本文の実サイズと、設定された数値や選択肢、配列件数などの条件です。全属性を一律に検証するものではありません。','common'),
 ('ikumi','通常Operationのvalidate-contentはサイズを強制する設定で、schema検証の子要素はありません。JSONのContent-Typeも、共通経路で一律に検査するわけではありません。','validate'),
 ('manabu','schemaがあるから安心、で調査を止めず、実際に動くポリシーがどの条件を確認するかを読みます。専用のガードがある操作は、その処理まで確認しましょう。','registered')]),
('input','pii',[
 ('mako','専用ガードの違いが分かる例を、一つ見せてください。'),
 ('ikumi','PIIの公開入力は、textとlanguageを持つJSONで、上限は32キビバイトです。この二項目だけを許し、型、空文字、許可言語、不要なqueryなどを専用処理で検査します。','fields'),
 ('manabu','同じJSONでも、Chatの入力契約とは形が違いますね。使う人に必要な小さい入力を公開し、余分な条件を受け入れないようにしているわけです。','body'),
 ('ikumi','その後でLanguage API用の要求に変換します。共通入力検査だけを読んで、PII固有の条件や変換まで分かったとはしないでください。','convert')]),
('input','other-guards',[
 ('mako','JSON以外にも、それぞれ専用の確認があるんですね。'),
 ('ikumi','Speech TTSはSSMLの実宣言として、DTDやENTITYを拒否するガードがあります。上限は8キビバイト。ただし、このガードだけでSSML全体の意味やschemaを検証できるわけではありません。','speech'),
 ('ikumi','文書解析は許可したPDFや画像について、MIME、実バイト数、ファイル先頭の形式などを確認します。上限は1メビバイトで、結果の照会先も固定ホストへ限定します。','document'),
 ('manabu','画像編集の4メビバイトは、multipartの本文全体です。ファイルだけでなくboundaryや他の項目も含みます。サイズの数値と一緒に、何を測る値なのかも確認しましょう。','image')]),
('input','waf',[
 ('mako','前段のWAFでサイズ検査を通っても、APIMでは大きすぎることがあるんですか？'),
 ('ikumi','あります。画像編集なら、WAFの4メビバイトはファイル部分、APIMは本文全体です。ファイルが上限内でも、他の項目を含む本文では超える可能性があります。','image'),
 ('manabu','visionもWAFは二千キビバイト、APIMは二百万バイトです。似た数値だから同じ上限、と読み替えないことが大切ですね。','vision'),
 ('ikumi','前段の受入枠と、操作ごとの契約上限は別の判断です。何のサイズか、単位は何か、どこで測るかをそろえて説明します。','document')]),
('input','streams',[
 ('mako','ここまでのHTTP入力ガードで、接続後の内容まで全部見ている、と考えてよいですか？'),
 ('ikumi','いいえ。WebSocketは接続開始時のonHandshakeです。接続後のフレームやgRPCのストリーム、WebRTCの音声を、このガードで全検査する構成ではありません。','ws'),
 ('manabu','通常RESTのSSE応答も、中継を維持する設計です。全部読み切って、すべてのイベントのschemaを検証しているわけではありません。通信方式ごとの境界を守ります。','sse')]),
('pipeline','order',[
 ('mako','検査の種類は分かりました。でも、共通、API、Operationと書かれた順番に動くんでしょうか。'),
 ('ikumi','スコープ名の一覧を、そのまま実行順にしたものではありません。この図は通常RESTの主な流れです。最初は標準キー検証。その後にカスタムポリシーの共通入口が来ます。',1),
 ('ikumi','続いてAPI共通の入力契約を確認し、利用枠や下流認証の準備へ進みます。ここでは詳細より、専用検査より先に実行される部分があることを押さえてください。',4),
 ('manabu','その後でOperation固有の検査に戻り、業務呼出しへ進む。下流認証の準備と、モデルやツールを実際に業務で呼ぶことは、別の段階なんですね。',5)]),
('pipeline','inheritance',[
 ('mako','なぜAPIの処理の後に、Operationへ戻るんですか。どこで順番が決まりますか？'),
 ('ikumi','Operationのinboundにある<base />の位置で、親であるAPIの処理を取り込みます。さらにAPIの<base />で、共通入口を継承します。親の処理が終わると、元の続きへ戻ります。'),
 ('manabu','階層名より、baseをどこに置いたかが大切なんですね。前に学んだWAFのパス別ポリシーの置換とは、同じ読み方にしないわけです。'),
 ('ikumi','API側は共通認証の完了マーカーも確認し、なければ503で停止します。また、Productポリシーだけに共通認証を置く設計にはしていません。')]),
('pipeline','order',[
 ('mako','それだと、PIIの専用検査で拒否されても、その前の処理は済んでいますか？'),
 ('ikumi','はい。通常のOperationやPII、文書のbaseでは、API側の利用枠、下流認証の準備、acceptedの記録までが先に動く場合があります。後の専用検査で拒否されても、計数や内部照会が未実行とは限りません。',4),
 ('manabu','拒否された要求は、何の処理もしていない、とは言えないんですね。一方で、認証の準備をしただけで、モデルへの業務呼出しが成功したとも言えません。',6),
 ('mako','拒否した場所までの処理と、その先にある業務呼出しを分ける。ログのacceptedも最終成功と読み違えないようにします。',5)]),
('pipeline','inheritance',[
 ('mako','この順番には例外もあるんですよね。先ほどのSSMLガードはどこですか？'),
 ('ikumi','SSMLの宣言ガードは、Operationのbaseより前にあります。したがって共通入口や利用枠、下流認証準備より前です。ただし、APIMの標準キー検証は、このガードより先に行われます。'),
 ('manabu','同じ専用検査という呼び名でも、XMLのどこに置かれたかで前後が変わる。全部のAPIが同じ順序だ、と一つの図だけで断定しないことですね。')]),
('pipeline','finish',[
 ('mako','業務呼出しを探すときは、backendだけを見ればよいでしょうか。'),
 ('ikumi','通常RESTはbackendのforward-requestで転送します。一方、文書解析はinboundのsend-requestで開始や照会を行い、return-responseで終了します。実呼出しが常にbackendにあるとは限りません。',['rest','document']),
 ('ikumi','return-responseは、その場で以降の処理を止めます。常にoutboundやon-errorを通るわけではありません。実行例外などでon-errorへ分岐する場合と、明示的な終了を分けます。','return'),
 ('manabu','処理名だけでなく、どの段階で実行し、何が終了条件かまで追うと、何を確認できて何が未確認かを説明できますね。','error')]),
('check','q1',[
 ('mako','確認問題です。有効なキーで同じChatの操作へ画像を送り、profileは省略しました。キーが正しいので、画像も受け付けられるでしょうか。'),
 ('manabu','まず既定profileを確認します。省略時はchatなので、画像を許可した入力契約にはなっていません。有効なキーだけでは、画像を受け付ける条件を満たせません。','b'),
 ('ikumi','visionを明示する場合も、対応モデル、入力種別、本文サイズをそろえます。profileの変更は、APIMサブスクリプションの利用枠の変更ではありません。','b')]),
('check','q2',[
 ('mako','二問目です。schemaが登録済みなので、全属性を実行時に検証している、という説明を受けました。どこを確認しますか？'),
 ('ikumi','実際のXMLと専用ガードの条件です。通常Operationのvalidate-contentはサイズ強制で、完全なschema検証ではありません。共通経路と固有処理の両方を確認します。','b'),
 ('manabu','登録された仕様と、実行される検査を分ける。検査している、と言うときには、何の条件をどこで判断するのかまで答えましょう。','b')]),
('check','q3',[
 ('mako','最後です。PIIの専用検査で拒否されました。業務呼出しが止まったので、利用回数の計数も、認証の準備も絶対に行われていない、と判断できますか？'),
 ('ikumi','判断できません。OperationのbaseからAPI側の処理を先に実行しているため、計数や内部照会が済んでいる場合があります。拒否地点と、それより前の処理を照合します。','b'),
 ('manabu','下流の業務処理に進まなかったことと、入口から何もしていないことは違います。認証準備やacceptedを、最終成功とするのも同じように避けます。','b')]),
('summary','summary',[
 ('mako','今日の説明を三つの問いに整理します。誰の利用か、何を送れるか、どこまで進んだか、ですね。','key'),
 ('ikumi','誰の利用かは、標準検証済みのAPIMサブスクリプション。何を送れるかは、Operation、model、profileと入力条件。処理の到達点は、baseと専用検査、途中停止の位置から追います。','contract'),
 ('manabu','基盤管理者は、公開定義を根拠に、一つの要求について説明できることが大切です。今回の画面は教材用で、稼働環境から取得して検証した結果ではありません。','order'),
 ('mako','次回は、同じキーでどれだけ使えるか。回数、トークン量、一要求の生成上限と、下流認証の分担を見ていきます。ありがとうございました。','next')])
]
for chapter,key,lines in dialogue:
    for line in lines:
        add(chapter,key,*line)
source['turns']=turns
if '--final-cut' in sys.argv:
    omitted=set(range(21,25)) | set(range(41,44)) | set(range(60,63))
    source['firstCut']['omitTurnIds']=[t['id'] for i,t in enumerate(turns,1) if i in omitted]
    source['firstCut']['reason']='入口経路・固定転送先・ストリーム境界の補足は参照資料へ残し、問答ごと省略。到達点と確認問題を保ち約15分へ整理する。'
    for i in [25,44,63]:
        t=turns[i-1]
        t['id']=t['id'].replace(f'-{i:03}-',f'-{i:03}r2-')
        t['rerecordingReason']='問答の省略に伴い直前文脈styleを更新。本文は不変。元音源は保持。'
    source['status']='ready-for-generation'
(HERE/'script.json').write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(turns=len(turns),speechCharacters=sum(len(t['speech']) for t in turns),captionCharacters=sum(len(t['caption']) for t in turns)),ensure_ascii=False))
