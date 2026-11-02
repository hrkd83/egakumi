#!/usr/bin/env python3
"""Episode A1 authoring source; only emits this episode's script."""
import copy
import json
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
S=json.loads((HERE/'script.json').read_text())
ref=lambda a:dict(file='../01-network-control.html',anchor=a)
chapters=[
 ('opening','今回の問い','ハブ接続で何が変わるかを問う。',['hub-spoke']),
 ('scope','共有する機能を選ぶ','private接続・出口・DNS・Gatewayの採用を分ける。',['hub-spoke']),
 ('peering','双方向ピアリングと転送','アドレス重複、両側接続、転送を受ける方向を説明する。',['hub-peering']),
 ('egress','APIMの公開宛てを追う','ハブFWを通る往路・SNAT・復路を追う。',['hub-udr']),
 ('local','入口の往復を維持する','AppGWとAPIM・ACAのローカルFW経由を区別する。',['hub-udr']),
 ('subnets','区画別に変更する','管理VM・ACA・PE・FW区画の扱いを選ぶ。',['hub-udr']),
 ('gateway','Gatewayを共有する条件','Gateway共有の両側設定と社内側の復路を説明する。',['hub-peering']),
 ('propagation','Gateway経路伝搬を選ぶ','設定の真偽、ACA固定、経路選択と伝搬停止の影響を説明する。',['hub-propagation']),
 ('check','三つの確認問題','接続・出口・伝搬の判断を確認する。',['hub-peering','hub-udr','hub-propagation']),
 ('summary','まとめと次の問い','往復経路の設計を整理し、次回のDNSと切替へつなぐ。',['hub-spoke','hub-udr','hub-propagation'])
]

def cards(heading,*items):
 return dict(layout='cards',heading=heading,cards=[dict(title=t,body=b) for t,b in items])
def node(id,label,detail,x,y,w=260,h=100):
 return dict(id=id,label=label,detail=detail,x=x,y=y,width=w,height=h)
def diag(heading,nodes,edges,focus=()):
 return dict(layout='diagram',heading=heading,nodes=nodes,edges=edges,focus=list(focus))
def edge(a,b,label=None,**kw):
 return dict(from_=a,to=b,**({'label':label} if label else {}),**kw)
def E(a,b,label=None,**kw):
 v=edge(a,b,label,**kw);v['from']=v.pop('from_');return v

