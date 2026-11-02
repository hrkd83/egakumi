"""Episode 05 visual revision. Keeps all speech, captions and delivery intact."""
from copy import deepcopy
import json

STYLE = 'teaching-settings-v2'
MENU = ['関連付け・経路', 'ルールの種類', '評価順・優先度', '内部中継', '基盤依存通信', 'DNS・共通設定', '確認問題', '保守・まとめ']
VIEWS = {}


def base(key, title, section, heading=None, notice='設定値と教材注釈を対応付ける'):
    return dict(viewId=key, areaLabel='Firewallポリシー', title=title,
                kind='設定・公開定義の確認', section=section, menu=MENU,
                heading=heading or title, subtitle='資料・公開設定例に基づく教材。稼働環境の取得画面ではありません。',
                notice=notice, footer='01 ネットワークの制御 / 26–30・34–35節 / 公開定義と教材注釈')


def fields(key, title, section, rows, **kw):
    v=base(key,title,section,**kw)
    v.update(layout='settings',fields=[dict(id=i,label=l,value=s) for i,l,s in rows])
    VIEWS[key]=v


def table(key,title,section,headers,widths,rows,**kw):
    v=base(key,title,section,**kw)
    v.update(layout='settings',table={'columns':[dict(label=l,width=w) for l,w in zip(headers,widths)],
               'rows':[dict(id=i,cells=c) for i,c in rows]})
    VIEWS[key]=v


def composite(key,title,section,rows,diagram,*,note='',**kw):
    v=base(key,title,section,**kw)
    v.update(layout='composite',rows=[dict(id=i,label=l,value=s) for i,l,s in rows],
             diagram=diagram,note=note)
    VIEWS[key]=v


def vertical(a,b,c,*,details=('', '', ''),labels=('',''),caption='設定と経路を対応付ける',bidirectional=False):
    return dict(kind='vertical',nodes=[dict(id=i,label=l,detail=d) for i,l,d in zip(['source','firewall','target'],[a,b,c],details)],
                labels=labels,caption=caption,bidirectional=bidirectional)


composite('fw-association-path','関連付けを確認してから、通信の経路を追う','関連付け・経路',[
 ('firewall','Firewall','afw-<stem>'),('policy','関連Policy','afwp-<stem>'),
 ('relay','中継経路','AppGW ↔ Firewall ↔ APIM / ACA'),
 ('pe','PE経路（注釈）','標準UDRでは、PE宛ての具体的な直接経路'),
 ('basis','資料の範囲','公開定義・設定例。実効経路の取得値ではない')],
 dict(kind='routes',caption='代表的な2経路',upper=['AppGW','Firewall','APIM'],lower=['ACA','SQL PE']),
 note='注釈：許可の定義があることと、その通信がFirewallを通ることを区別する。')

table('rule-inputs','NetworkとApplicationの照合条件','ルールの種類',
 ['照合する情報','Networkルール','Applicationルール'],[230,371,371],[
 ('source',['送信元','IP範囲','IP範囲']),
 ('destination',['宛先','IP / サービス タグ等','WebのFQDN']),
 ('protocol',['プロトコル・ポート','TCP / UDP等 + 宛先ポート','HTTP / HTTPS + ポート']),
 ('host',['名前の扱い','FQDNをDNSでIPへ解決する場合もある','HTTP Host / HTTPS SNI'])],
 heading='NetworkとApplicationの違い',notice='教材注釈：FQDNがあるだけでApplicationルールとは判断しない。')

fields('application-tls','標準Applicationルールと検査の分担','ルールの種類',[
 ('tls','terminateTLS','false'),('sni','HTTPSの照合対象','SNIのホスト名。URLパス・本文は復号しない'),
 ('host','HTTPの照合対象','Host'),('waf','Web攻撃検査（注釈）','公開入口のWAF'),
 ('auth','API認証・認可（注釈）','APIMやアプリの設定も確認')],
 notice='教材注釈：宛先の許可、Web攻撃の検査、API認可の役割を分ける。')

composite('ntp-fqdn','FQDNを使う管理VMのNTP許可','ルールの種類',[
 ('rule','ルール名','management-time'),('source','送信元','管理VM区画'),
 ('fqdn','宛先FQDN','ntp.ubuntu.com'),('port','通信・ポート','UDP / 123'),
 ('method','種別・照合','Network / DNSで解決した宛先IPを照合')],
 vertical('管理VM','DNSで解決したIP','IP + UDP 123',details=('management-time','ntp.ubuntu.com','Networkルールで照合'),labels=('名前を解決','通信条件で判定')),
 note='注釈：WebのHost / SNIを見分けるApplicationルールとは照合方法が異なる。')

