"""Author episode 17 using the shared production schema and fixed settings views."""
from pathlib import Path
import copy
import json
import re

P = Path(__file__).resolve().parent
s = json.loads((P / 'script.json').read_text())
ref = lambda anchor: {'file': '../03-subscription-management.html', 'anchor': anchor}
s.update(status='ready-for-generation', description='発行済みキーの操作と、利用制限の保存・反映を、Portal・SQL・APIMの分担から説明する。')
s['chapters'] = [dict(id=i, title=t, goal=g, sourceRefs=[ref(a)]) for i, t, g, a in [
    ('opening', '今回の問い', '発行後の操作を目的別に区別する', 'key-operations'),
    ('roles', '操作の入口と保存先', 'Portal・SQL・APIMの分担と現在所属の確認を説明する', 'key-operations'),
    ('state', 'キー表示・停止・再開', '秘密値の取得と利用状態の変更を区別する', 'key-operations'),
    ('transfer', '所有グループを移す', '所有の変更とキー実体の継続を説明する', 'key-operations'),
    ('review-delete', '棚卸しと削除', '確認記録と削除の受付・完了を区別する', 'key-operations'),
    ('limits', '利用制限を保存し反映する', '版管理とSQL保存、APIMの内部照会・キャッシュを説明する', 'limits'),
    ('case', 'チーム引継ぎの具体例', '停止・移管・制限変更・再開を目的と確認先へ対応付ける', 'key-operations'),
    ('check', '三つの確認問題', '移管・削除・制限反映の誤解を判断する', 'limits'),
    ('summary', 'まとめと次回の問い', '操作後の状態と確認先をまとめる', 'key-operations'),
]]
menu = ['操作の分担', '表示と停止', '所有の移管', '棚卸しと削除', '利用制限', '確認問題', 'まとめ']
views = {}
def fields(key, title, section, rows, notice):
    views[key] = dict(layout='settings', viewId='lesson17-'+key,
        areaLabel='Portalのサービスと管理', title=title, kind='発行済みAPIキー',
        section=section, menu=menu, subtitle='操作の目的・保存先・API利用への効果を対応付ける',
        footer='現行リポジトリ定義に基づく教材用設定ビュー。稼働環境の取得画面ではありません。',
        notice=notice, fields=[dict(id=i, label=l, value=v) for i,l,v in rows], highlightFields=[])
def table(key, title, section, columns, rows, notice):
    views[key] = dict(layout='settings', viewId='lesson17-'+key,
        areaLabel='Portalのサービスと管理', title=title, kind='発行済みAPIキー',
        section=section, menu=menu, subtitle='操作の目的・保存先・API利用への効果を対応付ける',
        footer='現行リポジトリ定義に基づく教材用設定ビュー。稼働環境の取得画面ではありません。',
        notice=notice, table=dict(columns=[dict(label=l,width=w) for l,w in columns], rows=[dict(id=i,cells=c) for i,c in rows]), highlightRows=[])