V={
 'opening':dict(layout='hologram',heading='ハブにつなぐと、帰り道はどうなる？'),
 'choices':cards('共有する機能を、一つずつ選ぶ',('private接続',['社内・他ネットワークへ','必要な経路と復路']),('公開宛ての出口',['対象区画を選んで集約','ハブFWの許可とSNAT']),('DNS・Gateway',['DNSは別の採用判断','Gateway共有も必要時のみ'])),
 'ownership':cards('企業側とCore側の担当をそろえる',('企業側',['ハブVNet・FW・Gateway','共有設備と変更権限','ハブ側の経路・許可']),('Core側',['接続先とCIDRの確認','スポーク側ピアリング','対象区画のUDR・復路'])),
 'maintain':cards('維持する構成を、先に決める',('入口の検査',['公開入口はAppGW','内部中継はローカルFW','APIM・ACAへの往復']),('private接続',['既存の6サブネット','PEへの具体的な直接経路','共有設備は追加設計'])),
 'cidr':cards('CIDRを、接続先全体と照合する',('Coreの全VNet CIDR',['ハブだけでなく','他スポーク・社内も照合','一部のサブネットだけで判断しない']),('重複がある場合',['接続前に再採番等を設計','サービスへの影響も確認','重複がなければ現行を維持'])),
 'peering':diag('両側のピアリングをConnectedへ',[node('core','Core / スポーク','スポーク側の接続',40,165,350),node('hub','企業ハブ','ハブ側の接続',815,165,350)],[E('core','hub','Core → ハブ',points=[[390,195],[815,195]]),E('hub','core','ハブ → Core',points=[[815,240],[390,240]],color='#8bcaff')],['core','hub']),
 'forward':diag('転送を受ける側で、転送トラフィックを許可',[node('remote','社内・他スポーク','元の送信元',20,170,300),node('hubfw','ハブFW / NVA','中継する装置',455,170,300),node('core','Core / スポーク','転送を受け入れる',890,170,300)],[E('remote','hubfw'),E('hubfw','core','転送')],['core']),
 'nontransit':cards('ピアリングは、自動の中継網を作らない',('接続の状態',['両側Connected','VNetアクセスを許可','NSG・FWの判定は別']),('中継の成立',['ハブFW / NVA等の実体','転送を受ける方向の許可','宛先ごとの往路と復路'])),
 'out':diag('主例：APIM → ハブFW → 公開依存先',[node('apim','APIM','公開宛ての要求',20,155,280),node('hubfw','ハブFW','許可・SNAT',455,155,280),node('public','公開依存先','外部HTTPS',890,155,280)],[E('apim','hubfw','APIMの0/0'),E('hubfw','public','公開宛て')],['apim','hubfw']),
 'return':diag('公開宛ての復路も、同じハブFWを通る',[node('apim','APIM','要求を開始した側',20,140,280),node('hubfw','ハブFW','SNAT状態と経路',455,140,280),node('public','公開依存先','応答する側',890,140,280)],[E('apim','hubfw','往路',points=[[300,170],[455,170]]),E('hubfw','public','往路',points=[[735,170],[890,170]]),E('public','hubfw','復路',points=[[890,215],[815,215],[815,330],[595,330],[595,240]],color='#8bcaff'),E('hubfw','apim','復路',points=[[455,215],[380,215],[380,330],[160,330],[160,240]],color='#8bcaff')],['hubfw']),
 'default':cards('変更対象はAPIMのdefault-egress',('採用する変更',['0.0.0.0/0 → ハブFW','到達可能なprivate IP','企業側が指定する次ホップ']),('合わせて準備',['APIMからの依存先許可','ハブ側のSNATと復路','接続先の送信元IP許可'])),
 'specific':diag('0/0より、宛先に合う具体的な経路',[node('apim','APIM','宛先ごとに経路を選択',30,160,290),node('pe','PE / VNet内宛て','具体的な直接経路',650,50,500),node('hubfw','ハブFW','具体的な経路がない公開宛て',650,320,500)],[E('apim','pe','PEへの経路',dashed=True,color='#8bcaff'),E('apim','hubfw','既定経路 0/0')],['pe','hubfw']),
 'local':diag('入口の往復は、ローカルFW経由を維持',[node('agw','AppGW','公開入口',20,155,280),node('localfw','ローカルFW','入口の検査',455,155,280),node('apim','APIM / ACA','内部の入口',890,155,280)],[E('agw','localfw','要求',points=[[300,185],[455,185]]),E('localfw','apim','要求',points=[[735,185],[890,185]]),E('apim','localfw','応答',points=[[890,230],[820,230],[820,350],[595,350],[595,255]],color='#8bcaff'),E('localfw','agw','応答',points=[[455,230],[380,230],[380,350],[160,350],[160,255]],color='#8bcaff')],['localfw']),
 'apim-routes':cards('APIMの出口を変えても、復路と管理経路は残す',('公開宛て',['default-egress','0/0 → ハブFW','出口集約を採用時のみ']),('AppGWへの応答',['agw-return-via-firewall','AppGW区画 → ローカルFW','入口の往復をそろえる']),('Azure管理',['apim-management-direct','ApiManagement → Internet','必要な直接経路を維持'])),
 'agw-routes':cards('通常のAppGW v2を、一律に強制トンネルしない',('内部入口へ',['実APIM VIP /32','実ACA入口IP /32','次ホップはローカルFW']),('既定経路に注意',['0/0 → ハブFWを一律追加しない','Gatewayからの0/0伝搬も確認','Azure制御系の直接経路を維持'])),
 'vm':cards('管理VM：保守の帰り道も確認',('公開宛ての変更',['対象のdefault-egressを変更','認証・更新・ビルド・監視','証明書やDNS APIも事前許可']),('private接続の維持',['社内の運用元 → 管理VM','管理VM → 運用元の復路','PEへの直接接続も確認'])),
 'aca':cards('ACA：出口と入口の復路を分ける',('公開宛て',['Portal・Job・実行基盤','依存先の許可を先に準備','採用時にdefault-egressを変更']),('維持する経路',['agw-return → ローカルFW','ACR・SQL・Key Vault PE','APIMからACAへの直接経路'])),
 'no-rt':cards('PEとローカルFW区画へ、一律にRTを足さない',('PE区画',['現在は独自RTなし','具体的なprivate接続を維持','サービス本体の出口とは別']),('AzureFirewallSubnet',['現在は独自RTなし','ローカルFWの動作を維持','撤去は別の再設計'])),
 'gateway':diag('Gateway共有は、社内接続に必要な場合だけ',[node('onprem','社内・オンプレミス','スポークCIDRへの復路',20,150,300),node('gw','ハブGateway','VPN / ExpressRoute',455,150,300),node('spoke','Core / スポーク','社内宛ての経路',890,150,300)],[E('onprem','gw'),E('gw','spoke'),E('spoke','gw','復路',points=[[1040,250],[1040,365],[605,365],[605,250]],color='#8bcaff'),E('gw','onprem','復路',points=[[555,250],[555,365],[170,365],[170,250]],color='#8bcaff')],['gw']),
 'gateway-flags':cards('Gateway transitの設定は、両側で役割が違う',('ハブ側',['allowGatewayTransit','ハブGatewayを共有する','VPN / ExpressRoute']),('スポーク側',['useRemoteGateways','共有Gatewayを利用する','自分のGatewayとの併用不可']),('確認する制約',['利用は1ピアリングのみ','社内側にも復路が必要','FWやDNS利用だけなら不要'])),
 'propagate':cards('利用者設定とAzure属性は、真偽が反転する',('伝搬する',['bgpRoutePropagation: true','↓','disableBgpRoutePropagation: false']),('伝搬しない',['bgpRoutePropagation: false','↓','disableBgpRoutePropagation: true'])),
 'propagate-scope':cards('伝搬の扱いは、サブネットごとに確認',('AppGW・APIM・管理VM',['利用者設定で切替可能','agw / apim / management','既定はtrue：伝搬する']),('ACA',['disableBgpRoutePropagation: true','現行コードで固定','ACA用の公開切替キーなし'])),
 'stop':cards('伝搬停止は、既定経路だけを除く操作ではない',('失い得る経路',['Gateway由来の社内宛て','中央DNS宛て等の経路','ピアリング経路の削除とは別']),('停止前の設計',['必要な宛先へ明示UDR等','相手側からの復路','通信ごとの実効経路を比較'])),
 'priority':cards('経路の優先順位は、実効経路で確かめる',('基本の比較',['最長一致を先に見る','同じプレフィックスなら','UDR → BGP → システム']),('例外と確認',['VNet・ピアリング等に例外','BGPや0/0で全経路は上書き不可','両端と中央装置を一組で確認'])),
 'q1':dict(layout='quiz',heading='確認①：接続したら全部ハブFW経由？',bullets=['両側ピアリングがConnectedになった。','すべての通信はハブFWを通る？','経路・転送・許可・復路を別に確認する。']),
 'q2':dict(layout='quiz',heading='確認②：APIMの出口集約で何を残す？',bullets=['APIMの0/0をハブFWへ変更した。','AppGWへの応答も、ハブFWへ変える？','AppGW区画への復路はローカルFWを維持。']),
 'q3':dict(layout='quiz',heading='確認③：伝搬を止めれば安全？',bullets=['AppGWへの0/0伝搬を避けたい。','伝搬を止めると、既定経路だけが消える？','社内・中央DNS宛ての経路と復路も用意。']),
 'summary':dict(layout='summary',heading='接続・出口・Gatewayを選び、往復で説明する',bullets=['共有する機能と、企業側・Core側の担当を決める。','公開宛てはハブFW、入口はローカルFWの往復。','PE直接経路と必要な社内経路を維持する。','伝搬を区画別に選び、実効経路で照合する。']),
 'next':cards('次回A2：経路を、DNS・制御・切替につなげる',('名前と許可',['中央DNSとPrivate DNS','NSG・FWの通信許可','実際の送信元と出口IP']),('切替と管理',['段階切替・新規接続で検証','切戻しの準備','IaCと再配備の管理']))
}

