#!/usr/bin/env python3
"""Author episode 12 only; all on-screen settings are repository examples."""
import copy, json, re
from pathlib import Path
HERE=Path(__file__).resolve().parent
base=json.loads((HERE/'script.json').read_text())
base.update(status='ready-for-generation',visualStyle='teaching-settings-v2')
refs=[{'file':'../02-external-protection.html','anchor':a} for a in ['apim-rate','apim-tokens','apim-backend-auth']]
refs += [{'file':'../../../apps/gateway/apim/'+p,'purpose':why} for p,why in [
('policies/rate-limit.xml','検証済みサブスクリプションIDに集約する回数制限'),
('policies/subscription-profile.xml','SQLプロファイル・30秒キャッシュ・取得障害503'),
('policies/model-limits.xml','1要求の出力上限パラメーター検査'),
('policies/token-limit.xml','対応操作のTPM・期間累積quota'),
('model-capabilities.json','モデル別の生成上限と確認状態'),
('api-publication.bicep','Chat/Responsesのトークン制限対象選択'),
('apis/openai-inference-v1.json','chat/compatible/Responsesの公開契約'),
('policies/gateway.xml','入口資格情報の除去と下流認証準備'),
('policies/credential-connection.xml','本人接続証明の照合とCM認可'),
('policies/credential-session-use.xml','本人接続の利用確定')]]
base['sourceReferences']=refs
chapters=[('opening','今回の問い','有効なキーと利用許可・下流接続を分ける','apim-rate'),('purpose','何を、誰の枠で制限するか','回数・トークン量・1要求の上限を区別する','apim-rate'),('rate','回数制限の設定と計数','プロファイルの状態と計数時点を説明する','apim-rate'),('tokens','生成上限とトークン利用量','入力契約・TPM・期間quotaの違いを説明する','apim-tokens'),('auth','入口と下流の認証','入口キーの除去と下流専用資格情報を説明する','apim-backend-auth'),('walkthrough','一つの要求を追う','入力検査から下流までの判定を順に追う','apim-backend-auth'),('check','理解を確かめる','3つの誤解を具体例で解く','apim-rate'),('summary','まとめと次の問い','制限と認証の分担を振り返る','apim-backend-auth')]
base['chapters']=[{'id':i,'title':t,'goal':g,'sourceRefs':[{'file':'../02-external-protection.html','anchor':a}]} for i,t,g,a in chapters]
menu=['制限の単位','回数制限','生成上限','トークン利用量','下流認証','確認問題','まとめ']
views={}
def table(key,title,section,cols,rows,notice='',widths=None):
    views[key]={'layout':'settings','viewId':'lesson12-'+key,'areaLabel':'APIM / 利用制限と下流認証','heading':title,'title':title,'kind':'教材用ポリシー設定','section':section,'menu':menu,'notice':notice,'table':{'columns':[{'label':c,**({'width':widths[i]} if widths else {})} for i,c in enumerate(cols)],'rows':[{'id':r[0],'cells':r[1:]} for r in rows]},'highlightRows':[]}
def fields(key,title,section,rows,notice=''):
    views[key]={'layout':'settings','viewId':'lesson12-'+key,'areaLabel':'APIM / 利用制限と下流認証','heading':title,'title':title,'kind':'教材用ポリシー設定','section':section,'menu':menu,'notice':notice,'fields':[{'id':i,'label':l,'value':v} for i,l,v in rows],'highlightFields':[]}