table('roles', '発行後の操作は、三つの役割をつなぐ', '操作の分担', [('担当',220),('保存・処理すること',752)], [
    ('portal',['Portal','本人・入力・現在所属を確認し、管理処理をつなぐ']),
    ('sql',['SQL','キー台帳の状態・所有・棚卸し日時・監査']),
    ('apim',['APIM','APIMサブスクリプションの実体・状態・キー実値']),
], 'SQLの台帳に、キー実値を保存する構成ではない')
fields('authorize', '同じキーでも、操作のたびに条件を確かめる', '操作の分担', [
    ('identity','操作する本人','検証したPortalセッションから識別'),
    ('group','所有グループ','現在所属と、SQLの所有・製品承認を照合'),
    ('state','操作できる状態','作成中・削除中・削除済みを区別'),
    ('scope','APIMの対象範囲','GETで実在・製品スコープ・状態を確認'),
], '以前見えたキーでも、現在の管理権限を確認する')
fields('reveal', 'キー表示：実値の取得と操作監査', '表示と停止', [
    ('get','APIMをGET','対象と製品スコープを確認'),
    ('secret','APIMのlistSecrets','キー実値を取得'),
    ('audit','SQLの操作監査','表示した本人と操作を記録'),
    ('store','SQLのキー台帳','キー実値は保存しない'),
], '表示は既存キーの取得。新しいキーの発行ではない')
table('state', '停止・再開：同じAPIMサブスクリプションを変更', '表示と停止', [('操作',200),('APIMの状態',290),('SQLに残すもの',482)], [
    ('suspend',['停止','suspended','台帳状態と停止の操作監査']),
    ('resume',['再開','active','台帳状態と再開の操作監査']),
    ('same',['継続するもの','同じID・同じキー','所有・会社・履歴は保持']),
], '停止は一時的な利用停止。削除やキーの再発行と区別する')
fields('concurrency', '二人の管理者が同じキーを変更したら', '表示と停止', [
    ('lock','SQLのロック','同じAPIMサブスクリプションの操作を順番に実行'),
    ('etag','APIMのETag','GETで取得した現在版を、変更時に照合'),
    ('transaction','SQLの確定','台帳状態と操作監査を同じSQL処理で保存'),
    ('unknown','途中失敗・結果不明','APIMを再照会し、SQLの状態と照合'),
], 'APIM操作とSQL保存を、一つのSQLトランザクションとは考えない')
fields('transfer', '移管：変えるのはキーの所有グループ', '所有の移管', [
    ('source','移管する人','移管元グループへの現在所属を確認'),
    ('target','移管先','有効な対象グループ・製品承認・発行枠を確認'),
    ('save','SQLの更新','groupIdと移管監査を保存'),
    ('same','APIMの実体','同じAPIMサブスクリプション・キーを継続'),
], '操作者が移管先グループのメンバーであることは必須ではない')
table('transfer-effect', '所有変更で変わること・継続すること', '所有の移管', [('対象',270),('移管の効果',702)], [
    ('owner',['SQLのgroupId','移管先グループへ変更']),
    ('access',['以後の管理操作','移管後の所有と、操作者の現在所属で判断']),
    ('apim',['APIMのID・キー','同じ実体を継続。移管だけで旧キーを失効しない']),
    ('keep',['会社・利用制限','移管操作では変更しない']),
    ('audit',['移管監査','移管元・移管先・本人・日時を保持']),
], '費用帰属の変更、利用停止、キーの失効を移管と混同しない')
fields('review', '棚卸し：確認した事実を記録する', '棚卸しと削除', [
    ('check','APIMの照会','実在・製品・操作可能な状態を確認'),
    ('review','SQLの棚卸し記録','最終確認日時・確認した本人を保存'),
    ('audit','操作監査','棚卸し操作の履歴を保存'),
    ('same','キー実体と利用状態','棚卸しだけで再発行・期限延長・再開はしない'),
], '棚卸しは状態を確認した記録。キーを作り直す操作ではない')
table('delete', '削除：受付と完了を分けて記録する', '棚卸しと削除', [('段階',245),('APIM側',320),('SQL側',407)], [
    ('start',['削除を開始','GETで対象を確認','deletingへ更新']),
    ('pending',['削除の受付中','DELETEの202など','deletingを維持']),
    ('done',['完了を確認','404などで不在を確認','deletedと削除監査']),
    ('history',['完了後の台帳','キー実体を削除','台帳と過去の履歴は保持']),
], '202 Acceptedは、削除完了を意味しない')
table('limit-values', '利用制限：キーに対応する三つの設定', '利用制限', [('設定',285),('保存する値',687)], [
    ('rate',['呼出回数','回数と更新期間の秒数']),
    ('minute',['毎分トークン数','1分あたりのトークン上限']),
    ('quota',['期間トークン数','トークン上限と、時・日・週・月・年の期間']),
    ('null',['上限を設けない項目','null。ゼロ回・ゼロトークンという値ではない']),
], '教材では設定・保存・反映を扱う。判定の詳細は第12回へ')
fields('version', '未保存の制限と、最初の保存', '利用制限', [
    ('zero','制限行が未作成','版0・各項目は上限なしとして返す'),
    ('input','画面の更新要求','読込時の版数と、新しい設定値を送信'),
    ('check','保存時の確認','現在所属・製品承認・キー状態・版数'),
    ('save','初めての保存','版0を確認し、版1の設定と監査を作成'),
], 'ここでの上限なしは、このキー別設定の範囲を指す')
fields('version-conflict', '古い画面から、別の変更を上書きしない', '利用制限', [
    ('read','管理者A・Bが読込','どちらも版1を取得'),
    ('a','Aが先に保存','expectedVersion=1 → 版2を保存'),
    ('b','Bが古い画面で保存','expectedVersion=1 → 版不一致で競合'),
    ('next','競合したとき','最新設定を読み、差分を確認して判断'),
    ('audit','SQLの保存単位','設定と変更前後・版数・操作者の監査を同時保存'),
], '架空例。版数は変更競合を検出するための値')
fields('runtime-profile', 'SQL保存後、API利用時に設定を読む', '利用制限', [
    ('id','照会するID','実APIMサブスクリプションID'),
    ('auth','内部profile API','専用鍵でAPIMからの照会を確認'),
    ('cache','APIMでの再利用','取得結果を30秒キャッシュ'),
    ('apply','利用時の適用','APIMが取得した回数・トークン制限を使う'),
    ('fail','照会障害','503で止める。上限なしとして通さない'),
], '制限変更のたびに、APIMのキーやポリシーXMLを更新しない')
fields('example-limits', '架空例：分析チームの引継ぎで設定を見直す', '利用制限', [
    ('rate','呼出回数','60回 / 60秒'),
    ('minute','毎分トークン数','30,000トークン'),
    ('quota','期間トークン数','1,000,000トークン / Daily'),
    ('version','保存と反映','版一致でSQL保存 → APIMの30秒キャッシュを考慮'),
    ('usage','利用枠の扱い','主副キーで共有。設定変更だけでは使用量をリセットしない'),
], '説明用の設定例。稼働環境の値や推奨値ではない')
table('case', '架空例：分析チームから運用チームへ引き継ぐ', '所有の移管', [('目的',270),('選ぶ操作と確認先',702)], [
    ('pause',['切替中の利用を止める','停止：APIMの状態とSQL台帳・監査']),
    ('owner',['管理責任を引き継ぐ','移管：所有グループと移管監査']),
    ('limit',['引継ぎ後の利用枠を変える','制限：SQLへ版管理して保存']),
    ('resume',['利用を戻す','再開：同じAPIMサブスクリプションをactiveへ']),
    ('review',['確認した事実を残す','棚卸し：APIMを確認し日時・本人を保存']),
], '教材上の組合せ例。すべての移管で停止が必須という意味ではない')
fields('check-transfer', '確認1：所有グループを移管したら？', '確認問題', [
    ('a','選択肢A','新しいキーが発行され、古いキーは失効する'),
    ('b','選択肢B','所有が変わり、同じAPIMのIDとキーを継続する'),
    ('c','選択肢C','会社・利用制限も自動的に変更される'),
], '移管によって変更するデータを考える')
fields('check-delete', '確認2：削除要求に202が返ったら？', '確認問題', [
    ('a','選択肢A','受付だけでdeletedと断定する'),
    ('b','選択肢B','deletingを保ち、再照会で完了を確認する'),
    ('c','選択肢C','台帳と監査を直ちにすべて消す'),
], '受付・完了・履歴の保存を分けて考える')
fields('check-limits', '確認3：新しい制限を保存した直後は？', '確認問題', [
    ('a','選択肢A','すべての要求で即時に新設定へ切り替わる'),
    ('b','選択肢B','SQL保存と、APIMで取得・適用する時点を分ける'),
    ('c','選択肢C','profile照会の失敗時は上限なしで通す'),
], '保存先・30秒キャッシュ・照会障害を手掛かりにする')
table('summary', '操作の名前を、変更する状態へ結び付ける', 'まとめ', [('操作',240),('変わるもの・残るもの',732)], [
    ('state',['表示・停止・再開','実値取得と監査／APIMと台帳の状態']),
    ('owner',['移管','所有グループと監査。APIMのID・キーは継続']),
    ('review',['棚卸し','確認日時・本人。キー再発行ではない']),
    ('delete',['削除','受付中と完了を区別。台帳と履歴は保持']),
    ('limit',['利用制限','SQLの版と監査／APIMが取得して適用']),
], '次回：組織・SQL台帳・APIM実体の状態が一致しないときの確認')
for key in ['management-flow','limits-flow']:
    views[key] = dict(layout='svg', viewId='lesson17-'+key, svg='diagrams/'+key+'.svg')