rows={
'opening':'''mako|opening|企業のハブにつなぐと、今のFirewallはもう要らないんですか。行き先を変えたら、帰りも同じ道になるのか気になります。
manabu|opening|共有したいのが社内接続なのか、公開通信の出口なのかで変わります。使う機能と、今のまま残す役割を先に決めましょう。
ikumi|opening|今日はAPIMの公開宛て通信を、ハブFirewallへ集約する例です。入口の通信と区別して、往路と復路を図で追います。
mako|opening|つないで終わり、ではないんですね。第8回までの経路を土台に、変更するところを一緒に見ていきましょう。''',
'scope':'''manabu|choices|まず目的を分けます。社内から管理VMへprivate接続したいことと、公開依存先へ出る出口をまとめたいことは、別の要件です。
mako|choices|社内へつなぐだけなら、インターネット向けの出口やDNSまで、同時に変える必要はないんですね。
ikumi|choices|はい。社内接続、出口集約、中央DNS、VPNやExpressRouteの共有を個別に選びます。出口も、対象となる送信元の区画ごとに判断します。
manabu|ownership|企業側とは、接続先VNet、アドレス計画、共有設備、両側の変更担当を先にそろえます。
mako|ownership|現在のCoreを配備すれば、企業ハブやピアリングまで自動で作られる、という前提にはできませんか。
ikumi|ownership|できません。現行IaCは、ハブ、ピアリング、VPN・ExpressRoute Gateway、中央DNS Resolverを作りません。企業側と追加設計します。
manabu|maintain|六つのサブネットとサービスの役割は維持します。ローカルFirewallは、AppGWと内部入口の検査を続けます。
ikumi|maintain|PEへのprivate接続も維持します。ローカルFirewallの撤去や公開入口の中央化まで行うなら、送信元や制御も含む別の再設計です。
mako|maintain|ここで見るのは、教材と現行定義に基づく接続案です。稼働環境へ変更を適用し、疎通を実測した結果とは分けて進めます。''',
'peering':'''mako|cidr|では接続の準備からです。企業ハブとCoreのCIDRが違っていれば、アドレス計画の確認は終わりでしょうか。
ikumi|cidr|ハブだけでは足りません。Coreの全VNet CIDRを、ハブ、他スポーク、接続先の社内ネットワークと照合します。重複による経路衝突を避けます。
manabu|cidr|あとからつながらない区画を作らないためですね。重複があれば接続前に再採番などの影響を確認します。
ikumi|peering|重複がなければ既存CIDRを維持できます。そのうえで、Coreからハブ、ハブからCoreという、両側のピアリングを作ります。
mako|peering|Core側の接続だけを作って、ハブの設定は相手任せのまま、完成としないわけですね。
ikumi|peering|そうです。VNetアクセスを許可し、両側の状態がConnectedとなることを確認します。その接続状態と、個別通信の許可は別です。
ikumi|forward|ハブFirewallやNVAが、別のネットワークから来た通信をCoreへ転送するなら、Core側の対ハブピアリングで転送トラフィックを許可します。
mako|forward|allowForwardedTrafficは、転送された通信を受け入れる側で見る設定なんですね。逆方向の転送を受ける場合はどうですか。
ikumi|forward|その場合はハブ側にも必要です。ただし、この許可だけでUDRや中継装置が作られるわけではありません。実際の中継機能と経路を用意します。
manabu|nontransit|どこまで直接届き、どこから中央装置が中継するかを宛先ごとに決める。NSG、Firewall、往復経路もそろえて、初めて通信を説明できます。''',
'egress':'''mako|out|ここから主例です。APIMから公開依存先への要求を、ハブFirewallへ集約する場合、どこを変えるのでしょうか。
ikumi|default|APIM用のdefault-egressです。0/0の次ホップを、企業側指定の、到達可能なハブFirewallのprivate IPへ変えます。
manabu|default|この変更は、出口集約を採用する場合だけです。公開通信を中央で管理する目的と、対象の区画を明確にしてから変更します。
mako|out|図の矢印では、APIMからハブFirewall、その先の公開依存先ですね。以前のローカルFirewallは、この公開通信では通らない。
ikumi|out|この例ではそうです。先にハブ側でAPIMの依存先を許可します。経路が届くだけでは、外部バックエンドや認証先への接続が許可されたことになりません。
ikumi|return|公開宛てでは、ハブ側のSNATも設計します。相手に見える送信元が変わるため、接続先の許可元IPも、採用する出口と整合させます。
mako|return|外部からの応答は、その送信元だったハブの出口へ戻り、同じハブFirewallを経由してAPIMへ返る、という流れですか。
ikumi|return|はい。SNATの状態と、ハブからCoreへ届く経路、必要な転送許可を一組で確認します。往路だけのUDR変更で完成とは判断しません。
manabu|return|出口をまとめる価値は、中央の方針と観測に合わせられることです。その分、中央側の許可や復路にも担当と確認先が必要になります。
mako|specific|APIMの0/0を変えたら、FoundryのPEへ向かう通信も、すべてハブへ送られてしまいますか。
ikumi|specific|PEやVNet内宛てには、より具体的な経路があります。既定経路より先に選ばれるので、0/0だけですべての宛先を集約できるとは考えません。''',
'local':'''mako|local|次は利用者から来る要求ですね。公開入口のAppGWからAPIMへ入る経路も、出口と一緒にハブへ変えるんでしょうか。
ikumi|local|この接続案では変えません。AppGWからローカルFirewallを通ってAPIMへ進み、応答も同じローカルFirewallを通ってAppGWへ戻します。
manabu|local|公開バックエンドへの接続と、AppGWから受ける接続は別です。バックエンド応答を受けたAPIMが、今度はAppGWへの応答を返します。
ikumi|apim-routes|APIMでは、AppGW区画へ戻す具体的な経路を残します。復路をローカルFirewallへ向けるためです。
mako|apim-routes|それを消してしまうと、入口ではローカルFirewallを通ったのに、応答だけ別の道へ行く可能性がありますね。
ikumi|apim-routes|そうです。片方向だけがFirewallを通る非対称を避けます。さらに、ApiManagementサービスタグ宛てをInternetへ向ける管理用の直接経路も維持します。
ikumi|agw-routes|AppGW側も、実APIM VIPの/32と、実ACA入口IPの/32をローカルFirewallへ向ける経路を残します。入口ごとの実IPを確認します。
mako|agw-routes|では、AppGWにも0/0を追加してハブFirewallへ送る、という統一はしないんですね。
ikumi|agw-routes|はい。通常のAppGW v2へ、FirewallやNVA向けの既定経路を一律追加しません。Azure制御系の直接通信と、サービスの制約を守る必要があります。''',
'subnets':'''mako|vm|APIM以外はどうでしょう。前回扱った管理VMも、必要なら公開宛ての出口をハブへ集約できますか。
ikumi|vm|できます。対象のdefault-egressを変える前に、認証、更新、ビルド、証明書、DNS API、監視などの依存先をハブ側で許可します。
manabu|vm|社内から管理VMへ入れることに加え、管理VMから運用元へ戻れることも必要です。受信の許可だけでSSHの往復が完成したとは言えません。
ikumi|aca|ACAも、採用する場合に公開宛ての既定経路を変えます。Portal、Job、実行基盤の依存先を先に確認し、AppGWへのagw-returnはローカルFirewallへ残します。
mako|aca|ACAからACRやSQLなどのPEへ向かう通信は、公開出口と分けて見るんですね。
ikumi|aca|はい。Key VaultのPE、APIMからACAへの直接経路も含めて確認します。公開出口の変更だけで、これらの通信まで中央検査されるとは扱いません。
mako|no-rt|PE区画やAzureFirewallSubnetにも、ハブ向けルートテーブルを付けて統一するのでしょうか。
ikumi|no-rt|現在はどちらにも独自のルートテーブルがなく、一律には追加しません。PEの直接接続とローカルFirewallの動作を維持します。サービス本体の出口もPE側のUDRとは別です。''',
'gateway':'''manabu|gateway|ここからは、企業のVPNやExpressRoute Gatewayを共有する場合です。ハブFirewallを使うだけなら、この共有も必須なのでしょうか。
ikumi|gateway|必須ではありません。社内やオンプレミスへの接続で、ハブのGatewayを実際に使う場合に採用します。中央DNSの利用だけで有効にする設定でもありません。
mako|gateway-flags|使うことを選んだ場合、ピアリングの両側には何を設定しますか。
ikumi|gateway-flags|ハブ側ではallowGatewayTransit、スポーク側ではuseRemoteGatewaysを設定します。共有を提供する側と、利用する側で設定名が異なります。
ikumi|gateway-flags|スポーク自身のGatewayとは併用できません。リモートGatewayの利用は一つのピアリングのみなど、構成の制約も確認します。
mako|gateway|設定できたら、Coreから社内へ届くだけでなく、社内からCoreのCIDRへ戻れることも必要ですね。
ikumi|gateway|そのとおりです。社内側の復路も企業担当者と設計します。Gateway共有、転送の許可、実際の往復経路を、同じ通信について確認します。''',
'propagation':'''mako|propagate-scope|Gatewayを共有すると、独立VNetのときにはなかった経路が届くんですね。どの区画が受け取るかは、どこで決めますか。
ikumi|propagate-scope|現行ではbgpRoutePropagationで、AppGW、APIM、管理VMの伝搬を切り替えます。既定はtrue、伝搬する設定です。
ikumi|propagate|Azure側の属性はdisableBgpRoutePropagationです。利用者設定のtrueはAzure属性ではfalse。伝搬するかと、止めるかで、真偽が反転します。
mako|propagate|利用者設定をfalseにすると、Azure属性はtrueになり、Gateway由来の経路を伝搬しない。属性名まで読む必要がありますね。
ikumi|stop|はい。しかも既定経路だけを除外する設定ではありません。社内や中央DNSに必要なGateway由来の経路まで失う可能性があります。
manabu|stop|止めることを選ぶなら、必要な宛先への明示経路と復路を先に用意する。不要な道を避ける判断と、必要な道を残す判断を一緒にします。
ikumi|propagate-scope|ACAは別です。現行コードではdisableBgpRoutePropagationがtrueに固定され、公開の切替キーはありません。社内宛てには明示UDR等かコード変更を検討します。
mako|agw-routes|AppGWには、ハブから0/0が伝搬してくる場合もありますか。自分で既定UDRを足していなくても確認が必要ですね。
ikumi|agw-routes|その確認が必要です。通常のAppGW v2を強制トンネルしないよう、必要ならagwの伝搬を止めます。その前に、必要な社内やDNS宛ての経路を補います。
ikumi|priority|経路選択は基本的に最長一致、同じプレフィックスならUDR、BGP、システムの順です。ただしVNetやピアリング等のシステム経路には優先の例外があり、実効経路で確かめます。
manabu|priority|伝搬停止はピアリング経路を消す操作でもありません。両端と中央装置を一組で見て、公開宛て、社内、PE、入口への応答を比較しましょう。''',
'check':'''mako|q1|ここで三問です。一つ目。両側のピアリングがConnectedになりました。これで、すべての通信がハブFirewallを通ると言えますか。
manabu|q1|言えません。接続したことと、通信の経路は別です。どの機能を共有し、どの宛先を中央装置へ送るかを決める必要があります。
ikumi|q1|転送を受ける方向の許可、実際の中継装置、UDR、NSGやFirewall、復路まで確認します。他スポークへも自動で中継されるとは判断しません。
mako|q2|二つ目。APIMの公開宛て0/0をハブFirewallへ変えました。AppGW区画への復路も、ハブ向けに変えるべきですか。
ikumi|q2|この案では変えません。AppGWへの復路はローカルFirewallへ残し、入口の往復をそろえます。公開宛ての往復は、別にハブFirewallを通します。
manabu|q2|出口を集約しても、入口の検査の役割は維持する。宛先と、その要求を開始した側を言葉にすると、二つの往復を区別できますね。
mako|q3|最後です。AppGWへの既定経路の伝搬を避けたいので、伝搬を無効にします。既定経路だけが消える、と考えてよいですか。
ikumi|q3|いいえ。必要な社内や中央DNS宛て経路も対象になり得ます。明示経路と復路を先に設計し、変更後の実効経路で確認します。ACAの固定設定とも区別します。
manabu|q3|どの問題も、設定一つで結果を決めつけないことですね。つながる範囲、通す装置、帰り道を、通信ごとに対応付けて説明します。''',
'summary':'''mako|summary|今日の要点です。private接続、出口集約、中央DNS、Gateway共有を個別に選び、企業側とCore側の変更担当を合わせます。
ikumi|summary|APIMの公開宛てはハブFirewallの往復、AppGWとの通信はローカルFirewallの往復です。PEへの直接経路と、必要な社内経路も残してください。
mako|next|次のA2では、この経路に中央DNS、Private DNS、NSGやFirewallの許可を合わせます。切替後の送信元や出口IPも確認します。
manabu|next|さらに段階切替、新しい接続での検証、切戻し、IaCと再配備までつなげます。今日描いた往復を、維持して運用する方法へ進みましょう。'''
}