table('rule-processing','独自ルールの種類と評価順','評価順・優先度',
 ['段階（教材注釈）','一致した場合','一致しない場合'],[240,366,366],[
 ('network',['01 Network','Allowで判定終了','対応するWeb通信は次の照合へ']),
 ('application',['02 Application','Allowで判定終了','Azure組み込み基盤用許可も評価']),
 ('builtin',['03 組み込み許可','基盤用の許可を区別する','最後まで許可がなければ拒否']),
 ('default',['最終の既定拒否','独自deny-all規則とは別','独自一覧以外を一律拒否とはしない'])],
 heading='種類の順序と優先度',notice='教材注釈：Network Allow一致後にApplicationのFQDN許可を重ねない。')

fields('hierarchy','グループ・コレクション・ルールの階層','評価順・優先度',[
 ('group','グループ','同じ種類の中で、グループの小さいpriorityから'),
 ('collection','コレクション','次にコレクションの小さいpriorityから'),
 ('action','Actionの設定先','コレクションにAllow / Denyを設定'),
 ('individual','個々のルール（注釈）','NSGのような個別のpriorityを付ける構造ではない')],
 notice='教材注釈：種類の順序 → グループ → コレクション、の順で読む。')

table('priority-comparison','種類の順序が優先度の数字より先になる','評価順・優先度',
 ['比較する対象','グループ優先度・所属','読み方（教材注釈）'],[210,392,370],[
 ('network',['仮例：Network','400','Applicationより先に評価']),
 ('application',['仮例：Application','350','Network不一致のWeb通信を評価']),
 ('broker',['実定義①','rcg-credential-broker / 360','同値だけで互いの前後を決めない']),
 ('gateway',['実定義②','rcg-portal-gateway / 360','同値だけで互いの前後を決めない'])],
 notice='400 / 350は原理を示す仮例。360の2行はリポジトリの実定義。')

fields('allow-default','独自Allowと最終的な既定拒否','評価順・優先度',[
 ('allow','独自コレクション','すべてAllow'),('deny','独自Deny / DNAT','定義なし'),
 ('broad','Internet全ポート許可','独自定義なし'),('builtin','組み込み許可（注釈）','Azureの基盤用許可も評価する'),
 ('default','最終判定（注釈）','最後まで許可に一致しなければ既定拒否')],
 notice='拒否の説明では、独自Deny名だけでなくAllow不一致も確認する。')

composite('agw-apim','AppGW → APIMの内部中継を許可する','内部中継',[
 ('rule','ルール名','agw-to-apim-http'),('source','送信元','AppGW区画'),
 ('destination','宛先','APIMの実内部VIP（数値は取得していない）'),
 ('protocol','種別 / Action','Network / Allow / TCP'),('ports','宛先ポート','80 / gRPC有効時だけ内部443も追加')],
 vertical('AppGW区画','Azure Firewall','APIM実内部VIP',details=('内部中継の送信元','agw-to-apim-http','TCP 80 / gRPC時443も'),labels=('往路UDR','Network / Allow')),
 note='注釈：名前のhttpだけでポート範囲を判断しない。FQDN許可を重ねない。')

composite('agw-aca','Portal入口の許可先とUDRの宛先を比べる','内部中継',[
 ('rule','ルール名','agw-aca-https'),('source','送信元','AppGW区画'),
 ('destination','FWの許可宛先','ACA区画のCIDR'),('ports','通信・ポート','TCP / 443'),
 ('route','AppGWのUDR','ACA入口の実IP /32 → Firewall')],
 vertical('AppGW区画','Azure Firewall','ACA入口',details=('Portalの要求','agw-aca-https','許可宛先はACA CIDR'),labels=('入口IP /32のUDR','TCP 443')),
 note='注釈：UDRは次の行き先、Firewallルールは通してよい範囲を決める。')

composite('stateful-return','許可済み接続への応答と復路UDR','内部中継',[
 ('state','動作（注釈）','Firewallはステートフルに応答を扱う'),
 ('response','応答（注釈）','許可済み接続への応答は状態追跡の対象'),
 ('reverse','逆向きのAllow','応答だけのための新規接続Allowは不要'),
 ('route','往路・復路のUDR','同じFirewallへ経路をそろえる'),
 ('new','新規接続','逆向きの別の新規接続は別判定')],
 vertical('AppGW区画','Azure Firewall','APIM',details=('要求と応答','接続の状態を追跡','同じ接続への応答'),labels=('往路・復路UDR','往路・復路UDR'),bidirectional=True),
 note='注釈：状態を追跡することは、戻る経路を自動で作ることではない。')

