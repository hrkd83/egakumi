#!/usr/bin/env python3
"""Episode 07 authoring source; local only."""
import json,re
from pathlib import Path
HERE=Path(__file__).resolve().parent
S=HERE/'script.json'
s=json.loads(S.read_text())
def cards(heading,*pairs):return {'layout':'cards','heading':heading,'cards':[{'title':t,'body':b.split('|')} for t,b in pairs]}
def diagram(heading,nodes,edges,focus=()):
 return {'layout':'diagram','heading':heading,'nodes':[dict(id=i,label=l,detail=d,x=x,y=y,width=w,height=h) for i,l,d,x,y,w,h in nodes],'edges':edges,'focus':list(focus)}
def edge(a,b,label=None,**kw):return dict({'from':a,'to':b},**({'label':label} if label else {}),**kw)
pathnodes=[('agw','AppGW','新しいバックエンド接続',40,170,280,100),('fw','Firewall','Networkルール',455,170,260,100),('apim','APIM','受信・応答',870,170,280,100)]
V={
'opening':{'layout':'hologram','heading':'つながらないときの調べ方'},
'goal':cards('一つの通信で証拠をそろえる',('特定','時刻・通信元・名前|宛先IP・ポート'),('照合','DNS → 経路 → NSG|経由したFWのログ'),('判断','記録と通知を区別|確認範囲を残す')),
'path':diagram('主例 U1：AppGW → Firewall → APIM',pathnodes,[edge('agw','fw','TCP 80'),edge('fw','apim','TCP 80')],('agw','fw','apim')),
'keys':cards('調査票：同じ通信を探すための条件',('いつ・どこから','失敗時刻と時間帯|通信元の区画・IP'),('どこへ','問い合わせた名前|DNS回答の宛先IP'),('何の通信か','プロトコルとポート|新規接続か・症状')),
'dns':diagram('DNS：失敗した通信の送信元で確かめる',[
 ('src','AppGWの名前解決','元の端末とは条件が違う',40,170,300,100),('dns','参照するDNS','設定・転送・ゾーン',450,170,280,100),('answer','APIM内部IP','回答を経路照合へ渡す',850,170,300,100)], [edge('src','dns','問い合わせ'),edge('dns','answer','回答')],('answer',)),
'route':cards('経路：名前ではなく解決後の宛先で照合',('往路','AppGW側のUDR|APIM宛て → FW'),('復路','APIM側のUDR|AppGW宛て → FW'),('確認方法','IaC・関連付けを確認|取得可能な実効情報')),
'nsg':diagram('NSG：通信元の送信と宛先の受信を照合',pathnodes,[edge('agw','fw','送信NSG'),edge('fw','apim','受信NSG')],('agw','apim')),
'nsg-rules':cards('U1の許可：同じTCP 80を照合する',('AppGWの送信','allow-apim-http|APIM区画へ送信'),('APIMの受信','allow-agw-api|AppGW区画から受信'),('Firewall','agw-to-apim-http|内部中継の許可')),
'logtypes':cards('Firewallを通ったら、判定された種類へ',('Network','AZFWNetworkRule|IP・ポート・Protocol'),('Application','AZFWApplicationRule|Web宛先・FQDN'),('評価順に注意','Network一致なら|Applicationへ進まない')),
'logkeys':cards('ログ：対象FWと通信条件をそろえる',('対象を絞る','Resource ID・時刻|送信元・宛先・ポート'),('判定を見る','Action|ActionReason'),('設定へ戻る','Policy・RCG|Collection・Rule')),
'deny':cards('Deny：名前付きルールだけを探さない',('明示の判定','一致したルール|条件・優先順位へ戻る'),('既定拒否','ルールに一致しない|Default Actionも確認'),('証拠の範囲','拒否を観測した地点|他層の成功は別途確認')),
'allow':cards('Allowが証明するのはFWでの許可',('通信の次へ','受信側NSG・復路|接続先の応答'),('アプリの次へ','認証・認可・処理結果|サービス側のログ'),('結論の書き方','FWでは許可を確認|API全体の成功は別')),
'nolog':cards('ログが見つからないときの確認',('検索条件','時間帯・対象FW・LAW|通信条件・ログ種類'),('収集状態','診断設定・保存先|取り込みの状態'),('経路の前提','FWへ届いたか|FWを通る通信か')),
'direct':diagram('比較 D2：APIM → FoundryのPEは直接経路',[
 ('apim','APIM','送信NSG・DNS',40,170,280,100),('pe','Foundry PE','private IP・受信NSG',455,170,280,100),('fw','Firewall','この直接経路では通らない',870,170,280,100)], [edge('apim','pe','TCP 443')],('apim','pe')),
'walk':cards('演習：同じU1の接続失敗を追う',('分かったこと','DNS回答は内部IP|往復経路はFW経由'),('次に確かめる','両側NSGの条件|FWの判定と一致規則'),('演習上の仮定','稼働環境の実測ではない|一つの接続で照合')),
'record':cards('引き継ぎ：観測・未確認・次の確認先',('観測した事実','時刻・条件・対象資源|ログと適用設定'),('判断の範囲','拒否した地点|まだ確認していない層'),('変更後の確認','限定した変更を管理|同じ条件の新規接続')),
'stores':diagram('通信ログ：検索用と監査用の保存先',[
 ('fw','Firewall診断','Network / Application',40,170,310,100),('law','LAW','Dedicated形式・検索',490,55,300,100),('storage','日本側監査Storage','監査のための保護',490,310,300,100)], [edge('fw','law'),edge('fw','storage')],('law',)),
'scope':cards('保存しているログの範囲を確認する',('入口','AppGWアクセスログ|WAF判定とは区別'),('基盤','メトリック＝数値|Activity＝管理操作'),('収集範囲','全VNet通信ではない|Flow Logs等の定義なし')),
'alerts':diagram('ログ記録と通知は別の設定',[
 ('signal','状態・管理操作','メトリック / Activity',40,170,290,100),('rule','アラート条件','閾値・対象・評価窓',440,170,290,100),('mail','Action Group','登録された運用者へメール',840,170,320,100)], [edge('signal','rule'),edge('rule','mail','条件に一致')],('rule',)),
'metrics':cards('状態の異常：メトリックアラート',('AppGW','UnhealthyHostCount > 0|平均・5分窓 / 毎分'),('Firewall','FirewallHealth < 100|平均・5分窓 / 毎分'),('意味','状態異常の通知|Deny全件通知とは別')),
'activity':cards('変更の通知：操作名・結果・対象IDで評価',('含める例','VNet・NSG・FW|指定のルートテーブル'),('範囲外の例','別宣言のACA用RT|PE・Private DNS'),('結果も確認','Succeeded / Failed|失敗＝反映済みではない')),
'flag':cards('enableLogAlerts の切替範囲',('公開設定','本番 false / テスト true|3本のKQL通知を切替'),('別設定','診断ログ・メトリック|Activity通知は継続'),('誤解を確認','FW Deny全件通知を|有効にするフラグではない')),
'quiz1':{'layout':'quiz','heading':'確認①：PE通信のFWログがない','bullets':['APIM → Foundry PE / TCP 443','FWログがないのでFW Allowを足す？','DNS・実効経路・両側NSGを確認']},
'quiz2':{'layout':'quiz','heading':'確認②：Firewall Allowの記録がある','bullets':['同じ通信のAllowを見つけた','APIの認証・処理も成功した？','FWでの許可と処理結果を分ける']},
'quiz3':{'layout':'quiz','heading':'確認③：Denyが記録されたがメールなし','bullets':['FW Deny全件を通知する定義はある？','ログ保存と通知条件を別々に確認','enableLogAlerts=false の範囲は？']},
'summary':{'layout':'summary','heading':'調べ方を一つの通信で説明できる','bullets':['時刻・通信元・宛先・ポートを固定する','DNS → 実効経路 → NSG → 経由FWのログ','ログ不在・Allow・通知を読み違えない','次回：基盤管理者の接続と作業実行']}
}
# One question per screen; avoid the shared quiz renderer numbering every bullet.
V['quiz1']=cards('確認①：PE通信のFWログがない',('条件','APIM → Foundry PE|TCP 443 / FWログなし'),('問い','FW Allowを足せば|解決すると言える？'),('判断','まず直接経路か確認|DNS・経路・両側NSG'))
V['quiz2']=cards('確認②：Firewall Allowの記録がある',('条件','同じ通信について|FW Allowを確認'),('問い','APIの認証・処理も|成功したと言える？'),('判断','FWでの許可と|API処理結果は別'))
V['quiz3']=cards('確認③：Denyが記録されたがメールなし',('条件','FW Denyの保存あり|通知メールはなし'),('問い','enableLogAlerts=false|だけが原因と言える？'),('判断','Deny全件通知はない|通知条件・配線を確認'))
chapter_defs=[('opening','今回の問い','同じ通信の証拠をそろえる目的を持つ。',['observability-triage']),('identify','調査する通信を一つに絞る','送信元と時刻、名前・IP・ポートを特定する。',['observability-triage','network-control-map']),('route','DNS・往復経路・NSGを照合する','U1を例に各層の役割と確認条件をそろえる。',['network-control-map','observability-triage']),('logs','Firewallログの読み方と観測範囲','Network/Application、Deny/Allow/不在の意味を区別する。',['observability-logs','observability-triage','network-control-map']),('walkthrough','接続失敗を調べて引き継ぐ','一つの通信の観測から限定的な判断と再確認へ進む。',['observability-triage','network-control-map']),('alerts','記録と通知を分けて確認する','保存先・メトリック・Activity・KQLアラートの範囲を区別する。',['observability-logs','observability-alerts']),('check','三つの判断を確かめる','ログ不在・Allow・通知不在からの早合点を避ける。',['observability-triage','observability-alerts','network-control-map']),('summary','まとめと次回の問い','調査の順序を振り返り第8回へつなぐ。',['observability-triage','observability-alerts'])]
# chapter | speaker | visual | display caption (spoken product names below)
rows='''
opening|mako|opening|APIがつながりません。まずFirewallの拒否ログを探せばよいのでしょうか。
opening|manabu|opening|焦りますよね。でも、別の通信のログを見ていたら、判断もずれてしまいます。
opening|ikumi|opening|まず、いつ、どこから、どこへ接続したかをそろえましょう。Firewallを通らない経路もあります。
opening|mako|opening|なるほど。同じ通信を追うために、名前解決から順に証拠を集めるんですね。
opening|manabu|opening|調べた事実と、まだ分からないことを分けて、次の確認につなげましょう。
opening|ikumi|opening|ログを保存する設定と、メールを通知する設定の違いも、最後に確かめます。
identify|mako|goal|今回のゴールは、DNS、実効経路、NSG、Firewallログを、一つの通信について照合することです。調査の出発点は何でしょう。
identify|ikumi|path|AppGWからAPIMへ作られる、バックエンド接続を主な例にします。標準のHTTPではTCPの80番で、間にFirewallを通ります。
identify|manabu|path|利用者から公開入口への接続と、AppGWから先への接続を分けるのが大切です。失敗している区間が違えば、見る条件も変わります。
identify|mako|keys|利用者は同じAPIを呼んでいても、ネットワークでは別の接続になるんですね。手元には、どの情報を残しておきますか。
identify|ikumi|keys|失敗した時刻と時間帯、通信元の区画とIP、宛先の名前と解決後のIP、それにプロトコルとポートです。再現時の症状も添えます。
identify|manabu|keys|担当者に渡すときも、この条件を一緒に伝えます。つながらない、という一言だけでは、相手が同じ現象を探せないからです。
identify|mako|keys|ログの時刻と、問い合わせた人が言う時刻が少し違う場合もありそうです。時刻の表記も、そろえる必要がありますね。
identify|ikumi|keys|はい。タイムゾーンと検索する時間幅を確認します。対象のFirewallやLAWが違っていないかも、時刻と一緒に確かめましょう。
identify|mako|path|AppGWの先を調べるのに、元の利用者のIPだけでログを探してしまうと、見落とすことがあるのでしょうか。
identify|ikumi|path|あります。AppGWから新しい接続を作るためです。ヘッダーに残る利用者情報と、NSGやFirewallが見る送信元IPは区別します。
identify|manabu|goal|まず通信を一つに絞る。その条件を、名前解決、経路、通信制御、ログへ持ち回る。この順序が、調査の軸になります。
route|mako|dns|では名前解決です。手元のパソコンからAPIMの名前を引いて、IPが返ってくれば、DNSは正常と言えますか。
route|ikumi|dns|それだけでは言えません。接続に失敗した送信元が、どのDNSを使い、どの回答を得るかを確認します。端末とAppGWでは条件が違います。
route|manabu|dns|別の場所で成功した結果は参考になりますが、今回失敗した接続の証拠を置き換えません。確認した場所も、結果と一緒に残すんですね。
route|ikumi|dns|この例では、APIMの内部IPへ解決する設計です。参照先のDNS、ゾーンのリンクや転送の関係と、得られた回答を照合します。
route|mako|route|内部IPが返ったら、次は経路ですね。ルートテーブルにFirewallという名前があれば、そこを通ると考えてよいですか。
route|ikumi|route|名前や既定経路だけでは決まりません。解決後の宛先IPについて、関連付けたUDRや、より具体的な経路を照合し、次ホップを確認します。
route|ikumi|route|AppGW側のAPIM宛ての経路は、Firewallを次ホップにします。さらにAPIMからAppGWへ戻る経路も、同じFirewallへ向く設計です。
route|mako|route|行きが届くかだけではなく、応答の戻り道まで見るんですね。戻りだけ別の経路なら、接続が成立しない原因になります。
route|ikumi|route|はい。実環境では、資源の種類に応じて取得できる実効経路情報と、設定の関連付けを確かめます。設定例だけを実測結果にはしません。
route|manabu|route|UDRが決めるのは次の行き先です。経路がFirewallへ向くことと、通信が許可されることは、別の確認になります。
route|mako|nsg|その許可を見るのがNSGですね。この通信なら、AppGWからの送信だけ確認すれば足りますか。
route|ikumi|nsg-rules|APIM側の受信も確認します。AppGW送信のallow-apim-httpと、APIM受信のallow-agw-apiを、同じTCPの80番で照合します。
route|ikumi|nsg|送受信の向き、送信元と宛先、ポート、優先順位を確認します。この内部中継の標準設定では、AppGWのprivate IPが保持されます。
route|manabu|nsg|片方の許可だけを見て、全区間が通ると決めないことです。NSGは区画の通信制御、UDRは経路、Firewallは到達した通信の判定を担当します。
logs|mako|logtypes|経路とNSGの条件を確かめたら、Firewallのログへ進みます。NetworkとApplication、どちらから探しましょう。
logs|ikumi|logtypes|主例の内部中継はNetworkルールなので、AZFWNetworkRuleを見ます。IP、ポート、プロトコルと判定を、先ほどの条件に合わせます。
logs|mako|logtypes|APIMから公開Webサービスへの接続を調べる場合は、Application側のログも候補になるんですね。
logs|ikumi|logtypes|そうです。Web宛先の判定はAZFWApplicationRuleで、FQDNなどを照合します。ただしNetworkルールで一致した通信は、Applicationへ進みません。
logs|manabu|logtypes|ログの種類も、調べている通信と評価順に合わせて選びます。Application側に記録がないだけで、通信全体がなかったとは言えないんですね。
logs|ikumi|logkeys|まず対象FirewallのResource IDと時刻を絞り、送信元、宛先、ポートを照合します。その上でActionを見て、AllowかDenyかを確認します。
logs|mako|logkeys|Denyが見つかったら、その名前の拒否ルールを探す、という進め方でよいでしょうか。
logs|ikumi|deny|名前付きの拒否ルールだけとは限りません。許可に一致せず、既定動作で拒否される場合もあります。NetworkログのActionReasonも確認します。
logs|ikumi|deny|そこにDefault Actionと記録される場合があります。Rule Collection Group、Collection、Ruleの情報も、ポリシー定義と対応させて見ます。
logs|manabu|deny|拒否の証拠が見つかった地点から、適用条件に戻るわけですね。調べる前に許可範囲を広げるより、変更すべき条件を絞れます。
logs|mako|allow|では反対に、同じ通信のAllowログがありました。これでAPIが正常に動くことまで確認できた、と言えますか。
logs|ikumi|allow|言えません。Allowが示すのは、そのFirewallでの許可です。先の受信制御や応答経路、接続先の認証、アプリの処理結果は、別に確かめます。
logs|mako|nolog|AllowもDenyも見つからなかったら、通信がFirewallより前で止まった、と判断できるのでしょうか。
logs|ikumi|nolog|それも断定できません。時刻や対象資源、検索先、ログの種類、診断設定や取り込みの状態を見直し、その通信がFirewallへ届く経路かを確認します。
logs|ikumi|direct|たとえば、APIMからFoundryのPEへ向かうTCPの443番は、標準では具体的な直接経路を使います。この通信をFirewallは判定しません。
logs|manabu|direct|この場合は、DNSの回答、実効経路、APIM送信とPE受信のNSG、接続先のログを調べます。FirewallにAllowを追加すれば直る、とは決めません。
walkthrough|mako|walk|ここから短い演習です。AppGWからAPIMへのバックエンド接続が失敗した、と仮定します。実環境で起きた障害の報告ではありません。
walkthrough|ikumi|keys|まず失敗時刻、AppGWからの送信元、APIMの内部IP、TCPの80番を調査票にそろえます。同じ時間帯の別の宛先を混ぜないようにします。
walkthrough|mako|walk|この演習では、送信元のDNS回答と往復経路が設計どおりで、両側NSGも条件上は許可だと分かりました。次にFWのログを探します。
walkthrough|ikumi|deny|同じ通信についてDenyとDefault Actionが見つかった、とします。次は、適用されたポリシーと内部中継の許可条件が、想定と一致するかを調べます。
walkthrough|manabu|record|ここまでで言えるのは、この試行をFirewallが拒否したことです。どの設定変更が必要かは、送信元や宛先、ポートの照合を続けて判断します。
walkthrough|mako|nsg-rules|設計上のagw-to-apim-httpがあるはずなのに一致しないなら、名前の存在だけでなく、適用先や実際の条件を見るわけですね。
walkthrough|ikumi|record|はい。IaCの定義、配備先の設定、観測した通信を照合します。間違った送信元で検索していないか、別のポリシーを見ていないかも確認します。
walkthrough|manabu|record|変更するなら、理由と元の設定を残し、必要な範囲を限定します。変更後には同じ条件の新規接続で、症状と記録を確かめ直します。
walkthrough|mako|allow|別の分岐も考えたいです。同じ条件のAllowがあるのに失敗している場合、次にどこへ調査を進めましょう。
walkthrough|ikumi|allow|受信側の状態や復路、APIM側の記録を確認します。公開入口からの要求なら、AppGWアクセスログの時刻、status、バックエンドなども対応させます。
walkthrough|ikumi|scope|AppGWのTransactionId等も手掛かりです。ただしWAFの判定ログとアクセスログは別です。認証や要求処理の詳細は、第13回で扱います。
walkthrough|manabu|record|引き継ぎには、観測した事実、判断できる範囲、未確認の層、次の確認先を残します。設定したことと、疎通を観測したことも分けて書きます。
alerts|mako|stores|ここまではログを探す側の話でした。そもそもFirewallの通信ログは、どこへ送る定義になっていますか。
alerts|ikumi|stores|NetworkとApplicationの判定は、LAWへDedicated形式で保存します。監査収集を有効にした構成では、日本側の監査Storageにも送ります。
alerts|manabu|stores|ただ、保存先が二つあることと、二つとも実際に届いたことは別ですね。調査時には、診断設定と到着状況まで確かめる必要があります。
alerts|ikumi|scope|そのとおりです。また、現行infraにはVNetやNSGのFlow Logs、Packet Captureを作る定義はありません。全VNet通信が記録される構成ではありません。
alerts|mako|scope|PEへの直接通信も含めて、何でもFirewallログで追えるわけではないんですね。メトリックとActivity Logは、何を見る記録ですか。
alerts|ikumi|scope|メトリックは状態を表す数値、Activity Logは管理操作や基盤状態の記録です。どの通信を許可したかという通信ログとは、調べる問いが違います。
alerts|mako|alerts|では、FirewallのDenyが一件出たら、運用担当者へすぐメールが届くのでしょうか。ログ保存と通知はつながっていますか。
alerts|ikumi|alerts|現在、Deny全件を自動通知する定義ではありません。通知には、アラートの対象と評価条件、Action Groupへの配線を、別に確認します。
alerts|ikumi|metrics|たとえばAppGWのUnhealthyHostCountは平均がゼロを超えた場合、FirewallHealthは平均が100を下回った場合です。どちらも5分窓で、毎分評価します。
alerts|manabu|metrics|接続失敗が起きたその瞬間の通知とは限らず、決めた窓と条件で状態を評価するんですね。拒否ログの件数をそのまま通知する例でもありません。
alerts|ikumi|alerts|通知先は運用者のメールアドレスを登録したAction Groupです。このAction GroupにはWebhookや、自動復旧や撤去をする処理は登録していません。
alerts|mako|activity|ネットワーク設定が変わったときにも通知がありますね。それなら、DNSやPEの変更まで、全部を知らせてくれるのでしょうか。
alerts|ikumi|activity|全部ではありません。操作名、結果、対象IDを照合します。別宣言のACA用ルートテーブル、PE、Private DNSゾーンは、この変更通知の対象一覧に入りません。
alerts|manabu|activity|Activity Logの収集範囲と、個別の通知対象は違うわけですね。操作が記録されてもメールが出るとは限らず、Failedを設定反映済みとも考えません。
alerts|mako|flag|本番の公開設定でenableLogAlertsがfalseなら、ログ収集や、今のメトリック通知も止まってしまいますか。
alerts|ikumi|flag|止まりません。このフラグは、監査Blob書込失敗、取り込み量、API失敗率の三つのKQLアラートを切り替えます。診断ログやメトリック、Activity通知とは別です。
alerts|manabu|flag|本番の例はfalse、テストの例はtrueですが、FWのDeny全件通知を有効にするスイッチではありません。記録、評価、通知先を分けて確認しましょう。
check|mako|quiz1|ここで三つ確認です。APIMからFoundryのPEへ接続できず、Firewallログもありません。まずFirewallの許可を追加する、でよいでしょうか。
check|ikumi|quiz1|まず直接経路かを確認します。この標準経路ならFWを通りません。DNS、実効経路、送受信のNSGとサービス側の記録を、同じ通信で照合します。
check|manabu|quiz1|ログがない、という観測だけで原因を決めないことですね。何を通る通信なのかが分かると、調べる場所を選べます。
check|mako|quiz2|二つ目です。FWのAllowを確認できました。これで利用者のAPI要求も認証され、正常に処理されたと判断できますか。
check|ikumi|quiz2|判断できません。FWでの許可と、APIの処理結果を分けます。先の接続先や復路、認証や認可まで、必要な記録を追加して確かめます。
check|manabu|quiz2|確認できた層を言葉にするのが大切ですね。全体が正常、とまとめる前に、何の成功を観測したのかを確かめます。
check|mako|quiz3|最後です。FWのDenyが保存されていますが、メールは来ていません。本番のenableLogAlertsがfalseなのが原因だ、と決めてよいですか。
check|ikumi|quiz3|決められません。現在の定義はDeny全件の通知ではありません。診断ログの保存とは別に、対象のアラート条件、評価結果、通知先を確認します。
check|manabu|quiz3|通知されなかった理由も、設定と条件から調べるんですね。保存済みのログ、通知する条件、通知先までを分ければ、説明の飛躍を防げます。
summary|mako|summary|今日は、つながらないときに、同じ通信を追う調べ方を学びました。最初にそろえるのは、時刻、通信元、宛先、プロトコルとポートです。
summary|ikumi|summary|DNSの回答を得たら、宛先IPの実効経路と往復を確認し、NSGを照合します。FW経由なら、評価された種類のログと適用ルールへ進みます。
summary|manabu|summary|ログ不在だけで遮断と決めず、Allowだけで全体の成功と決めないこと。調べた事実と残った確認を記録すれば、次の担当者にもつながります。
summary|mako|summary|そして、ログを残す設定と通知の条件は別でした。設定例の説明と、実際に通信やメールを確認した結果も、区別して扱います。
summary|ikumi|summary|次回は、基盤管理者の接続と作業実行です。管理VMへどうprivate接続し、NSG、SSH認証、Managed Run Commandが何を分担するかを見ていきます。
summary|mako|summary|調べるために必要な接続も、経路と認証から整理していくんですね。それでは、第8回でお会いしましょう。
'''.strip().splitlines()
replacements=[('Managed Run Command','マネージド ラン コマンド'),('Rule Collection Group','ルール コレクション グループ'),('TransactionId','トランザクション アイディー'),('AZFWApplicationRule','エーゼット エフダブリュー アプリケーション ルール'),('AZFWNetworkRule','エーゼット エフダブリュー ネットワーク ルール'),('UnhealthyHostCount','アンヘルシー ホスト カウント'),('FirewallHealth','ファイアウォール ヘルス'),('enableLogAlerts','イネーブル ログ アラーツ'),('allow-apim-http','アラウ エーピーアイエム エイチティーティーピー'),('allow-agw-api','アラウ エージーダブリュー エーピーアイ'),('agw-to-apim-http','エージーダブリュー トゥー エーピーアイエム エイチティーティーピー'),('Default Action','デフォルト アクション'),('Resource ID','リソース アイディー'),('ActionReason','アクション リーズン'),('Action Group','アクション グループ'),('Private DNS','プライベート ディーエヌエス'),('Packet Capture','パケット キャプチャー'),('Flow Logs','フロー ログ'),('Dedicated','デディケイテッド'),('Application','アプリケーション'),('Collection','コレクション'),('Succeeded','サクシーディッド'),('Network','ネットワーク'),('Firewall','ファイアウォール'),('AppGW','アップゲートウェイ'),('Foundry','ファウンドリー'),('Activity Log','アクティビティ ログ'),('Activity','アクティビティ'),('Storage','ストレージ'),('Protocol','プロトコル'),('Action','アクション'),('private','プライベート'),('Webhook','ウェブフック'),('Failed','フェイルド'),('status','ステータス'),('Allow','アラウ'),('Deny','ディナイ'),('Rule','ルール'),('infra','インフラ'),('IaC','アイエーシー'),('VM','ブイエム'),('HTTP','エイチティーティーピー'),('APIM','エーピーアイエム'),('API','エーピーアイ'),('NSG','エヌエスジー'),('DNS','ディーエヌエス'),('UDR','ユーディーアール'),('TCP','ティーシーピー'),('LAW','ログ アナリティクス ワークスペース'),('KQL','ケーキューエル'),('Blob','ブロブ'),('VNet','ブイネット'),('SSH','エスエスエイチ'),('PE','ピーイー'),('IP','アイピー'),('FW','ファイアウォール'),('ACA','エーシーエー'),('FQDN','エフキューディーエヌ')]
def speech(c):
 for a,b in replacements:c=c.replace(a,b)
 return c