# Use Japanese clauses and whole English identifiers as subtitle break units.
def units(s):
 return sum(.56 if ord(c)<128 else 1 for c in s)
def split_lines(s):
 if units(s)<=36:return [s]
 candidates=[]
 for i in range(1,len(s)):
  if units(s[:i])>39 or units(s[i:])>39:continue
  if re.match(r'[A-Za-z0-9_./-]',s[i-1:i]) and re.match(r'[A-Za-z0-9_./-]',s[i:i+1]):continue
  if s[i] in '、。，．！？）】」』ー':continue
  score=abs(units(s[:i])-units(s[i:]))
  if s[i-1] in '、。！？':score-=18
  if s[i-1] in 'はがをにでとのもへや':score-=5
  candidates.append((score,i))
 if not candidates:
  raise ValueError('Subtitle group too long: '+s)
 _,i=min(candidates)
 return [s[:i],s[i:]]
def chunks(caption):
 # Each cue is at most two short lines. Sentence boundaries are preferred.
 clauses=re.findall(r'.*?[、。！？]|.+$',caption)
 groups=[];current=''
 for clause in clauses:
  if units(clause)>66:
   # Long clauses here are rare; find a grammatical particle boundary.
   while units(clause)>66:
    options=[i for i in range(15,len(clause)) if units(clause[:i])<55 and clause[i-1] in 'はがをにでとのもへや']
    i=options[-1] if options else 35
    if current:groups.append(current);current=''
    groups.append(clause[:i]);clause=clause[i:]
  if current and units(current+clause)>66:groups.append(current);current=''
  current+=clause
 if current:groups.append(current)
 return [dict(caption=s,lines=split_lines(s),weight=len(s)) for s in groups]