replacements = [('APIMサブスクリプションID','エーピーアイエム サブスクリプション アイディー'),('APIM','エーピーアイエム'),('API','エーピーアイ'),('Portal','ポータル'),('SQL','エスキューエル'),('Graph','グラフ'),('Entra','エントラ'),('ETag','イータグ'),('GET','ゲット'),('PATCH','パッチ'),('DELETE','デリート'),('listSecrets','リストシークレッツ'),('groupId','グループ アイディー'),('expectedVersion','エクスペクテッド バージョン'),('profile','プロファイル'),('XML','エックスエムエル'),('ID','アイディー'),('suspended','サスペンデッド'),('active','アクティブ'),('deleting','デリーティング'),('deleted','デリーテッド'),('Accepted','アクセプテッド'),('null','ヌル'),('Daily','デイリー'),('Responses','レスポンシズ'),('Chat','チャット'),('60回','六十回'),('60秒','六十秒'),('30,000','三万'),('1,000,000','百万'),('503','五百三'),('404','四百四'),('202','二百二')]
replacements.sort(key=lambda item:-len(item[0]))
def chunks(text):
    # Preserve complete Japanese clauses; every authored clause fits at 32px.
    clauses = re.findall(r'.*?[、。？！?]|.+$', text)
    lines=[]; line=''
    for clause in clauses:
        assert len(clause)<=38, clause
        if line and len(line)+len(clause)>38:
            lines.append(line); line=''
        line+=clause
        if clause[-1] in '。？！?':
            lines.append(line); line=''
    if line: lines.append(line)
    return [dict(caption=''.join(lines[i:i+2]),lines=lines[i:i+2],weight=len(''.join(lines[i:i+2]))) for i in range(0,len(lines),2)]
