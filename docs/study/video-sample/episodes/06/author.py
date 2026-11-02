from pathlib import Path
import json,re,copy
HERE=Path(__file__).resolve().parent
S=json.loads((HERE/'script.json').read_text())
def cards(heading,*items):
 return dict(layout='cards',heading=heading,cards=[dict(title=t,body=b) for t,b in items])
def node(id,label,detail,x,y=168,w=285,h=112,color='#80dce3'):
 return dict(id=id,label=label,detail=detail,x=x,y=y,width=w,height=h,color=color)
def flow(heading,labels,focus=[]):
 nodes=[node(id,label,detail,35+i*405) for i,(id,label,detail) in enumerate(labels)]
 return dict(layout='diagram',heading=heading,nodes=nodes,edges=[dict(**{'from':nodes[i]['id'],'to':nodes[i+1]['id']}) for i in range(len(nodes)-1)],focus=focus)
V={
'opening':dict(layout='hologram',heading='外向き通信と出口IP'),
'goal':cards('外へつながる通信を、三つの視点で追う',('宛先の許可',['誰が・どのWeb依存先へ','接続してよいか']),('出口の経路',['Firewall経由か','PEへの直接経路か']),('相手から見えるIP',['構成上の期待値と','実際の観測を分ける'])),
'web':flow('Web依存先は、送信元と用途をそろえて許可する',[('source','APIM・管理VM','今回の送信元'),('fw','Firewall','Applicationの許可'),('dest','Web依存先','証明書・更新・外部API')]),
'web-detail':cards('Applicationルールの照合条件',('送信元',['APIM区画 / 管理VM区画','ACA区画などを区別']),('通信とホスト名',['HTTP 80 / HTTPS 443','設定されたFQDNと照合']),('前提',['Firewallを通る経路','Networkで未一致のWeb通信'])),
'base':cards('APIMと管理VMで、必要なWeb通信が違う',('APIM',['証明書・失効情報','管理基盤の更新','診断・プロファイル等']),('管理VM',['証明書の検証先','Ubuntu等のパッケージ','承認した管理用サービス'])),
'wild':cards('ホスト名の許可範囲も確認する',('固定ホスト',['packages.microsoft.com','特定のホストを許可']),('ワイルドカード',['*.windowsupdate.com','固定ホスト1個と範囲が違う'])),
'portal':cards('実行する場所から、Web依存先を整理する',('ACA実行基盤',['起動・認証の依存先','api.ipify.org：出口確認']),('管理VMでのビルド',['GitHub / npm / Docker','Python・OSパッケージ']),('PortalからのAPI試用',['ACA → 公開Gateway','gatewayOriginのホスト'])),
'public-gateway':flow('Portalから公開Gatewayへ、新しいサーバー間通信を作る',[('aca','Portal / ACA','サーバーからの要求'),('fw','Firewall','外向き既定経路'),('agw','公開Gateway','選択環境のホスト1個')]),
'generated':flow('入力JSONだけで、最終的な許可先を決めない',[('input','入力JSON','許可先配列が空でも'),('generate','設定生成','選択した設定から追加'),('azure','Azure上のルール','配備結果と照合')]),
'cert':cards('証明書の設定から、管理VMの許可先が増える',('CAの選択',['ACME production / staging','選択したCAのホスト']),('DNS事業者の選択',['Cloudflare / Route53 / Azure','選択したDNS API']),('管理・検証',['PowerShell配布先','公開Gateway・Portalのホスト'])),
'api-add':flow('API公開で、外部バックエンドのホストを追加する',[('api','API・Operation','外部HTTPS / WSS'),('extract','ホストを抽出','TLS 443・明示ホスト'),('allow','APIMの許可先','既存宛先を保持して追加')]),
'ownership':cards('許可先を足す処理と、旧許可を消す処理を分ける',('API公開',['未登録ホストを追加','既存ルール・宛先を保持']),('削除・変更後',['旧FQDNは自動削除しない','残存する許可先も確認']),('次回の基盤配備',['所有・送信元・HTTPS境界','検査したAPIM許可を継承'])),
'additional':cards('追加ルールは、種類と担当範囲を確認する',('Network追加',['広い許可に一致すると','Applicationへ進まない']),('Application追加',['必要なWeb依存先を追加','送信元・宛先を絞る']),('Portalの配備',['専用Gatewayグループを更新','基盤グループと区別'])),
'explicit':cards('暗黙の出口に依存せず、構成を明示する',('暗黙の出口',['defaultOutboundAccess:false','UDRやDenyとは別の設定']),('経路の選択',['APIM・管理VM・ACA','0.0.0.0/0 → Firewall']),('公開送信元',['Firewallの公開IP','Standard / IPv4 / Static'])),
'egress':flow('公開宛ての標準経路：管理VM → Firewall → 外部サービス',[('vm','管理VM','Public IPなし'),('fw','Firewall','許可と送信元変換'),('public','公開依存先','Firewall公開IPが見える')]),
'ip-resources':cards('入口の公開IPと、出口の公開IPを分ける',('利用者の入口',['AppGWの公開IP','公開フロントエンド']),('外部への出口',['Firewallの公開IP','外部接続先に見える送信元'])),
'paths':cards('公開宛ての既定経路と、PEへの経路を分ける',('公開依存先',['APIM・管理VM・ACA','既定UDRでFirewallへ']),('PEへの接続',['具体的な直接経路','Firewallを通らない']),('資料の範囲',['NAT Gateway定義なし','実環境の追加資源は未調査'])),
'snat':flow('SNAT：外部へ出る接続の送信元が変わる',[('source','送信元のprivate IP','APIM・管理VM・ACA'),('fw','Firewall','SNATで送信元を変換'),('external','外部接続先','Firewallの公開IPを観測')]),
'private':flow('Networkのprivate宛て：AppGWのprivate IPを保持する',[('agw','AppGW','新しい内部接続'),('fw','Firewall / Network','private宛てを非SNAT'),('apim','APIM・ACA','送信元はAppGW private IP')]),
'private-ranges':cards('snat.privateRanges が指定するもの',('Networkの非SNAT宛先',['公開例：IANAPrivateRanges','private宛ての送信元を保持']),('別に確認するもの',['通信をAllowする規則','IDPSの方向分類用の範囲'])),
'app-snat':cards('Applicationへ、Networkの非SNAT設定を当てはめない',('Networkルール',['snat.privateRangesで','非SNATの宛先を指定']),('Applicationルール',['透明プロキシを通す','宛先がprivateでもSNAT']),('現在の標準設定',['Applicationの許可先は','公開FQDN向け'])),
'pe':flow('PEへの直接接続では、FirewallのSNATは起きない',[('apim','APIM等','対象の通信元'),('pe','PE','具体的な直接経路'),('service','閉域サービス','Firewallを経由しない')]),
'network-source':cards('送信元は、制御箇所ごとに確認する',('端末 → 公開入口',['端末から始まる接続','利用者の情報を持つ']),('Portal → 公開入口',['ACAから新しい接続','標準経路ではFW公開IP']),('AppGW → 内部入口',['AppGWから新しい接続','HTTPヘッダーとNSGを区別'])),
'expected':cards('期待値の受け渡しと、確認先の許可は別の設定',('期待値を受け渡す',['infra出力：expectedEgressIp','↓ Portal配備','環境変数：EXPECTED_EGRESS_IP']),('確認先の通信許可',['api.ipify.org','自動実測・照合処理はない'])),
'not-measured':cards('設定があることは、測定済みであることを示さない',('用意されているもの',['Firewall公開IPの期待値','出口確認先への通信許可']),('現在のPortalコード',['環境変数による出口IPの','自動実測・照合処理はない'])),
'measure':cards('実測は、対象の実行場所・経路・時刻をそろえる',('期待値を取得',['対象環境の出力を確認','Firewallの公開IP']),('対象から測定',['Portalの実行場所等','許可された確認先へ接続']),('結果を比較',['相手から見える送信元','実行場所・時刻と記録'])),
'compare':cards('出口IPが違ったとき、同じ通信かを確かめる',('比較の条件',['同じ環境・実行場所か','公開宛ての対象経路か']),('経路・資源',['実効経路とFirewall','別の出口資源の追加有無']),('確認結果の限界',['確認先1件の観測','全宛先の出口とは限らない'])),
'q1':cards('確認1：入力の許可先が空なら、外向き通信はゼロ？',('条件と問い',['公開params例の apimAllowedFqdns が空。','設定生成・API公開後の許可先も空と判断できる？'])),
'q2':cards('確認2：privateRanges に入れれば、通信は許可される？',('条件と問い',['Networkの snat.privateRanges に宛先が入っている。','Allow規則や経路の確認を省略できる？'])),
'q3':cards('確認3：期待値と確認先があれば、出口IPは実測済み？',('条件と問い',['EXPECTED_EGRESS_IP が設定されている。','api.ipify.org が許可されていれば、自動照合済み？'])),
'summary':dict(layout='summary',heading='宛先・経路・送信元・実測を、一つの通信で説明する',bullets=['Web依存先：送信元・用途・FQDNをそろえて許可','許可先一覧：入力・設定生成・API公開・配備を照合','SNAT：経路とルール種別から、見える送信元を確認','出口IP：期待値を取得し、対象の実行場所から実測']),
'next':cards('第7回：つながらないときの調べ方',('今回の判断材料',['宛先・経路・見える送信元','期待値と実測の区別']),('次に照合するもの',['DNS / 実効経路 / NSG','Firewallログと同じ通信']))
}
chapters=[
('opening','今回の問い','許可先と出口IPが外部サービス接続の条件になると理解する。',['fw-base-applications','egress-explicit']),
('purpose','外向き通信の見る順番','宛先・経路・送信元を同じ通信について確認する。',['fw-base-applications','egress-explicit']),
('web','Web依存先を許可する','送信元と用途でAPIM・管理VM・ACAのWeb依存先を分ける。',['fw-base-applications','fw-portal-applications']),
('generated','設定から生成されるFQDN','入力JSONと設定生成・API公開・配備後の許可先を区別する。',['fw-generated-settings']),
('egress','公開IPを持たないVMの出口','暗黙の出口無効化・経路・許可・公開IPを区別する。',['egress-explicit']),
('snat','接続先から見える送信元','Network・Application・PE直接接続のSNATの違いを説明する。',['egress-snat']),
('measure','期待値と実測を分ける','期待値の受け渡しと自動実測の未実装を区別し、測定条件を整理する。',['egress-snat']),
('check','三つの問いで確かめる','生成許可先・非SNAT設定・実測済みの三つの誤解を解く。',['fw-generated-settings','egress-snat']),
('summary','まとめと次の問い','同じ通信の宛先・経路・見える送信元を説明し、次回の調査へつなぐ。',['fw-base-applications','egress-explicit','egress-snat'])]
rows={
'opening':'''mako|opening|外部APIの接続先から「許可する送信元IPを教えて」と言われました。Portalの利用者のIPを伝えるんですか。
manabu|opening|実際に外へ接続するのが誰かで変わります。宛先の許可と、相手から見える出口をそろえて考えましょう。
ikumi|opening|今回はWeb依存先、設定から増えるホスト名、SNATを追い、出口IPの期待値と実測を分けて確認します。
mako|opening|送ってよい相手と、こちらが何者に見えるか。一本の通信で見ていくんですね。''',
'purpose':'''manabu|goal|外部APIだけでなく、証明書の検証やパッケージ取得も外向き通信です。依存先へ届かなければ、アプリの起動や更新で困ります。
mako|goal|必要だからとインターネット全体を許可すると、接続の用途が分からなくなりそうです。
ikumi|goal|はい。まず、どの区画が、何のために、どのホストへ接続するかをそろえます。次に経路と、相手に見える送信元を確認します。
manabu|goal|説明は教材とリポジトリ定義、公開設定例に基づきます。稼働中のAzureから、実際の出口IPを取得した結果ではありません。
mako|goal|では許可の設定から始め、構成から期待できることと、測って分かることを分けましょう。''',
'web':'''ikumi|web|Applicationルールは、送信元とWebのプロトコル、ポート、ホスト名を組み合わせます。Firewallへ到達する経路が前提です。
mako|web-detail|HTTPSの443番を使っていれば、どのホストにも接続できる、という許可ではないんですね。
ikumi|web-detail|そうです。設定されたFQDN、つまり完全なホスト名の条件も確認します。ただしNetworkルールに一致した通信は、Applicationでもう一度許可を求めません。
manabu|base|送信元ごとに用途を見ると、許可の理由が分かります。APIMには証明書チェーンや失効情報の取得先、管理基盤の更新先が必要です。
mako|base|資料にWindows更新先がありますが、管理VMもWindowsにする、という意味ですか。
ikumi|base|いいえ。ここはAPIM管理基盤の依存先です。管理VMには別の規則があり、証明書の検証先と、UbuntuやMicrosoftのパッケージ配布先を許可します。
manabu|base|同じ証明書や更新でも、誰のための通信かを分ける。不要な許可を別の送信元へ広げずに済みます。
ikumi|wild|ホスト名の書き方も確認します。固定ホスト一つの指定と、アスタリスクを含むワイルドカードでは、対象が同じとは限りません。変更時は範囲も読みます。
mako|portal|Portalやビルドは、APIの実行先だけを許可すれば足りますか。
ikumi|portal|ACAには起動・実行基盤や認証の依存先があります。管理VMのビルドにはGitHub、npm、Docker、PythonやOSのパッケージ取得先があります。
manabu|portal|実行中のアプリと、アプリを作って配備する工程は依存先が違います。ACRのPEへの通信を、この公開Web許可で通す説明ではありません。
ikumi|public-gateway|Portalサーバーから公開Gatewayを試す通信もあります。選択環境のgatewayOriginからホスト一つを取り出して許可し、ACAの外向き既定経路でFirewallを通ります。
mako|public-gateway|利用者のブラウザーと、Portalサーバーからの通信は別。送信元をACAとして読むんですね。''',
'generated':'''mako|generated|公開設定例では、APIM用や管理VM用の許可FQDN配列が空です。承認済みの外部接続先はないと読めますか。
ikumi|generated|入力JSONだけでは決められません。証明書などの設定からホストが補われ、APIの公開工程でも追加されます。入力と生成後と配備後を対応付けて読みます。
manabu|generated|設定例は、選択した機能を組み立てる前の入口でもあるわけですね。空配列を、完成した許可一覧だと思わないようにします。
ikumi|cert|証明書のDNSプラグイン方式では、選択したACMEのCA、DNS事業者のAPI、PowerShell配布先などが管理VMの許可先へ加わります。
mako|cert|DNS事業者を変えても、全部の事業者APIを一括で開けるわけではないんですね。
ikumi|cert|はい。CloudflareならそのAPI、Route53ならそのAPI、Azureなら管理APIという具合です。CAもproductionとstagingで選択が変わります。
manabu|cert|公開GatewayやPortalのホストも、配備後の入口確認のために追加されます。用途までたどれると、保守がしやすいですね。
mako|api-add|新しく外部APIを公開するときは、どの部分を許可先へ反映しますか。URLのパスまで見るんでしょうか。
ikumi|api-add|対象はAPI本体やOperationで上書きした、外部HTTPSまたはWSSバックエンドのDNSホストです。TLSの443番で、明示されたホストを追加する範囲です。
ikumi|api-add|本人接続に必要なAzure管理APIや、ProviderのidentityEndpointのホストも反映します。ホストの許可と、パスや本人認証の判断は別です。
mako|ownership|では、APIの接続先を変更したら、古いホストの許可は自動で消えるんですか。
ikumi|ownership|API公開は追加方式です。未登録ホストを足し、既存ルールと宛先を保持します。APIの削除や変更だけで、旧FQDNは自動削除されません。
manabu|ownership|変更の成功と、古い許可の要否は別の確認ですね。次回の基盤配備も、所有や送信元、HTTPSの境界を検査したAPIM許可を引き継ぎます。
ikumi|additional|追加ルールにも注意します。広いNetwork許可に一致すると、Applicationのホスト条件へ進みません。Portalの配備も専用のGatewayグループを扱い、基盤グループと区別します。''',
'egress':'''mako|explicit|必要なホストを許可できました。次は出口です。管理VMは公開IPを持たないのに、公開依存先へ接続できるんですか。
ikumi|egress|できます。標準構成では、管理VMの既定UDRがFirewallを次ホップにします。許可がそろえば、公開依存先へ出る経路があります。
manabu|explicit|自分の公開IPがなくても、外へ接続する手段はあります。共通の出口を明示し、そこで許可と送信元を管理する構成です。
mako|explicit|サブネットのdefaultOutboundAccessがfalseなのは、外向き通信を全部拒否する設定、ではないんですね。
ikumi|explicit|はい。暗黙の公開出口に依存するのを避ける設定です。UDRの転送先、NSGやFirewallの許可、SNATとはそれぞれ別です。それぞれを確認します。
ikumi|explicit|この属性はAppGW、APIM、PE、管理VM、ACAの五つのサブネットに明示されています。Firewall区画には指定していません。区画ごとに確認します。
mako|ip-resources|相手へ伝える公開IPは、利用者が接続するAppGWのアドレスですか。
ikumi|ip-resources|別です。AppGWの公開IPは入口で、Firewallの公開IPが公開宛ての標準出口です。FirewallにはStandard、IPv4、Staticの公開IPを関連付けています。
manabu|paths|ただし、全部の宛先がその出口を使うとは言えませんね。PEへ向かう通信を、インターネット向けと一緒にしないことが大切です。
ikumi|paths|APIM、管理VM、ACAの既定経路はFirewall向けですが、より具体的なPEやVNet内経路、管理用の例外があります。AppGWにも同じ既定経路を一律追加する説明ではありません。
ikumi|paths|現行BicepにはNAT GatewayやLoad Balancerの外向き規則を作る定義はありません。ただし、実環境に別途追加された資源がないと調査した結果ではありません。''',
'snat':'''mako|snat|では、Firewallを通ると、送信元はどう変わるんですか。ここでSNATが出てきますね。
ikumi|snat|SNATは、通信の送信元IPなどを変換する仕組みです。APIM、管理VM、ACAから公開宛てへ出る標準経路では、Firewallの公開IPに変換されます。
manabu|snat|そのため外部サービスは、管理VMやACAのprivate IPではなく、Firewallの公開IPを送信元として見ます。相手の許可元IPを考える出発点ですね。
mako|private|でも内部のAppGWからAPIMへの通信は、AppGW区画を送信元にしてNSGを許可していました。そこもFirewallのIPに変わりますか。
ikumi|private|Networkルールでprivate宛てへ中継する場合、公開例ではSNATしません。AppGWのprivate IPを保持するので、APIM側NSGがAppGW区画として照合できます。
ikumi|private-ranges|ここで使うsnat.privateRangesは、NetworkルールでSNATしない宛先範囲です。公開例のIANAPrivateRangesという設定を、許可する送信元一覧とは読まないでください。
mako|private-ranges|その範囲にあれば通信をAllowする、という意味でもないんですね。経路と許可は別に要る。
ikumi|private-ranges|そのとおりです。また、同じprivateRangesという名前でも、IDPSの方向分類用とは別です。どの設定内の値かも確かめます。
manabu|private|送信元を保持する設計だから、復路も同じFirewallへ戻す経路と組み合わせる。第4回で見た往復経路が、ここにもつながりますね。
mako|app-snat|Networkでprivate宛てを変換しないなら、Applicationルールでも同じ除外が効きますか。
ikumi|app-snat|そこは異なります。Applicationルールは透明プロキシを通し、宛先がprivateでもSNATする仕様です。Network用の非SNAT設定を、そのまま当てはめません。
ikumi|app-snat|現在の標準Applicationルールは公開FQDN向けです。private宛てのApplication通信が、標準で存在するという意味ではありません。
mako|pe|もう一つ、PEへの直接接続では、Firewallによる送信元の変換を考える必要はありますか。
ikumi|pe|Firewallを通らないので、FirewallによるSNATはありません。対象の送信元とPE側の制御を確認し、Firewall公開IPを送信元にした通信として扱いません。
manabu|network-source|HTTPヘッダーの元利用者情報と、NSGが照合するネットワーク送信元は別です。AppGWは内部へ新しい接続を作るので、制御箇所ごとに見える相手を確認します。''',
'measure':'''mako|expected|出口の仕組みが分かりました。Portalには期待する出口IPを渡す設定がありますよね。それで実測済みと言えますか。
ikumi|expected|まだ言えません。infraはFirewallの公開IPをexpectedEgressIpとして出力し、Portal配備はEXPECTED_EGRESS_IPという環境変数へ渡しています。
ikumi|not-measured|さらに、出口確認用のapi.ipify.orgへの通信許可もあります。ただし、現在のPortalコードには、その環境変数を使う出口IPの自動実測・照合処理はありません。
manabu|not-measured|期待値と確認用の宛先が用意されている。そこまでは設定から説明できますが、測って一致した証拠は別に必要なんですね。
mako|measure|私の端末から確認サイトへアクセスして、その値を期待値と比べればよいですか。
ikumi|measure|端末の出口を測っても、Portalの出口を測ったことにはなりません。対象環境の期待値を取得し、対象の実行場所から確認先へ接続して比較します。
manabu|measure|どの環境のどこから、いつ、どの確認先へ接続したかも記録します。後から同じ条件で説明しやすくなります。
mako|compare|もし違うIPが返ったら、すぐに許可元IPをその値へ書き換えてよいんでしょうか。
ikumi|compare|まず同じ環境と実行場所を比べているか、対象の実効経路と出口資源は何かを確かめます。ハブ集約出口へ変更した場合など、期待する経路自体が変わることもあります。
ikumi|compare|確認先一件の観測を、全宛先の出口へ広げないことも大切です。特にPEの直接経路は別です。確認できた通信の範囲と、未確認の範囲を分けて記録します。
manabu|compare|相手の許可元IPを管理するときは、対象環境の実出力、実際の経路、観測結果をそろえる。構成図の想像でIPの実値を決めないんですね。''',
'check':'''mako|q1|ここで三つ確認します。一つ目。公開params例の許可FQDN配列が空でした。外部接続先の最終許可も空だと判断できますか。
manabu|q1|できません。設定生成やAPI公開でホストが追加されます。入力JSON、生成された設定、Azure上のルールを対応付けて、最終的な許可先を確認します。
ikumi|q1|API公開は追加方式なので、削除したAPIの旧FQDNが残ることにも注意します。追加できたことだけで、不要な許可の整理まで終わったとは判断しません。
mako|q2|二つ目。Networkのsnat.privateRangesに宛先が入っています。これなら通信が許可され、NSGやFirewallのAllowは確認しなくてよいですか。
ikumi|q2|いいえ。これはNetworkでSNATしない宛先範囲です。通信許可ではありません。経路とAllowを別に確認し、Applicationにも同じ除外が効くとは考えません。
manabu|q2|送信元を変えるかと、送ってよいかは違う判断。設定の名前が似ていても、何を決める値なのかを説明するのが大事ですね。
mako|q3|最後です。期待値の環境変数があり、出口確認先も許可されています。Portalの出口IPは自動測定され、一致確認も済んでいますか。
ikumi|q3|いいえ。自動実測・照合処理はありません。対象の実行場所から測り、期待値と比べて初めて、その通信について一致を確認したと言えます。
manabu|q3|三問とも、設定の一部から結果全体を決めつけないことが共通していますね。送信元、宛先、経路、確認した事実をそろえて判断します。''',
'summary':'''mako|summary|今日の流れを振り返ります。まず、Web依存先を送信元と用途で分けて許可する。許可先は入力だけでなく、設定生成やAPI公開による追加まで読みます。
manabu|summary|次に、どの出口を通り、相手からどの送信元に見えるか。入口のAppGW公開IPと、公開宛てのFirewall出口IPを区別し、SNATの設定を確認します。
ikumi|summary|Network、Application、PEへの直接接続で扱いが違います。そして出口IPの期待値を渡す設定と、対象から測定して一致を確認することを分けてください。
mako|next|次回は、つながらない一つの通信について、DNS、実効経路、NSG、Firewallログをどう照合するかを見ていきます。
manabu|next|どこからどこへ、どの経路で、何を確認したか。今日の見方を使って、次は調査の材料をそろえましょう。'''
}
def chunks(caption):
 # Preserve the complete displayed text, split into readable two-line cues.
 sentences=re.findall(r'.*?[。！？]|.+$',caption)
 groups=[];current=''
 for sentence in sentences:
  if current and len(current+sentence)>64: groups.append(current);current=''
  while len(sentence)>64:
   split=sentence.rfind('、',22,61)+1
   if split<23: split=60
   if current:groups.append(current);current=''
   groups.append(sentence[:split]);sentence=sentence[split:]
  current+=sentence
 if current:groups.append(current)
 result=[]
 for s in groups:
  if len(s)<=34: lines=[s]
  else:
   cut=s.rfind('、',max(10,len(s)-34),35)+1
   if cut<max(10,len(s)-34):cut=min(34,(len(s)+1)//2)
   lines=[s[:cut],s[cut:]]
  result.append(dict(caption=s,lines=lines,weight=len(s)))
 return result
replacements=[('defaultOutboundAccess','デフォルトアウトバウンドアクセス'),('EXPECTED_EGRESS_IP','エクスペクテッド・イーグレス・アイピー'),('expectedEgressIp','エクスペクテッド・イーグレス・アイピー'),('IANAPrivateRanges','アイアナ・プライベート・レンジズ'),('snat.privateRanges','スナットのプライベートレンジズ'),('identityEndpoint','アイデンティティ・エンドポイント'),('gatewayOrigin','ゲートウェイ・オリジン'),('api.ipify.org','エーピーアイ・アイピファイ・ドット・オーグ'),('Private Endpoint','プライベートエンドポイント'),('Firewall','ファイアウォール'),('Application','アプリケーション'),('Network','ネットワーク'),('Operation','オペレーション'),('Provider','プロバイダー'),('Cloudflare','クラウドフレア'),('Route53','ルート・フィフティースリー'),('PowerShell','パワーシェル'),('Windows','ウィンドウズ'),('Ubuntu','ウブントゥ'),('Microsoft','マイクロソフト'),('production','プロダクション'),('staging','ステージング'),('GitHub','ギットハブ'),('Python','パイソン'),('Docker','ドッカー'),('Standard','スタンダード'),('Static','スタティック'),('Public IP','パブリックアイピー'),('Load Balancer','ロードバランサー'),('NAT Gateway','ナットゲートウェイ'),('private','プライベート'),('HTTPS','エイチティーティーピーエス'),('HTTP','エイチティーティーピー'),('WSS','ダブリューエスエス'),('SNAT','スナット'),('FQDN','エフキューディーエヌ'),('APIM','エーピーアイエム'),('AppGW','アプリケーションゲートウェイ'),('Portal','ポータル'),('Azure','アジュール'),('params','パラメーター'),('Bicep','バイセップ'),('ACME','アクミ'),('ACR','エーシーアール'),('ACA','エーシーエー'),('UDR','ユーディーアール'),('NSG','エヌエスジー'),('IDPS','アイディーピーエス'),('FQDN','エフキューディーエヌ'),('DNS','ディーエヌエス'),('JSON','ジェイソン'),('TLS','ティーエルエス'),('IPv4','アイピーバージョンフォー'),('API','エーピーアイ'),('PE','プライベートエンドポイント'),('IP','アイピー'),('CA','シーエー'),('OS','オーエス'),('npm','エヌピーエム'),('false','フォルス'),('Allow','アロー')]
turns=[]
for chapter,*_ in chapters:
 for row in rows[chapter].splitlines():
  speaker,vis,caption=row.split('|',2);speech=caption
  for a,b in replacements:speech=speech.replace(a,b)
  cue=chunks(caption)
  delivery={'mako':'視聴者の疑問を自然に確かめる。文末の質問と納得を区別し、通常速度で話す。','manabu':'運用する人の目的と判断につなげる。落ち着いた対話で、結論を明確にする。','ikumi':'図の対象を順に示すように、設定と結果の違いを明瞭に説明する。通常速度を保つ。'}[speaker]
  turns.append(dict(id=f'lesson06-turn-{len(turns)+1:03}-{speaker}',speaker=speaker,chapterId=chapter,speech=speech,caption=caption,subtitleLines=cue[0]['lines'],subtitleChunks=cue,gapAfterSeconds=.16,delivery=delivery,visual=copy.deepcopy(V[vis])))
ref=lambda a:dict(file='../01-network-control.html',anchor=a)
S.update(description='Web依存先の許可から、生成されるFQDN、SNAT、出口IPの期待値と実測までを、一つの通信に沿って確認する。',status='draft',sourceReferences=[ref(a) for a in ['fw-base-applications','fw-portal-applications','fw-generated-settings','egress-explicit','egress-snat']],chapters=[dict(id=id,title=title,goal=goal,sourceRefs=[ref(a) for a in anchors]) for id,title,goal,anchors in chapters],turns=turns,scope='2026-11-01参照の教材・現行リポジトリ定義・公開設定例に基づく。稼働中Azureの取得値・実測結果ではない。')
(HERE/'script.json').write_text(json.dumps(S,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(turns=len(turns),captionCharacters=sum(len(t['caption']) for t in turns),speechCharacters=sum(len(t['speech']) for t in turns),chapters={c[0]:sum(t['chapterId']==c[0] for t in turns) for c in chapters}),ensure_ascii=False,indent=2))