replacements=[
 ('disableBgpRoutePropagation','ディセーブル・ビージーピー・ルート・プロパゲーション'),
 ('network.bgpRoutePropagation','ネットワークのビージーピー・ルート・プロパゲーション'),
 ('bgpRoutePropagation','ビージーピー・ルート・プロパゲーション'),
 ('allowForwardedTraffic','アロー・フォワーデッド・トラフィック'),
 ('allowGatewayTransit','アロー・ゲートウェイ・トランジット'),
 ('useRemoteGateways','ユーズ・リモート・ゲートウェイズ'),
 ('agw-return-via-firewall','エージーダブリュー・リターン・バイア・ファイアウォール'),
 ('agw-return','エージーダブリュー・リターン'),('default-egress','デフォルト・イーグレス'),
 ('AzureFirewallSubnet','アジュール・ファイアウォール・サブネット'),
 ('ApiManagement','エーピーアイ・マネジメント'),
 ('Private DNS','プライベート・ディーエヌエス'),('DNS Resolver','ディーエヌエス・リゾルバー'),
 ('ExpressRoute Gateway','エクスプレスルート・ゲートウェイ'),('ExpressRoute','エクスプレスルート'),
 ('Gateway','ゲートウェイ'),('Firewall','ファイアウォール'),('Connected','コネクテッド'),
 ('management','マネジメント'),('Internet','インターネット'),('private','プライベート'),
 ('AppGW','アプリケーションゲートウェイ'),('Key Vault','キー・ボールト'),('Foundry','ファウンドリー'),
 ('Portal','ポータル'),('Azure','アジュール'),('Core','コア'),('VNet','ブイネット'),
 ('APIM','エーピーアイエム'),('ACA','エーシーエー'),('ACR','エーシーアール'),
 ('CIDR','サイダー'),('SNAT','スナット'),('UDR','ユーディーアール'),('NSG','エヌエスジー'),
 ('NVA','エヌブイエー'),('VPN','ブイピーエヌ'),('BGP','ビージーピー'),('DNS','ディーエヌエス'),
 ('SQL','エスキューエル'),('SSH','エスエスエイチ'),('VIP','ブイアイピー'),
 ('IaC','アイアック'),('Job','ジョブ'),('API','エーピーアイ'),('PE','プライベートエンドポイント'),
 ('VM','ブイエム'),('IP','アイピー'),('true','トゥルー'),('false','フォルス'),
 ('0.0.0.0/0','ゼロ・ドット・ゼロ・ドット・ゼロ・ドット・ゼロ・スラッシュ・ゼロ'),
 ('0/0','ゼロ・スラッシュ・ゼロ'),('/32','スラッシュ三十二'),('v2','バージョン二'),
 ('agw','エージーダブリュー'),('apim','エーピーアイエム'),('A2','エー・ツー')]