turns=[]
def t(who,chapter,caption,view=None,hi=(),delivery=None):
    v=copy.deepcopy(views[view]) if view else dict(layout='hologram',heading='発行後のキー管理')
    if view and v['layout']=='settings': v['highlightRows' if 'table' in v else 'highlightFields']=list(hi)
    speech=caption
    for a,b in replacements: speech=speech.replace(a,b)
    cc=chunks(caption)
    turns.append(dict(id=f'lesson17-turn-{len(turns)+1:03}-{who}',speaker=who,chapterId=chapter,
        speech=speech,caption=caption,subtitleLines=cc[0]['lines'],subtitleChunks=cc,gapAfterSeconds=.14,
        delivery=delivery or {'mako':'直前の説明を受け、視聴者の疑問を自然に確かめる。','manabu':'目的や運用上の判断を、相手への短い返答として落ち着いて話す。','ikumi':'問いの焦点へ答え、設定と保存先を明瞭に説明する。'}[who],visual=v))

# Topic-specific opening, with all three selected speakers.
t('mako','opening','発行したAPIキーを、別のチームへ引き継ぎたいんです。所有グループを変えたら、今までのキーも使えなくなるんでしょうか。')
t('manabu','opening','引継ぎと利用停止は、目的が違いますね。誰が管理するか、使える状態か、どれだけ使えるか。この三つを分けて考えましょう。')
t('ikumi','opening','今回は発行後の管理です。表示、停止と再開、移管、棚卸し、削除。そして利用制限が、SQLへ保存されてAPI利用に反映されるまでを追います。')