table('direct-internal','内部ルールの定義と標準の直接経路','内部中継',
 ['ルール名','送信元','宛先・ポート','標準経路（注釈）'],[272,160,310,230],[
 ('broker',['apim-broker','APIM区画','ACA実内部IP / TCP 443','VNet内の具体経路']),
 ('sql',['broker-sql','ACA区画','SQL PE実IP / TCP 1433','PEへの具体的な直接経路']),
 ('vault',['broker-vault','ACA区画','Key Vault PE実IP / TCP 443','PEへの具体的な直接経路'])],
 notice='Network / Allowの定義があっても標準経路ではFWを通らない。apim-brokerは生成条件付き。')

composite('apim-storage','APIMの基盤依存通信：Storage','基盤依存通信',[
 ('rule','ルール名','apim-storage'),('source','送信元','APIM区画'),
 ('destination','宛先','Storageサービス タグのIP集合'),('ports','通信・ポート','TCP / 443'),
 ('action','種別 / Action','Network / Allow')],
 vertical('APIM区画','Network / Allow','StorageタグのIP',details=('基盤の依存通信','apim-storage','自社アカウント1個の指定ではない'),labels=('Firewallへ到達','一致で判定終了')),
 note='Application側の例：apim-approved-services。Network一致後にFQDN許可を重ねない。')

table('apim-platform','基盤SQLと業務SQLを、送信元・目的で分ける','基盤依存通信',
 ['代表の接続','送信元','宛先・ポート','目的（注釈）'],[250,160,332,230],[
 ('apim-sql',['apim-sql','APIM区画','Sqlタグ / TCP 1433','APIM基盤の依存通信']),
 ('business-sql',['業務SQLの接続','ACA区画','SQL PE実IP / TCP 1433','Portal・Brokerの業務SQL']),
 ('vault',['apim-key-vault','APIM区画','AzureKeyVault / TCP 443','基盤依存通信']),
 ('entra',['apim-entra','APIM区画','Entraのタグ / TCP 443','認証']),
 ('monitor',['監視・監査の代表例','APIM区画','AzureMonitor / EventHub等','監視・監査データの送信'])],
 notice='全ポートの暗記より、送信元と依存先の役割を結び付ける。')

composite('ntp-sources','APIMと管理VMのNTP許可は独立して読む','基盤依存通信',[
 ('collection','コレクション','allow-vm-platform'),
 ('apim','APIMのルール','apim-platform-time'),('apim-dest','APIMの条件','time.windows.com / UDP 123'),
 ('vm','管理VMのルール','management-time'),('vm-dest','管理VMの条件','ntp.ubuntu.com / UDP 123')],
 dict(kind='pair',caption='送信元とFQDNを左右固定で比較',flows=[
  dict(id='apim',rule='apim-platform-time',source='APIM区画',destination='time.windows.com',label='UDP 123'),
  dict(id='vm',rule='management-time',source='管理VM区画',destination='ntp.ubuntu.com',label='UDP 123')]),
 note='注釈：両方ともNetwork。名前をDNSでIPへ解決して照合する。所属名だけで送信元を決めない。')

composite('vm-aca','管理VMとACAの管理・監視・認証','基盤依存通信',[
 ('vm-dest','管理VMの宛先','Entra / ARM / Monitor / Storageのタグ'),
 ('vm-purpose','目的（注釈）','認証・配備・保守・監視'),
 ('aca-rule','ACAのルール','aca-monitor-https'),
 ('aca-dest','ACAの宛先','AzureMonitor / AzureActiveDirectory'),
 ('aca-ports','ACAの通信条件','ACA区画 / TCP 443 / Network Allow')],
 vertical('ACA区画','Azure Firewall','監視・認証のタグ',details=('送信元を限定','aca-monitor-https','AzureMonitor / Entra'),labels=('TCP 443','Network / Allow')),
 note='注釈：このNetwork許可に一致する通信は、Applicationの照合へ進まない。')