turns=[]
for chapter,*_ in chapters:
 for row in rows[chapter].splitlines():
  speaker,vis,caption=row.split('|',2);speech=caption
  for a,b in replacements:speech=speech.replace(a,b)
  cue=chunks(caption)
  for item in cue:
   if item['caption']=='ただしVNetやピアリング等のシステム経路には優先の例外があり、実効経路で確かめます。':
    item['lines']=['ただしVNetやピアリング等のシステム経路には','優先の例外があり、実効経路で確かめます。']
  delivery={
   'mako':'視聴者の疑問を一緒に確かめる自然な質問。納得した部分は短く穏やかに返し、通常速度で話す。',
   'manabu':'共有する目的や判断の理由を同僚へ語る。落ち着いた温かさで、価値と責任を結び付け、通常速度で話す。',
   'ikumi':'図の矢印と宛先を順番に確かめるように明瞭に説明。技術用語の前後は少し区切るが、通常速度を維持する。'
  }[speaker]
  turns.append(dict(id=f'lessona1-turn-{len(turns)+1:03}-{speaker}',speaker=speaker,chapterId=chapter,speech=speech,caption=caption,subtitleLines=cue[0]['lines'],subtitleChunks=cue,gapAfterSeconds=.16,delivery=delivery,visual=copy.deepcopy(V[vis])))
S.update(title='A1 ハブ＆スポーク接続と往復経路',episodeLabel='必修 A1',seriesLabel='ハブ＆スポーク対応 / 第8回の後',description='APIMの公開宛て通信をハブFWへ集約する例で、双方向ピアリング・区画別UDR・復路・Gateway共有と経路伝搬を説明する。',status='draft',chapters=[dict(id=id,title=title,goal=goal,sourceRefs=[ref(a) for a in anchors]) for id,title,goal,anchors in chapters],turns=turns,sourceReferences=[ref(a) for a in ['hub-spoke','hub-peering','hub-udr','hub-propagation']]+[dict(file='../../setup/07-hub-spoke.md'),dict(file='../../../infra/modules/network.bicep')])
(HERE/'script.json').write_text(json.dumps(S,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(turns=len(turns),captionCharacters=sum(len(t['caption']) for t in turns),speechCharacters=sum(len(t['speech']) for t in turns),openingCaptionCharacters=sum(len(t['caption']) for t in turns if t['chapterId']=='opening'),chapters={c[0]:sum(t['chapterId']==c[0] for t in turns) for c in chapters}),ensure_ascii=False,indent=2))