table('units','制限は四つの単位で見る','制限の単位',['制約','見る量','範囲'],[
('calls','回数','要求の回数','APIMサブスクリプション'),('output','1要求の生成上限','指定する出力の上限','モデル・Operationの入力契約'),('tpm','TPM','毎分の入力・出力トークン','対応操作／同じ利用枠'),('quota','期間内の累積量','期間中の入力・出力トークン','対応操作／同じ利用枠')], '設定が存在することと、有効になっていることを分ける',[250,330,392])
table('identity','誰の枠として数えるか','制限の単位',['使い方','カウンターの単位'],[
('primary','主キーでAPI Aを呼ぶ','検証済みAPIMサブスクリプションID'),('secondary','同じ契約の副キーでAPI Bを呼ぶ','同じAPIMサブスクリプションID'),('person','本人OAuth接続を追加する','APIMサブスクリプションの利用枠を維持')], 'キー実値・送信元IP・接続先の本人を計数キーにしない',[430,542])
table('rate-source','回数上限の設定元を選ぶ','回数制限',['設定元','教材・公開例で確認できる状態'],[
('config','config 共通設定','enabled: false／calls: 60／期間: 60秒'),('overrides','params 個別上書き','公開例のoverrides: []'),('portal','Portalプロファイル有効時','SQLのrequestRateを優先'),('off','Portal接続が無効な場合','config / paramsを選択')], 'SQLの現値・稼働中の有効値は取得していない',[390,582])
table('profile','有効な未設定と取得障害を分ける','回数制限',['状態','処理'],[
('valid','正しいプロファイルを取得','ID・schemaVersion・値を検証'),('null','requestRate: null','回数制限は未設定'),('cache','有効なプロファイル','30秒キャッシュ'),('failure','取得失敗・不正な応答','503 / LimitProfileUnavailable')], '取得できない場合に、無制限として下流へ進めない',[390,582])
views['count']={'layout':'svg','viewId':'lesson12-count','heading':'要求を数えるタイミング','svg':'diagrams/count-none.svg'}
table('rate-response','接続開始と超過時の応答','回数制限',['対象・事象','この実装での扱い'],[
('sse','SSE','inboundで1回／応答終了を待たない'),('protocol','gRPC / WebSocket','RPC開始／握手を計数'),('mcp','生成MCP','参照先RESTの実処理で計数'),('exempt','登録済みの取消・終了操作','計数免除あり／認証・所有者検査は維持'),('limit','回数枠の超過','429 / Retry-After / RATE_LIMIT_EXCEEDED')], '下流が返した429と、APIMの回数枠超過を区別する',[365,607])
table('output-field','出力上限のパラメーターは契約で選ぶ','生成上限',['Operation・契約','入力パラメーター'],[
('chat','通常のChat','max_completion_tokens'),('compatible','compatible Chat','max_tokens'),('responses','Responses','max_output_tokens')], 'モデルとOperationの契約を確認し、別名を混在させない',[410,562])
table('output-guard','1要求の出力上限を検証する','生成上限',['確認する入力','数値上限のある契約での扱い'],[
('required','所定のパラメーター未指定','400 / 指定を必須にする'),('type','文字列・不正値・別名の混在','400 / 入力を拒否'),('over','モデル別数値上限を超える値','400 / 入力を拒否'),('dynamic','dynamic等の扱い','推測の数値を補わない／契約に従う')], 'model-capabilities.json → 展開した契約 → model-limits.xml',[430,542])
table('token-profile','毎分と期間累積は利用量の制限','トークン利用量',['設定項目','制限する量','超過時'],[
('tpm','tokensPerMinute','毎分の入力・出力トークン','429'),('quota','tokenQuota.tokens / period','期間内の累積トークン','403'),('null','各項目がnull','そのトークン制限は適用なし','未設定')], '対応操作でllm-token-limitへ渡す',[355,400,217])
table('token-scope','トークン制限の適用範囲を確認する','トークン利用量',['対象・性質','この登録資材での範囲'],[
('yes','Foundry OpenAI HTTP','対応するChat / Responses操作'),('no','音声・画像・Embedding等','同じトークン制限の自動適用なし'),('stream','ストリーミング・同時要求','入力推定あり／計数の限界を考慮'),('audit','LLM利用量の数値ログ','この資材はChat操作に限定')], '回数・トークン枠・数値監査の対象は一致するとは限らない',[420,552])
views['auth-flow']={'layout':'svg','viewId':'lesson12-auth-flow','heading':'入口から下流で資格情報を切り替える','svg':'diagrams/auth-none.svg'}
table('azure','AzureサービスへはAPIMの権限で接続','下流認証',['確認する項目','役割'],[
('mi','APIMのManaged Identity','登録済みaudience向けのtokenを取得'),('audience','audience','どの接続先に向けたtokenか'),('rbac','下流のRBAC等','そのIDに実行権限があるか'),('external','登録済みの外部サービス','backend設定の専用キー・ヘッダー等')], '入口のキーの有効性と、下流の接続権限を分ける',[385,587])
table('person','本人OAuthでは二つの利用権を確認','下流認証',['確認するもの','確認する場所・意味'],[
('session','Core本人接続セッション','BrokerのSQL台帳で本人・契約・期限等を照合'),('cm','CMのmanaged認可','APIM自身の接続利用権'),('token','接続先用のOAuth token','照合後、下流の呼出しに使用'),('counter','APIMサブスクリプションID','本人接続を追加しても利用枠は維持')], '接続名やUPNを自己申告するだけでは本人証明にならない',[360,612])
table('auth-errors','認証準備と業務の成功を分ける','下流認証',['段階・状態','判断'],[
('entry','入口のキー検証が成功','下流接続・業務成功は未確定'),('prepare','API inboundで下流認証を準備','Operation固有検査の完了とは別'),('reconnect','本人接続の再認証が必要','409を下流の権限不足と分ける'),('permission','下流の権限不足','403／発生した層を確認')], '状態コードだけで拒否した処理を決めつけない',[390,582])
table('walkthrough','Chat要求を順に追う','生成上限',['順番','入力・判定・接続'],[
('input','① 入力契約','model / profile / サイズ / 出力上限'),('profile','② 有効な利用枠','プロファイル取得／キャッシュ'),('count','③ 利用制限','回数 → 対応操作のトークン量'),('auth','④ 下流認証の準備','入口資格情報を除去／下流用token'),('execute','⑤ 業務呼出し','残るOperation固有検査を通って実行')], '通常Chatの教材例／すべてのAPIが同じ並びとは限らない',[290,682])
table('diagnosis','拒否されたら何の制約かを問う','生成上限',['事象','まず照合する対象'],[
('400','出力上限の入力拒否','モデル・所定パラメーター・型・値'),('429','429が返った','回数枠／TPM／下流応答'),('503','LimitProfileUnavailable','利用枠プロファイルの取得・検証'),('403','403が返った','期間quota／下流権限／到達段階')], '詳細なログの照合と安全な設定変更は第13回で扱う',[390,582])
table('q1','確認1：キーを替えると枠は増える？','確認問題',['候補','考え方'],[
('a','A','同じ契約の主キー・副キーには別々の回数枠'),('b','B','同じ契約の主キー・副キーは同じ回数枠')], 'API AからAPI Bへ変更し、副キーで再要求した場合',[190,782])
table('q2','確認2：1要求の上限を指定すれば十分？','確認問題',['候補','考え方'],[
('a','A','出力上限を指定すれば毎分・月間の利用量も制限される'),('b','B','1要求の指定と、TPM・期間quotaは別に確認する')], '正しい出力上限を指定したChat要求を考える',[190,782])
table('q3','確認3：Graphへ入口キーを渡せばよい？','確認問題',['候補','考え方'],[
('a','A','有効な入口キーをGraphへそのまま転送する'),('b','B','入口認証と本人接続を照合し、下流用tokenを使う')], 'APIMサブスクリプションキーの検証が済んだ場合',[190,782])
fields('summary','利用制限と下流認証の確認順','まとめ',[
('unit','制限の単位','回数／1要求／毎分トークン／期間累積'),('state','有効条件と状態','設定値と有効化／nullと取得障害を区別'),('identity','計数する主体','検証済みAPIMサブスクリプションID'),('auth','下流接続','入口資格情報を除去／接続先専用の権限')], '代表要求を一つ選び、入力・利用枠・下流接続を順に追う')
# Final concise wording retains the whole teaching sequence.
edits={
11:'同じ契約で複数のAPIを使っても、共通の回数枠です。キーの更新と、利用枠を増やす操作を分けて考えます。',
15:'設定値が置かれていることと、その値が要求に適用されていることは、別に確かめる必要がありますね。',
19:'キャッシュがあるので、保存直後に全要求が新しい値を見るとは限りません。取得失敗を無制限と読めば、障害時に保護が外れてしまいます。',
21:'回数は、要求が成功したときに一回ですか。この順番の中では、どこで数えますか？',
23:'入力で拒否された要求は、全部、回数に入らないとは言い切れないんですね。検査の位置で違いますか？',
25:'ストリーミングや接続型APIだと、どこで一回とするのでしょう。データの個数を数えるのですか？',
32:'全部を同じ入力に寄せると、入力エラーになります。利用者へ示す仕様と、実行時の検査をそろえる意味が、ここにもあります。',
35:'許可した出力上限だからといって、その量を毎回生成したと数えたり、月間の総量を制限したことには、ならないんですね。',
40:'一回を小さくすることと、合計の利用を抑えることを両方考えるんですね。Portal未接続なら、既定のnullからトークン枠が有効になることもありません。',
43:'回数で接続を数えても、接続中のメディア量まで管理できたとは言えないんですね。守りたい量から、対象範囲を確認します。',
47:'Coreを利用できる権利と、モデルやツールを呼べる権利を分けるんですね。入口の秘密を余分に広げないためでもあります。',
49:'Azureサービスへは、利用者のキーではなく、APIM自身の権限を使うんですね。何を確認しますか？',
51:'tokenの取得と、接続先で必要な操作を許されていることも、別ですね。入口が通るだけで、モデルを必ず呼べるとは言えません。',
53:'Graphで本人として操作する場合、APIMの権限だけでよいのですか。本人の接続名を送れば、その人と認められますか？',
57:'認証の準備ができたら、業務の要求も成功したと考えてよいですか。それとも、まだ別の段階ですか？',
60:'本人接続の詳しい手順は後の回です。今日は入口の利用許可、本人接続の利用権、下流の権限を、それぞれ確認すると覚えておきましょう。',
64:'残るOperation固有検査も通って、業務を呼び出します。途中の判定を、要求全体の成功へ読み替えないのが、追跡の基本ですね。',
68:'はい。ログや応答の識別子、到達段階を合わせます。詳しい照合と安全な変更は次回です。今日は、どの量、どの主体、どの認証かを確かめましょう。',
72:'はい。ただし、回数制限が実際に有効かは別に確認します。カウンターの単位を説明できても、現在の上限値を推測してはいけません。',
76:'その通りです。小さい要求を大量に繰り返す場合も考え、抑えたい負荷から枠を選びます。それぞれの設定を単位と一緒に説明しましょう。',
83:'利用枠はAPIMサブスクリプションIDです。正常なnullと取得障害を分け、トークン制限の対応操作と計数の限界を確認します。',
6:'回数は要求の頻度です。軽い処理でも、短時間に大量の要求が来れば負担になります。受ける要求の回数を制限する枠です。',
10:'APIMが検証したAPIMサブスクリプションIDに結び付けます。同じ契約の主キーと副キーは同じIDなので、文字列を替えても枠は分かれません。',
16:'Portalのプロファイル接続が有効なら、SQLのrequestRateを優先します。無効ならconfigとparamsを選びます。SQLの現値は、今回取得していません。',
22:'通常のRESTでは、API共通の入力制約を通った後、inboundで一回加算します。未知のmodelやprofile、共通サイズ検査の拒否は計数前です。',
34:'送る前の入力検査です。数値上限のある契約では指定を必須にし、不正な型や値、別名の混在、モデル別上限の超過を四〇〇で拒否します。',
46:'渡しません。図の中央で、入口キー、受信Authorization、入口識別値、入力profileなどを除去します。入口の認証値を、下流の認証値へ流用しません。',
58:'まだ別です。下流認証の準備はAPI inbound内で、Operation固有検査の完了や業務呼出しの成功を意味しません。到達段階を分けます。',
62:'modelとprofile、本文サイズ、所定の出力上限などの入力契約です。通ったら利用枠のプロファイルを取得するか、検証済みキャッシュを使います。',
63:'回数を数え、対応操作のトークン枠を確認します。その後、入口資格情報を除去して、下流用認証を準備します。画面の上から順に追えます。',
82:'はい。回数、一要求の生成上限、毎分トークン、期間累積。それぞれの有効条件と守る対象を確認します。値があるだけで、制限中とは決めません。'
}
turns=[]
def add(ch,speaker,key,caption,highlight=None,delivery=None):
    caption=edits.get(len(turns)+1,caption)
    v=copy.deepcopy(views[key]) if key else {'layout':'hologram','heading':'利用制限と下流認証'}
    if highlight:
        if v['layout']=='svg':v['svg']=f'diagrams/{"count" if key=="count" else "auth"}-{highlight}.svg'
        else:v['highlightRows' if 'table' in v else 'highlightFields']=highlight if isinstance(highlight,list) else [highlight]
    speech=caption
    replacements={'TPM':'ティーピーエム','SSE':'エスエスイー','gRPC':'ジーアールピーシー','WebSocket':'ウェブソケット','MCP':'エムシーピー','OAuth':'オーオース','Managed Identity':'マネージド・アイデンティティ','audience':'オーディエンス','RBAC':'アールバック','Graph':'グラフ','Chat':'チャット','Responses':'レスポンシズ','Embedding':'エンベディング','Foundry':'ファウンドリー','UPN':'ユーザープリンシパル名','CM':'シーエム','SQL':'エスキューエル','null':'ヌル','config':'コンフィグ','params':'パラムズ','requestRate':'リクエストレート','enabled':'イネーブルド','false':'フォルス','true':'トゥルー','profile':'プロファイル','model':'モデル','Operation':'オペレーション','compatible':'コンパチブル','max_completion_tokens':'マックス・コンプリーション・トークンズ','max_output_tokens':'マックス・アウトプット・トークンズ','max_tokens':'マックス・トークンズ','tokenQuota':'トークンクオータ','tokensPerMinute':'トークンズ・パー・ミニット','LimitProfileUnavailable':'リミット・プロファイル・アンアベイラブル','Retry-After':'リトライ・アフター','RATE_LIMIT_EXCEEDED':'レート・リミット・エクシーデッド','Authorization':'オーソライゼーション','Credential Manager':'クレデンシャル・マネージャー','managed':'マネージド','schemaVersion':'スキーマ・バージョン','Core':'コア','Portal':'ポータル','backend':'バックエンド','provider':'プロバイダー','test':'テスト','production':'プロダクション','REST':'レスト','inbound':'インバウンド','quota':'クオータ','token':'トークン','Broker':'ブローカー','HTTP':'エイチティーティーピー','PII':'ピーアイアイ','OpenAI':'オープンエーアイ','ID':'アイディー','llm-token-limit':'エルエルエム・トークン・リミット','model-capabilities.json':'モデル・ケイパビリティーズ・ジェイソン','dynamic':'ダイナミック'}
    for a in sorted(replacements,key=len,reverse=True):speech=speech.replace(a,replacements[a])
    # Authored captions, split at natural Japanese punctuation into readable chunks.
    parts=re.findall(r'.+?[。？！](?:」)?|.+$',caption)
    merged=[]
    for p in reversed(parts):
        if len(p.strip())<13 and merged:merged[-1]=p+merged[-1]
        else:merged.append(p)
    parts=list(reversed(merged))
    chunks=[]
    for p in parts:
        p=p.strip()
        if not p:continue
        def units(s):return sum(.55 if ord(c)<128 else 1 for c in s)
        tokens=re.findall(r'Operation固有検査|APIMサブスクリプションID|[A-Za-z0-9_:/.-]+|.',p)
        def wrap_balanced(seq):
            text=''.join(seq)
            if units(text)<=39:return [text]
            n=max(2,int((units(text)+38)//39))
            target=units(text)/n
            choices=[]
            for i in range(1,len(seq)):
                left=''.join(seq[:i]);right=''.join(seq[i:]);u=units(left)
                if u>39:break
                if u<12 or units(right)<10 or right[0] in '、。？！':continue
                boundary=0 if left[-1] in '、はがをにでと' else 5
                choices.append((abs(u-target)+boundary,i))
            if not choices:raise ValueError('Cannot wrap '+text)
            _,i=min(choices)
            return [''.join(seq[:i])]+wrap_balanced(seq[i:])
        lines=wrap_balanced(tokens)
        if len(lines)<=2:chunks.append({'caption':p,'lines':lines,'weight':len(p)})
        else:
            for i in range(0,len(lines),2):
                ls=lines[i:i+2];chunks.append({'caption':''.join(ls),'lines':ls,'weight':sum(map(len,ls))})
    tid=f'lesson12-turn-{len(turns)+1:03d}-{speaker}'
    t={'id':tid,'speaker':speaker,'chapterId':ch,'speech':speech,'caption':caption,'subtitleLines':chunks[0]['lines'],'gapAfterSeconds':.14,'delivery':delivery or {'mako':'相手の説明を確かめる自然な質問・納得。','manabu':'目的と判断の意味を、同僚に穏やかに伝える。','ikumi':'直前の問いに答え、設定と仕組みを明瞭に説明する。'}[speaker],'visual':v}
    if len(chunks)>1:t['subtitleChunks']=chunks
    turns.append(t)
# Opening: a new episode-specific exchange.
add('opening','mako',None,'キーは有効なのに、使いすぎで止まる。さらに、接続先の認証でも止まる。これ、同じ許可を何度も確認しているんですか？')
add('opening','manabu',None,'利用できる契約か、どれだけ使えるか、接続先を呼べるか。それぞれ守りたいものが違うんです。分けると、止まった理由も見えてきます。')
add('opening','ikumi',None,'今日は一つの要求を追いながら、回数、トークン量、一回の生成上限、そして下流認証を整理しましょう。')
add('opening','mako',None,'なるほど。何を数えて、誰の枠を使い、最後は誰の権限で呼ぶのか。そこをつなげて見ていきます。')
# Purpose: units and shared identity.
add('purpose','mako','units','まず、回数制限とトークン制限は、両方とも使いすぎを止めるものですよね。何を分けて見ればいいですか？')
add('purpose','manabu','units','回数は要求の頻度です。一回の処理が軽くても、短時間に大量の要求が来れば負担になります。まず、要求を何回受けるかという枠があります。','calls')
add('purpose','ikumi','units','一方、一要求の生成上限は、その入力で指定する出力の上限です。毎分のトークン量や、期間内の累積トークン量は、複数の要求をまたいだ利用量を扱います。',['output','tpm','quota'])
add('purpose','mako','units','回数、一回の出力、毎分の量、期間の合計。単位を添えて読むと、同じ上限という言葉でも、役割が違うと分かりますね。',['calls','output','tpm','quota'])
add('purpose','mako','identity','では、誰の利用として数えますか。送信元のIPですか、それとも、送ったキーの文字列ごとですか？')
add('purpose','ikumi','identity','この実装は、APIMが検証したAPIMサブスクリプションIDに結び付けます。同じ契約の主キーと副キーは、同じIDです。キーの文字列を替えても枠は分かれません。',['primary','secondary'])
add('purpose','manabu','identity','同じ契約で複数のAPIを使っても、共通の回数枠で考えられるようにしています。キーを更新する操作と、利用枠を増やす操作を混同しないためにも大切です。',['primary','secondary'])
add('purpose','ikumi','identity','Graphなどの本人OAuth接続を追加しても、この利用枠は切り替えません。下流で誰として動くかと、Coreのどの契約で数えるかを分けます。','person')
# Rate: source, profile, counting, responses.
add('rate','mako','rate-source','設定例に、六十回、六十秒と書かれていました。今の環境は、すべての契約が一分六十回まで、と説明してよいですか？')
add('rate','ikumi','rate-source','いいえ。教材で参照するtestとproductionのconfigは、共通の回数制限がenabled、falseです。回数と期間の値だけを見て、制限中とは言えません。','config')
add('rate','manabu','rate-source','まず有効かを確認するわけですね。設定値が置かれていることと、その値が要求に適用されていることは、別に確かめる必要があります。',['config','overrides'])
add('rate','ikumi','rate-source','Portalのプロファイル接続が有効なら、SQLから取得するrequestRateを優先します。接続が無効ならconfigとparamsを選びます。SQLの現値は、今回取得していません。',['portal','off'])
add('rate','mako','profile','SQLから利用枠を読むとき、上限が設定されていない状態と、読み取りに失敗した状態は、同じ扱いですか？')
add('rate','ikumi','profile','分けます。正しいIDとschemaVersion、値を検証したプロファイルで、requestRateがnullなら、回数制限は未設定です。有効なプロファイルは三十秒キャッシュします。',['valid','null','cache'])
add('rate','manabu','profile','キャッシュがあるので、保存直後に全要求が新しい値を見るとは限りません。そして、取得失敗を上限なしと読み替えると、障害時に保護が外れてしまいます。','cache')
add('rate','ikumi','profile','取得失敗や不正な応答なら、五〇三、LimitProfileUnavailableで止めます。正常なnullと取得不能を区別し、下流を呼ぶ前に停止する設計です。','failure')
add('rate','mako','count','回数は、要求が成功したときに一回ですか。それとも、入口で受け取った瞬間ですか？この順番の中では、どこで数えますか？')
add('rate','ikumi','count','通常のRESTでは、API共通の入力制約を通った後、inboundで一回加算します。未知のmodelやprofile、共通サイズ検査の拒否は、その計数より前です。','count')
add('rate','mako','count','すると、入力で拒否された要求は、全部、回数に入らないとは言い切れないんですね。検査の位置で違ってきますか？','count')
add('rate','ikumi','count','はい。PIIや文書などのOperation固有検査は、計数後に拒否する場合があります。成功回数の台帳とは違います。処理の実行順を確認して判断します。','operation')
add('rate','mako','rate-response','長いストリーミングや接続型APIだと、どこまで待って一回とするのでしょう。受け取ったデータの個数を数えるわけですか？')
add('rate','ikumi','rate-response','SSEは応答終了を待たずに数えます。gRPCはRPC開始、WebSocketは握手です。生成MCPは、参照先RESTの実処理で計数します。フレーム数や音声量とは別です。',['sse','protocol','mcp'])
add('rate','manabu','rate-response','登録済みの取消や終了操作には、計数免除があります。使いすぎで終了操作までできなくなるのを避けつつ、認証や所有者の確認は維持します。','exempt')
add('rate','ikumi','rate-response','回数超過は四二九で、Retry-AfterとRATE_LIMIT_EXCEEDEDを返します。下流も四二九を返すことがあるので、番号だけで同じ回数枠の超過とは決めません。','limit')
# Tokens: parameter / guard / quota / scope.
add('tokens','mako','output-field','次は一要求の生成上限です。どのモデルでも、max_tokensという名前で送ればいいのでしょうか？')
add('tokens','ikumi','output-field','この公開契約では、通常のChatはmax_completion_tokens、compatibleのChatはmax_tokensを使います。似た名前でも、選択した契約に合わせます。',['chat','compatible'])
add('tokens','ikumi','output-field','Responsesはmax_output_tokensです。APIの呼び方、Operation、モデルを確認して、所定の入力名を選びます。別名を同時に並べて送ることも拒否します。','responses')
add('tokens','manabu','output-field','全部を同じ入力に寄せると、使えるはずのAPIが入力エラーになります。利用者へ示す仕様と、実行時の検査をそろえておく意味が、ここにもあります。',['chat','compatible','responses'])
add('tokens','mako','output-guard','そのパラメーターの値は、ゲートウェイが短い回答へ書き換えてくれるんですか。それとも、送る前に検査しますか？')
add('tokens','ikumi','output-guard','送る前の入力検査です。数値上限がある契約では、所定の指定を必須にします。不正な型や値、別名の混在、モデル別上限の超過を、四〇〇で拒否します。',['required','type','over'])
add('tokens','mako','output-guard','なるほど。許可した出力上限だからといって、その量を毎回生成したと数えるわけでも、月間の総量を制限したことにもならないんですね。','over')
add('tokens','ikumi','output-guard','そうです。モデル別上限の正本を使い、dynamicなどの状態へ推測の数字を足しません。自動値へ任せる契約もあるので、全モデルで同じ指定が必須とは説明しません。','dynamic')
add('tokens','mako','token-profile','では、毎分や月間の利用量は、どこで別に止めますか。入力の長さも、生成した出力も関係しますよね？')
add('tokens','ikumi','token-profile','対応操作で、プロファイルのtokensPerMinuteを標準のllm-token-limitへ渡します。毎分の入力と出力のトークン消費を制限し、超過時は四二九です。','tpm')
add('tokens','ikumi','token-profile','期間累積はtokenQuotaの量と期間を渡し、超過時は四〇三です。時間、日、週、月、年から期間を選びます。それぞれnullなら、そのトークン制限は適用しません。',['quota','null'])
add('tokens','manabu','token-profile','一回を小さくすることと、合計の利用を抑えることを両方考えるわけですね。Portal接続が未設定なら、既定のnullから、勝手にトークン枠が有効になることもありません。','null')
add('tokens','mako','token-scope','このトークン枠は、音声や画像も含めて、Coreを通るすべてのAPIで共通に効くんでしょうか？')
add('tokens','ikumi','token-scope','この登録資材が対象にするのは、Foundry OpenAIのHTTPで、対応するChatとResponsesの操作です。音声、画像、Embedding、gRPC、WebSocketなどへ自動では広がりません。',['yes','no'])
add('tokens','manabu','token-scope','回数で接続を数えられても、接続中のメディア量まで管理できたとは言えないんですね。利用したいAPIの、どの量を守るかから、対象範囲を確認します。','no')
add('tokens','ikumi','token-scope','入力は推定も使い、ストリーミングや同時要求には計数の限界があります。厳密な課金台帳とは分けます。数値ログはこの資材ではChat限定で、制限の対応範囲とも違います。',['stream','audit'])
# Auth: same diagram across question and answer.
add('auth','mako','auth-flow','利用枠まで通ったら、入口で使ったAPIMサブスクリプションキーを、接続先へそのまま渡すんですか？')
add('auth','ikumi','auth-flow','渡しません。この図の中央で、入口のキーや受信したAuthorization、入口識別値、入力profileなどを取り除きます。Coreの入口認証値を、下流の認証値として流用しません。','strip')
add('auth','manabu','auth-flow','Coreを利用できる権利と、モデルやツールを呼び出せる権利を分けるんですね。接続先へ、入口の秘密を余分に広げないためでもあります。','strip')
add('auth','ikumi','auth-flow','右側では、登録した接続方式に合う資格情報を使います。Azure向けのManaged Identity、外部サービスの専用キー、本人OAuth接続など、接続先ごとに準備します。','backend')
add('auth','mako','azure','FoundryなどのAzureサービスへ接続するなら、利用者のキーではなく、APIM自身の権限を使うんですね。何を確認しますか？')
add('auth','ikumi','azure','登録済みのaudienceに対して、APIMのManaged Identityでtokenを取得します。audienceは、どの接続先へ向けたtokenかという条件です。',['mi','audience'])
add('auth','manabu','azure','tokenを取得できることと、その接続先で必要な操作を許されていることも、同じではありません。入口が通るだけで、モデルを必ず呼べるとは言えませんね。','rbac')
add('auth','ikumi','azure','はい。下流のRBACなども合わせて確認します。登録済みの外部サービスは、backend設定に応じた専用キーやヘッダーを用意します。入口キーを代用品にしません。',['rbac','external'])
add('auth','mako','person','Graphで本人として操作する場合は、APIMの権限だけでよいのですか。本人の接続名を送れば、その人と認められますか？')
add('auth','ikumi','person','それだけでは本人証明になりません。Coreの本人接続セッションを、BrokerのSQL台帳で本人、APIMサブスクリプション、provider、期限などと照合します。','session')
add('auth','ikumi','person','その上で、Credential Managerから下流用tokenを利用します。CMのmanaged認可が見るのはAPIM自身の接続利用権です。元の利用者の接続証明は、その前にCoreで確かめます。',['cm','token'])
add('auth','manabu','person','APIMが接続を使えることと、今回の呼出元がその本人接続を使えること。両方必要なんですね。本人接続が加わっても、APIMサブスクリプションの利用枠は変えません。','counter')
add('auth','mako','auth-errors','これで認証の準備ができたら、業務の要求も成功したと考えてよいですか。それとも、まだ別の段階ですか？')
add('auth','ikumi','auth-errors','まだ別です。下流認証の準備はAPI inbound内です。Operation固有検査の完了や、実際の業務呼出しの成功を意味しません。どの段階まで進んだかを分けます。',['entry','prepare'])
add('auth','manabu','auth-errors','本人接続の再認証が必要な四〇九と、下流で権限が足りない四〇三も、対処が違いますね。何でもキーの入れ直しで解決しようとしない、ということですね。',['reconnect','permission'])
add('auth','ikumi','auth-errors','はい。本人接続の開始や更新の細かな手順は、後の回で扱います。今日は入口の利用許可、本人接続の利用権、下流の権限が別の確認だと押さえましょう。',['session'] if False else 'prepare')
# Walkthrough combines exact order and counterexample.
add('walkthrough','mako','walkthrough','ここで通常のChat要求を、一つだけ最初から追います。入口のキーは有効で、数値上限のあるモデルを選んだ場合です。次に何を見ますか？')
add('walkthrough','ikumi','walkthrough','まずmodelとprofile、本文サイズ、所定の出力上限などの入力契約です。通ったら、有効な利用枠のプロファイルを取得するか、検証済みのキャッシュを使います。',['input','profile'])
add('walkthrough','ikumi','walkthrough','続いて回数を数え、対応操作のトークン枠を確認します。その後、入口の資格情報を取り除いて、下流用の認証を準備します。この例の順番を、画面の上から追えます。',['count','auth'])
add('walkthrough','manabu','walkthrough','残るOperation固有検査も通って、業務を呼び出します。途中のどれかが通ったことを、要求全体の成功へ読み替えないのが、追跡の基本になりますね。','execute')
add('walkthrough','mako','diagnosis','同じ要求が拒否されたときは、どう問い直せばよいですか。状態コードを見て、枠を増やせばいいでしょうか？')
add('walkthrough','ikumi','diagnosis','出力上限の入力拒否なら、所定のパラメーター、型、値を見ます。四二九なら回数枠か、TPMか、下流応答かを分けます。違う制約を変えても、原因は解消しません。',['400','429'])
add('walkthrough','manabu','diagnosis','利用枠の取得不能は、使い切ったこととは違います。四〇三も、期間累積の枠と下流の権限不足では対応が変わりますね。設定変更の前に、止めた処理を探します。',['503','403'])
add('walkthrough','ikumi','diagnosis','はい。ログや応答の識別子、到達段階を合わせて確認します。詳しい照合と安全な変更は次回です。今日は、どの量、どの主体、どの認証かを言えるようにしましょう。',['429','403'])
# Three checks: stable neutral alternatives; answer highlight begins after question.
add('check','mako','q1','確認問題です。同じAPIMサブスクリプションで、API Aを主キーで使い、次はAPI Bを副キーで呼びます。回数枠は、別々のA、共通のB、どちらですか？')
add('check','manabu','q1','Bです。主キーと副キーは、同じAPIMサブスクリプションIDへ結び付きます。APIを替えても、この共通の回数枠を別枠にはできません。','b')
add('check','mako','q1','送信元のIPが変わっても、本人OAuth接続を追加しても、数える契約は同じ、という理解でよいですか？','b')
add('check','ikumi','q1','はい。この実装は検証済みの契約IDを使います。ただし、回数制限が実際に有効かは別に確認します。カウンターの単位を説明できても、現在の上限値を推測してはいけません。','b')
add('check','mako','q2','二問目です。正しい出力上限を指定したChat要求があります。これだけで、毎分や月間の利用量も制限できるA、それらは別に確認するB、どちらでしょう？')
add('check','ikumi','q2','Bです。一要求の出力指定は入力契約です。TPMと期間quotaは、対応操作に利用量の枠を適用します。出力指定を正しくしても、累積の利用枠を設定したことにはなりません。','b')
add('check','mako','q2','期間quotaだけ設定した場合も、毎分の制限が自動で付くわけではないんですね。適用する項目と対象操作を確認するんですね。','b')
add('check','manabu','q2','その通りです。小さい要求を大量に繰り返す場合も考え、どの負荷を抑えたいかで枠を選びます。回数、毎分、期間累積の設定を、それぞれの単位で説明しましょう。','b')
add('check','mako','q3','最後です。Graphを呼ぶために、入口キーの検証が済みました。そのキーを転送するA、本人接続を照合して下流用tokenを使うB、どちらですか？')
add('check','ikumi','q3','Bです。入口のAPIMサブスクリプションキーは下流へ流用しません。Coreで本人接続セッションを照合し、APIMの接続利用権も確認して、下流専用のtokenを使います。','b')
add('check','manabu','q3','入口のキーが有効でも、他の人の接続を使ってよいわけではありません。接続名やUPNを知っていることだけでも、本人として扱えません。','b')
add('check','mako','q3','利用枠は契約ID、本人の利用権は接続証明、接続先には下流用の資格情報。役割を分けて追うと、混ざらなくなりますね。','b')
# Summary and episode 13 handoff.
add('summary','mako','summary','今日の確認順を、もう一度つなげましょう。まず、制限という言葉だけでまとめず、何の量を見ているかから始めるんですね。')
add('summary','manabu','summary','はい。回数、一要求の生成上限、毎分トークン、期間累積。それぞれが有効になる条件と、守る対象を確認します。値があるだけで、制限中とは決めません。','unit')
add('summary','ikumi','summary','利用枠はAPIMサブスクリプションIDに結び付けます。正常なnullとプロファイル取得障害を分けます。トークン制限は対応操作を確認し、計数の限界も踏まえます。',['state','identity'])
add('summary','manabu','summary','下流へは、入口の秘密を取り除き、接続先専用の権限でつなぎます。本人接続が必要なら、その利用権も確認します。入口を通ることと、業務の成功は分けて考えます。','auth')
add('summary','mako','summary','次回は、拒否された要求をログから追い、安全に設定を変える方法です。何を増やすかを決める前に、どこで止まったかを確かめます。それでは、また次回。')
# Final editorial cut follows measured normal-speed audio; preserve source recordings.
base['firstCut']['omitTurnIds']=[t['id'] for t in turns if 57<=int(t['id'].split('-')[2])<=60]
base['firstCut']['editorialReason']='通常速度の初回実長16分39.667秒から約15分55秒へ整理。認証準備と業務成功・409の補足問答057〜060をまとまりごとに省略。認証準備と成功の区別は064・084で維持し、詳細な到達段階調査は第13回、本人接続手順は第19回以降へ残す。'
for t in turns:
    if t['id']=='lesson12-turn-061-mako':
        t['id']='lesson12-turn-061r2-mako'
base['turns']=turns
(HERE/'script.json').write_text(json.dumps(base,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(turns),'captionChars':sum(len(t['caption']) for t in turns),'speechChars':sum(len(t['speech']) for t in turns),'bodyViews':len(views),'chapters':len(chapters)},ensure_ascii=False))