# Caption chunks preserve all words and use short semantic phrases.
def caption_chunks(c):
 out=[]
 for sentence in re.findall(r'.+?(?:[。？！]|$)',c):
  if len(sentence)<=38:
   ll=[sentence]
  else:
   tokens=re.findall(r'[A-Za-z0-9][A-Za-z0-9_\-./]*|.',sentence)
   boundaries=[];length=0
   for token in tokens[:-1]:
    length+=len(token)
    if length<=38 and len(sentence)-length<=38 and sentence[length] not in '、。？！ーゃゅょっ':
     score=abs(length-len(sentence)/2)-(6 if sentence[length-1]=='、' else 0)
     boundaries.append((score,length))
   if not boundaries:raise ValueError('No safe subtitle split: '+sentence)
   split=min(boundaries)[1];ll=[sentence[:split],sentence[split:]]
  out.append({'caption':sentence,'lines':ll,'weight':len(sentence)})
 return out
turns=[]
for idx,row in enumerate(rows,1):
 chapter,speaker,v,caption=row.split('|',3)
 chunks=caption_chunks(caption)
 delivery={'mako':'疑問を相手に確かめる。説明を受けた反応は短く自然に返す。','manabu':'実務での判断の意味を、相手と整理するように落ち着いて返す。','ikumi':'通信の条件と、そこから言える範囲を明瞭に区切って説明する。'}[speaker]
 t={'id':f'lesson07-turn-{idx:03}-{speaker}','speaker':speaker,'chapterId':chapter,'speech':speech(caption),'caption':caption,'subtitleLines':chunks[0]['lines'],'subtitleChunks':chunks,'gapAfterSeconds':.14,'delivery':delivery,'visual':V[v]}
 if idx in (80,83):t['id']=f'lesson07-turn-{idx:03}r2-{speaker}'
 turns.append(t)
ref=lambda a:{'file':'../01-network-control.html','anchor':a}
s.update(status='ready-for-generation',description='同じ通信のDNS・往復経路・NSG・Firewallログを照合し、ログ保存と通知条件を区別する。',seriesLabel='NETWORK TROUBLESHOOTING',sourceReferences=[ref(a) for a in ['observability-logs','observability-triage','observability-alerts','network-control-map']],chapters=[{'id':i,'title':t,'goal':g,'sourceRefs':[ref(a) for a in aa]} for i,t,g,aa in chapter_defs],turns=turns)
s['firstCut']={'omitTurnIds':['lesson07-turn-079-manabu','lesson07-turn-082-manabu'],'sourceRecordingsKept':True,'reason':'確認①②の回答後に重複する振り返りを省く。続く質問は文脈変更のため新IDで再収録する。'}
S.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(turns),'speechCharacters':sum(len(t['speech']) for t in turns),'captionCharacters':sum(len(t['caption']) for t in turns),'openingSpeechCharacters':sum(len(t['speech']) for t in turns if t['chapterId']=='opening')},ensure_ascii=False))