t('mako','roles','まず、画面で一つのキーを選んだ後、裏側では誰が何をするのでしょう。Portalがキーそのものを保存して、操作しているんですか。','roles')
t('ikumi','roles','Portalは操作の入口です。本人や入力を確認して、SQLとAPIMの処理をつなぎます。SQLには状態や所有グループなどの台帳と、操作した人をたどる監査を残します。','roles',['portal','sql'])
t('manabu','roles','一方、APIを利用するための実体は、APIMサブスクリプションです。台帳に登録があることと、実際にAPIを使える状態であることは、分けて確認します。','roles',['apim'])
t('mako','roles','では、この管理の関係図では、PortalからSQLとAPIMへ、それぞれ別の処理を依頼するんですね。ブラウザーがSQLを直接書き換える図ではない。','management-flow')
t('ikumi','roles','はい。キー実値を持つのはAPIMです。SQLは、どのキーをどの組織が管理し、誰が何を変えたかを持ちます。画面上では一つの操作でも、保存先の役割は別です。','management-flow')
t('mako','roles','昨日そのキーを表示できた人なら、今日も同じ操作ができる、と考えてよいですか。発行時の所属だけ見ておけば足りますか。','authorize')
t('ikumi','roles','操作のたびに現在の条件を確かめます。本人を識別し、Graphで現在所属を確認して、SQLの所有グループや製品承認、キー状態と照合します。過去の成功だけでは判断しません。','authorize',['identity','group','state'])

t('mako','state','最初の操作はキー表示です。SQLの台帳を開けば、その行からキーの文字列を読めるのでしょうか。','reveal')
t('ikumi','state','実値はAPIMから取得します。まずGETで対象と製品スコープを確認し、その後listSecretsで秘密値を取得します。SQLには表示の操作監査を保存し、キー実値は保存しません。','reveal',['get','secret','audit','store'])
t('manabu','state','画面のキー表示は最大六十秒で閉じ、タブを離れた場合などにも破棄します。ただし、コピー済みのキーが失効する仕組みではありません。','reveal',['audit','store'])
t('mako','state','しばらく使わないときは停止ですね。削除して、必要になったら発行し直す方法と、何が違いますか。','state')
t('ikumi','state','停止は、同じAPIMサブスクリプションの状態をsuspendedへ変更します。SQLでも台帳状態と停止の監査を保存します。キーの実体や所有の記録は保持します。','state',['suspend','same'])
t('ikumi','state','再開では、同じAPIMサブスクリプションをactiveへ戻します。GETで現在の対象と状態を確認し、必要ならPATCHします。SQLの状態と監査も対応させます。','state',['resume','same'])
t('mako','state','二人が同時に停止と再開を押した場合はどうでしょう。Portalの同時操作や、外からのAPIM変更とぶつかったら、どう扱いますか。','concurrency')
t('ikumi','state','同じAPIMサブスクリプションへの操作は、SQLロックで順に進めます。さらにAPIMでは、取得したETagを変更時に照合し、別の変更が入った競合を検出します。','concurrency',['lock','etag'])
t('mako','state','でも、APIMの変更とSQL保存は、まったく別のサービスですよね。途中で応答が途切れたら、一度も変更されていないと決めて再実行してよいですか。','concurrency')
t('ikumi','state','結果不明を失敗と決め付けません。APIMを再照会して、実体の状態とSQL台帳を照合します。二つのサービスを一つのSQLトランザクションで巻き戻せる、とは考えないでください。','concurrency',['unknown','transaction'])