composite('custom-dns','独自DNS許可の生成条件・送信元・経路','DNS・共通設定',[
 ('condition','作成する条件','network.dnsServersが空でない場合'),
 ('source','送信元','APIM区画・管理VM区画（ACAは含まない）'),
 ('destination','宛先','指定した独自DNSのIP群'),('ports','通信・ポート','TCP・UDP / 53'),
 ('public','公開設定例','dnsServers: [] → このコレクションは作成しない')],
 dict(kind='routes',caption='DNS問い合わせが選ぶ経路を確認',upper=['APIM / VM','Firewall','独自DNS'],lower=['APIM / VM','独自DNS'],
      upperLabel='FWを経由する場合',lowerLabel='同じVNet内など、具体的な直接経路の場合'),
 note='ルール名：custom-dns。許可の定義と実際の名前解決経路は別に確認する。')

fields('dns-proxy','Firewall側のDNSとVNet側のDNSを区別する','DNS・共通設定',[
 ('proxy','enableProxy','true'),('fw-dns','FW上流DNSの優先指定','network.firewall.dnsServers'),
 ('fallback','未指定の場合','network.dnsServersを使用'),
 ('vnet','VNet側DNS（注釈）','別途確認。FW IPへ自動で切り替わらない')],
 notice='教材注釈：DNS proxyはNetwork FQDNの名前解決などを支える。')

def quiz(key,title,rows,choices,answer,note):
    composite(key,title,'確認問題',rows,
       dict(kind='choices',caption='判断する候補（教材の問い）',choices=[dict(id=i,label=s) for i,s in choices]),
       note=note,notice='通信条件を確認し、該当する判断を考える。')
    VIEWS[key]['answerTarget']=answer

quiz('quiz-storage','確認1：Storage通信は二重の許可が必要か',[
 ('source','送信元','APIM区画'),('destination','宛先','StorageタグのIP'),
 ('ports','通信・ポート','TCP / 443'),('premise','設問の前提','NetworkルールのAllowに一致済み')],
 [('required','ApplicationのFQDN登録も必要'),('not-required','FQDN登録を追加で重ねない')],
 'not-required','条件と評価順を照合し、追加登録が必要かを判断する。')
quiz('quiz-ntp','確認2：FQDNを使うNTPはどちらの種類か',[
 ('source','送信元','管理VM区画'),('destination','宛先FQDN','ntp.ubuntu.com'),
 ('protocol','プロトコル','UDP'),('port','宛先ポート','123')],
 [('network','Networkルール'),('application','Applicationルール')],
 'network','名前の見た目だけでなく、プロトコルと照合に使う情報を考える。')
quiz('quiz-pe','確認3：SQL PEへの接続失敗をどう考えるか',[
 ('source','送信元','ACA区画'),('destination','宛先','SQL Private Endpointの実IP'),
 ('ports','通信・ポート','TCP / 1433'),('symptom','設問の症状','接続できない（稼働環境の実測ではない）')],
 [('firewall','Firewallを経由する経路'),('direct','PE宛ての具体的な直接経路')],
 'direct','まず経路を確認する。宛先IP・Private DNS・NSG・認証も同じ通信として照合。')

composite('linux-web','Network不一致のWeb通信はFQDNで照合する','ルールの種類',[
 ('rule','ルール名','vm-certificate-and-linux-packages'),('source','送信元','管理VM区画'),
 ('destination','宛先FQDN','archive.ubuntu.com'),('ports','通信・ポート','HTTP / 80'),
 ('action','種別 / Action','Application / Allow')],
 vertical('管理VM','Applicationルール','archive.ubuntu.com',details=('Networkは不一致の例','WebのFQDNを照合','HTTP 80'),labels=('Network不一致','条件に一致 → Allow')),
 note='対象ルール：vm-certificate-and-linux-packages。未許可先は組み込み許可も確認して最終判定。')

fields('change-review','変更前後に同じ通信について照合する','保守・まとめ',[
 ('source','目的・送信元','誰が何のために使うか / Firewall到達時の送信元IP'),
 ('destination','宛先','実際の宛先IP・ホスト名'),('ports','通信条件','プロトコルと宛先ポート'),
 ('policy','規則の照合','種別 → グループ → コレクション → 条件 / Action'),
 ('after','変更後の確認','新しい接続とログで、意図した判定になったかを確認')],
 notice='教材注釈：この表は変更時の確認観点で、製品の設定項目名ではない。')
fields('alert-settings','本番の公開config：検知のAlertと規則のDeny','保守・まとめ',[
 ('sku','Firewall SKU','Premium'),('idps','IDPS','Alert'),
 ('ti','Threat Intelligence','Alert')],
 notice='教材注釈：検出しただけで自動遮断するモードとは説明しない。詳細は第10回。')