t('mako','transfer','ここから、冒頭の引継ぎです。分析チームから運用チームへ渡すとき、担当者は両方のグループに所属していないといけませんか。','transfer')
t('ikumi','transfer','移管する人には、移管元への現在所属が必要です。一方、移管先のメンバーであることは必須ではありません。移管先の有効性や製品承認、発行枠などを確認して進めます。','transfer',['source','target'])
t('mako','transfer','実際に保存するのは、SQL台帳の所有グループと移管監査。では、APIM側では新しいサブスクリプションを作るのでしょうか。','transfer')
t('ikumi','transfer','作り直しません。同じAPIMサブスクリプションとキーを継続します。SQLのgroupIdを変更し、移管元と移管先、移管した本人、日時を履歴へ保存します。','transfer',['save','same'])
t('mako','transfer','同じキーが続くなら、移管元にいた人が覚えているキーも、移管だけで失効するわけではない。管理権限とAPI利用の資格情報は別ですね。','transfer-effect')
t('ikumi','transfer','はい。以後のPortal管理は、新しい所有グループと現在所属で判断します。一方、移管はキー文字列を変更しません。古いキーの失効を完了した操作として説明してはいけません。','transfer-effect',['access','apim'])
t('manabu','transfer','会社への費用帰属や、利用制限も、移管操作で自動変更される対象ではありません。引継ぎの目的に応じて、別に見直す設定を洗い出します。','transfer-effect',['keep','audit'])

t('mako','review-delete','次は棚卸しです。確認したボタンを押すと、キーの有効期限を延ばしたり、新しいキーを発行したりするのでしょうか。','review')
t('ikumi','review-delete','棚卸しは確認記録です。APIMの実在や製品、状態を確かめ、SQLへ確認日時と本人、監査を保存します。再発行や期限延長ではなく、停止中のキーも勝手に再開しません。','review',['check','review','audit'])
t('mako','review-delete','もう使わないキーは削除します。この表ではdeletingとdeletedに分かれています。削除要求を受け付けたら、すぐ完了ではないんですか。','delete')
t('ikumi','review-delete','APIMの対象を確認し、SQLをdeletingにしてDELETEを要求します。二百二のAcceptedは受付中です。未完了と判断した間はdeletingを維持します。','delete',['start','pending'])
t('mako','review-delete','二百二という成功系の応答でも、キー実体がもう存在しないとは限らない。完了は、何で判断しますか。','delete')
t('ikumi','review-delete','再照会でAPIMの不在などを確認してから、SQLをdeletedへ進めて削除監査を保存します。結果が不明な場合も、受付と完了を飛び越えて同じ状態にしません。','delete',['done'])
t('manabu','review-delete','削除完了後も、台帳と過去の履歴は保持します。実体を消すことと、誰が管理して何をしたかという記録を消すことは、別の話です。','delete',['history'])

t('mako','limits','次は利用制限です。発行済みキーごとに、どれだけ利用できるかを変更したいとき、画面はどの値を保存するのでしょう。','limit-values')
t('ikumi','limits','呼出回数は、回数と更新期間の秒数を一組で持ちます。それに毎分トークン数と、期間トークン数を設定できます。期間には時、日、週、月、年の区分があります。','limit-values',['rate','minute','quota'])
t('mako','limits','使わない制限項目は、ゼロを入れるんですか。上限なしと、利用できる量がゼロとは、意味が違いそうです。','limit-values')
t('ikumi','limits','上限を設けない項目はnullで表します。ゼロ回やゼロトークンを上限値にする指定とは違います。また、このキー別の設定で上限なしでも、別の制御まで消えるとは限りません。','limit-values',['null'])
t('mako','limits','制限の行をまだ一度も作っていないキーは、画面を開いた時点で何が返りますか。取得に失敗した状態とは区別したいです。','version')
t('ikumi','limits','未作成なら、版ゼロで各項目が上限なしの設定を返します。画面は読み込んだ版数と新しい設定を送信し、最初の保存では版ゼロを確認して、版一を作ります。','version',['zero','input','save'])
t('ikumi','limits','版数は、古い画面から他の変更を上書きしないための値です。現在所属や承認、キー状態も確認し、設定と変更監査を同じSQL処理で保存します。','version',['check','input'])
t('mako','limits','具体例で確認します。AさんとBさんが同じ版一を読み、Aさんが先に保存しました。Bさんがその後、古い画面のまま保存するとどうなりますか。','version-conflict')
t('ikumi','limits','Aさんの保存で版二になっています。Bさんが送る期待版一とは一致しないので、競合として扱います。Bさんの古い値で版二をそのまま上書きする処理にはしません。','version-conflict',['a','b'])
t('manabu','limits','競合したら最新の設定を読み、先に何が変わったか確認して判断します。保存の成功だけを急ぐより、別の人の意図を保って変更を重ねるための仕組みです。','version-conflict',['next','audit'])
t('mako','limits','SQLに保存できました。すると、その瞬間にAPIMのサブスクリプションや、ポリシーXMLも書き換えるんでしょうか。','runtime-profile')
t('ikumi','limits','書き換えません。profile連携が有効な場合、APIMが実APIMサブスクリプションIDと専用鍵で、Portalの内部APIを照会します。保存しただけで連携設定まで有効化する操作ではありません。','runtime-profile',['id','auth'])
t('mako','limits','この反映の図では、管理者が保存する流れと、API利用時に読み出す流れが別に描かれています。利用するたびに、管理画面を通るわけではないんですね。','limits-flow')
t('ikumi','limits','はい。管理画面は設定と監査をSQLへ保存します。利用時の内部照会は、SQLの設定をAPIM向けに返します。Portal画面の台帳IDと、APIMへ返す実サブスクリプションIDも区別します。','limits-flow')
t('mako','limits','そこで三十秒のキャッシュですね。保存直後の試用で古い上限が使われても、すぐ保存失敗と決めてはいけないのでしょうか。','runtime-profile')
t('ikumi','limits','APIMは取得した設定を三十秒再利用します。保存直後は既存キャッシュの値が使われる場合があります。SQLで保存した版と値を確認し、反映の時間差も踏まえて利用結果を確認します。','runtime-profile',['cache','apply'])
t('mako','limits','もし内部profile APIが応答しなければ、業務を継続するために上限なしとして通しますか。それとも、未設定の版ゼロとは別ですか。','runtime-profile')
t('ikumi','limits','照会障害は五百三で止めます。取得不能を未設定と読み替え、無制限に通す構成ではありません。三十秒キャッシュと、照会が必要な場面の失敗を分けて確認します。','runtime-profile',['fail'])
t('manabu','limits','問い合わせでは、画面で何を保存したか、SQLの版と監査、APIMが何を取得したかを結び付けます。利用できない理由を、上限超過だけに決め付けないことも大切です。','runtime-profile',['auth','cache','fail'])
t('mako','limits','設定の架空例です。六十秒に六十回、毎分三万トークン、一日に百万トークン。この三つは、まとめて一つの上限という意味ではありませんね。','example-limits')
t('ikumi','limits','別々の設定です。トークン制限は対応するChatやResponsesの操作に適用します。主キーと副キーは同じ枠を共有し、設定変更だけで使用量がリセットされるわけではありません。','example-limits',['minute','quota','usage'])

t('mako','case','一つの引継ぎに当てはめます。分析チームのキーを運用チームへ渡し、切替中はいったん利用を止め、引継ぎ後の上限も変えたい場合です。','case')
t('manabu','case','まず目的を分けます。切替中の利用停止、管理責任の移管、利用枠の見直しです。この例で停止を選ぶのは運用上の判断で、すべての移管に停止が必須という意味ではありません。','case',['pause','owner','limit'])
t('mako','case','停止でAPIMと台帳の状態をそろえた後、元のチームの現在メンバーが、条件を満たす運用チームへ移管します。同じキーは継続するんですね。','case')
t('ikumi','case','はい。所有グループと移管監査を確認します。移管後に制限を変更する人は、新しい所有グループへの現在所属などを満たす必要があります。古い所有のままで判断しません。','case',['owner','limit'])
t('manabu','case','新しい担当者が上限を読み、版数を伴って保存します。保存後はSQLの設定と監査を確認し、再開する時点ではAPIMのキャッシュによる時間差も考慮します。','case',['limit','resume'])
t('mako','case','再開して利用を確認し、棚卸しで確認日時と本人を残す。引継ぎが終わったからと、同じ実体を削除するのは、この目的には合いませんね。','case')
t('ikumi','case','その通りです。状態、所有、制限、確認記録は別々に見ます。途中の結果が分からない操作はAPIMを再照会し、SQL台帳と監査を照合してから次の判断へ進みます。','case',['pause','owner','limit','review'])