fields('final-review','今日の到達点と、次回の外向き通信','保守・まとめ',[
 ('path','経路（教材注釈）','まず、Firewallへ届く通信かを確認する'),
 ('order','評価順（教材注釈）','Networkが先。一致で終了 / 不一致のWebはApplicationへ'),
 ('reason','許可の理由（教材注釈）','送信元・宛先・ポートと、使う目的を結び付ける')],
 notice='まとめ：次回はWebの依存先と、接続先から見える出口IPを追う。')

GROUPS=[
 (5,10,'fw-association-path',{6:['policy'],7:['policy'],9:['pe','direct'],10:['basis']}),
 (11,14,'rule-inputs',{12:['source','destination','protocol'],14:['destination','protocol','host']}),
 (15,17,'application-tls',{16:['tls','sni'],17:['waf','auth']}),
 (18,19,'ntp-fqdn',{19:['method','port','firewall','target']}),
 (20,23,'rule-processing',{21:['network'],23:['network']}),
 (24,26,'hierarchy',{24:['group','collection','action'],26:['group','collection','individual']}),
 (27,29,'priority-comparison',{28:['network','application'],29:['broker','gateway']}),
 (30,31,'allow-default',{31:['allow','deny','builtin','default']}),
 (32,35,'agw-apim',{33:['source','destination','protocol','firewall'],35:['ports','target']}),
 (36,37,'agw-aca',{37:['destination','ports','route','target']}),
 (38,40,'stateful-return',{39:['response','reverse','route'],40:['new','route']}),
 (41,41,'direct-internal',{41:['broker','sql','vault']}),
 (42,49,'apim-storage',{43:['source'],44:['rule','source','ports'],46:['destination','target'],48:['action','firewall'],49:['action']}),
 (50,52,'apim-platform',{50:['apim-sql','vault','entra','monitor'],52:['apim-sql','business-sql']}),
 (53,56,'ntp-sources',{54:['apim','apim-dest'],56:['apim','vm','vm-dest']}),
 (57,59,'vm-aca',{57:['vm-dest','vm-purpose'],58:['aca-rule','aca-dest','aca-ports','firewall']}),
 (60,63,'custom-dns',{60:['condition','source','destination','ports'],62:['source','public'],63:['direct']}),
 (64,65,'dns-proxy',{65:['proxy','fw-dns','fallback','vnet']}),
 (66,67,'quiz-storage',{67:['not-required','premise']}),
 (68,69,'quiz-ntp',{69:['network','protocol','port']}),
 (70,71,'quiz-pe',{71:['direct','destination']}),
 (72,73,'linux-web',{73:['rule','action','firewall']}),
 (74,76,'change-review',{75:['source','destination','ports','policy'],76:['source','after']}),
 (77,78,'alert-settings',{78:['idps','ti']}),
 (79,80,'final-review',{79:['path','order','reason']}),
]


def apply_settings_plan(script, folder):
    script['visualStyle']=STYLE
    script['visualRevision']='20261102-settings-v2'
    artifacts={}
    groups=[]
    for start,end,key,focus_by_number in GROUPS:
        spec=VIEWS[key]
        groups.append(dict(viewId=key,fromTurn=start,throughTurn=end,
                           questionTurns=[n for n in range(start,end+1) if script['turns'][n-1]['speaker']=='mako']))
        for number in range(start,end+1):
            t=script['turns'][number-1]
            focus=focus_by_number.get(number,[])
            if spec['layout']=='settings':
                visual=deepcopy(spec)
                visual['highlightRows' if 'table' in spec else 'highlightFields']=focus
            else:
                state='neutral' if not focus else '-'.join(sorted(focus))
                name=f'{key}--{state}.svg'
                artifacts[name]=dict(spec=spec,highlightTargets=focus)
                visual=dict(layout='svg',viewId=key,heading=spec['heading'],
                            svg='diagrams/'+name,highlightTargets=focus,
                            settingsStyle=STYLE,composition='settings-and-supporting-diagram')
            t['visual']=visual
    (folder/'settings-views.json').write_text(json.dumps(dict(visualStyle=STYLE,views=VIEWS,artifacts=artifacts,groups=groups),ensure_ascii=False,indent=2)+'\n')
    assert all(t['visual'].get('viewId') for t in script['turns'] if t['chapterId']!='opening')
    return script