t('mako','check','確認問題です。所有グループを移管しただけで、以前のキーは失効しますか。画面の三つの選択肢から、変わるものを選んでください。','check-transfer',delivery='確認問題として落ち着いて問い、答えを先取りせず短い間を取る。')
t('manabu','check','答えはBです。所有は移管先へ変わりますが、同じAPIMサブスクリプションとキーを継続します。移管だけで、旧キーの失効や会社の変更を済ませたとは扱いません。','check-transfer',['b'])
t('ikumi','check','管理権限は移管後の所有と現在所属で確認し、履歴は移管監査で追います。API利用の資格情報が同じことと、Portalで誰が管理できるかを区別してください。','check-transfer',['b'])
t('mako','check','二問目です。削除要求に二百二が返りました。A、即座に完了と断定。B、削除中を保ち再照会で確認。C、台帳と監査も消す。どれでしょう。','check-delete',delivery='選択肢の境目を明瞭にし、質問中は正答を示さない。')
t('ikumi','check','答えはBです。受付と完了は別です。deletingのまま必要な確認を続け、APIMの不在などで完了を確かめてからdeletedへ進めます。履歴は保持します。','check-delete',['b'])
t('manabu','check','成功系の応答だから、目的の状態まで到達したはず、と短絡しないことですね。利用停止を選んだのか、削除を選んだのかも、目的と合わせて確認しましょう。','check-delete',['b'])
t('mako','check','最後です。制限を保存した直後、以前の上限が使われました。保存失敗と即断してよいでしょうか。照会障害では、上限なしにしてよいでしょうか。画面の選択肢で考えてください。','check-limits')
t('ikumi','check','Bのように、SQLへの保存とAPIMの取得・適用を分けます。三十秒のキャッシュを考慮し、保存した版と値を確認します。照会障害は五百三で止め、上限なしへ置き換えません。','check-limits',['b'])
t('manabu','check','未設定の版ゼロと、設定を取得できない障害も別です。どこまで成功したかを保存先と利用側へ分けると、調べる順番が明確になります。','check-limits',['b'])

t('mako','summary','冒頭の問いへ戻ります。チームへ引き継ぐ操作と、利用を止める操作、利用量を変える設定は、それぞれ別の状態を変えているんですね。','summary')
t('manabu','summary','表示は実値取得と監査、停止と再開は利用状態、移管は所有、棚卸しは確認記録です。削除は受付から完了までを追い、実体がなくなっても台帳と履歴は残します。','summary',['state','owner','review','delete'])
t('ikumi','summary','利用制限は版を比較してSQLへ設定と監査を保存し、APIMが内部照会して適用します。現在所属、対象範囲、競合、キャッシュを、それぞれの確認先へ結び付けてください。','summary',['limit'])
t('mako','summary','次回は、組織、SQL台帳、APIMの実体がそろわないときの確認です。不一致と取得不能を区別し、どこを確かめて状態を整えるかへ進みましょう。','summary')

s['turns']=turns
s['sourceReferences']=[ref('key-operations'),ref('limits')]+[
    {'file':'../../../apps/portal/'+f} for f in [
        'lib/api-keys/service.ts','lib/api-keys/repository.ts','lib/api-keys/apim.ts',
        'lib/api-keys/limit-contracts.ts','lib/api-keys/limit-service.ts',
        'lib/api-keys/limit-repository.ts','lib/api-keys/gateway-profile.ts',
        'components/api-keys/limit-profile.tsx']]
(P/'script.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(turns=len(turns),captionChars=sum(len(t['caption']) for t in turns),speechChars=sum(len(t['speech']) for t in turns),openingChars=sum(len(t['speech']) for t in turns if t['chapterId']=='opening'),views=len(views)),ensure_ascii=False))
