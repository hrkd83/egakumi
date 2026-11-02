from pathlib import Path
import copy,json,re
HERE=Path(__file__).resolve().parent
S=json.loads((HERE/'script.json').read_text())
def ref(a):return dict(file='../01-network-control.html',anchor=a)
chapters=[
 ('opening','今回の問い','接続成功と切替完了の違いを問いにする。',['hub-dns','hub-rollout']),
 ('purpose','切替の完了条件','名前・経路・許可・管理元を一組として整理する。',['hub-policy','hub-rollout']),
 ('dns','二つのDNS設定','VNetとFW自身のDNS、通信許可と反映を区別する。',['hub-dns']),
 ('private','8ゾーンと管理元','Private LinkとAPIM・ACAの回答、転送と所有を維持する。',['hub-private-dns']),
 ('policy','許可と実際の送信元','NSG・FWの制御を実経路と送信元に合わせる。',['hub-policy']),
 ('egress','出口IPを照合する','SNAT・許可元・期待値を区別して確認する。',['hub-policy']),
 ('rollout','保存・準備・段階切替','対象範囲ごとに切替可能な手順を整える。',['hub-rollout']),
 ('verify','新しい接続で検証する','DNS・経路・制御・送信元を同じ通信で照合する。',['hub-dns','hub-rollout']),
 ('restore','切戻しと再配備','共有資源への影響を絞り、恒久設定と管理責任をそろえる。',['hub-private-dns','hub-rollout']),
 ('check','三つの確認問題','DNS・送信元・再配備の誤解を確認する。',['hub-dns','hub-policy','hub-rollout']),
 ('summary','まとめと次の問い','運用できる切替の条件を整理し、第9回へつなげる。',['hub-dns','hub-private-dns','hub-policy','hub-rollout'])]
def cards(heading,*items):return dict(layout='cards',heading=heading,cards=[dict(title=t,body=b) for t,b in items])
def node(id,label,detail,x,y,w=280,h=100):return dict(id=id,label=label,detail=detail,x=x,y=y,width=w,height=h)
def E(a,b,label=None,**kw):return dict({'from':a,'to':b},**({'label':label} if label else {}),**kw)
def diag(heading,nodes,edges,focus=()):return dict(layout='diagram',heading=heading,nodes=nodes,edges=edges,focus=list(focus))
V={
'opening':dict(layout='hologram',heading='つながったら、切替は完了？'),
'conditions':cards('名前・経路・許可・管理元を一組で確認',('通信の成立',['必要な名前の回答','往路と復路・NSG・FW','実際の送信元と出口IP']),('運用の成立',['切戻しの範囲と条件','ログと通知の担当','次回配備・撤去の管理元'])),
'dns-two':diag('VNetとFirewall自身は、DNS設定が別',[node('client','VNetクライアント','VNet側の設定',20,100,310),node('dns','中央DNS','転送・回答を準備',445,290,310),node('fw','Firewall自身','FQDN判定用の設定',870,100,310)],[E('client','dns','問い合わせ'),E('fw','dns','問い合わせ')],['client','fw']),
'dns-input':cards('同じ空配列でも、設定の場所で意味が違う',('network.dnsServers',['未指定 / []：Azure提供DNS','IP配列：中央DNS等を指定','VNetと対応NSGのDNS宛先']),('network.firewall.dnsServers',['未指定：VNet側の値を継承','明示 []：FWはAzure提供DNS','IP配列：FWの上流を個別指定'])),
'proxy':cards('DNS Proxyを有効にしても、問い合わせ先は別',('FWのDNS Proxy',['現行はenableProxy: true','FW側機能の設定','VNet DNSの自動変更なし']),('整合させる回答',['クライアントが得るIP','FWがFQDN判定に使うIP','必要な名前を両方で確認'])),
'dns-allow':cards('中央DNSまでの往復とTCP / UDP 53を用意',('経路と制御',['ピアリングで直接届くか','FWを通る経路になるか','NSGと経路上のFWを確認']),('現行custom-dnsの送信元',['APIM・管理VMだけ','AppGW・ACAは含まない','FW経由なら追加許可を設計'])),
'dns-apply':cards('切替は、各サービスへの反映まで計画する',('APIM・AppGW',['APIM：ネットワーク構成適用','AppGW：Stop / Start','対象・保守時間を確認']),('管理VM・各クライアント',['DNS再取得・必要な再起動','新規接続で使用DNSを確認','設定保存だけで完了にしない'])),
'zones-six':cards('Private Linkの6ゾーンを維持する',('Foundry PE：3ゾーン',['privatelink.cognitiveservices.azure.com','privatelink.openai.azure.com','privatelink.services.ai.azure.com']),('Key Vault・ACR・SQL PE：3ゾーン',['privatelink.vaultcore.azure.net','privatelink.azurecr.io','privatelink.database.windows.net'])),
'zones-two':cards('APIMとACAの内部名は、さらに2ゾーン',('実APIM Gateway FQDNのゾーン',['@ A → 実APIM VIP','azure-api.net全体にしない','実際に取得した名前で管理']),('実ACA defaultDomainのゾーン',['* A → 実ACA static IP','現行定義にapex @はない','アプリ名を含むFQDNで確認'])),
'forward':diag('中央DNSから、Azure側Resolver等へ転送',[node('client','問い合わせ元','サービスの公開名',20,170,270),node('central','中央DNS','公開ゾーンから転送',455,170,270),node('azure','Azure側Resolver等','ゾーン参照で解決',890,170,290)],[E('client','central','名前解決'),E('central','azure','転送')],['central','azure']),
'forward-detail':cards('転送・VNetリンク・CNAME連鎖を確認',('SQLの例',['database.windows.netを転送','Private Link名へのCNAME','最終回答が実PE IPか確認']),('参照の準備',['Azure側Resolver等のVNetへリンク','APIM・ACAの内部名も参照可能に','リンクの自動登録は無効を維持'])),
'zone-own':cards('ゾーンを残すか、中央へ移すかを決める',('現在のゾーンを維持',['中央DNSからAzure側へ転送','解決に使うVNetへゾーンをリンク','PE zone groupの管理を維持']),('中央のゾーンへ移管',['中央ゾーンのresource・scope','zone group参照と実IPレコード','IaC・配備・削除責任も変更'])),
'no-double':cards('レコードの管理元を一つにする',('PEとzone group',['実PEに対応するレコードを連携','手動で同じレコードを二重管理しない','更新・削除が中央へ及ぶ範囲も確認']),('API公開Gateway・Portal',['公開Aレコード → AppGW公開IP','内部DNSの集約と別の役割','公開名を一括でprivate IPへ変えない'])),
'policy-src':diag('許可は、NSGが観測する送信元で合わせる',[node('operator','社内・運用元','元の送信元',20,170,280),node('middle','中継装置 / FW','経路とSNATを確認',455,170,280),node('target','Coreの対象IP','実送信元・ポートで許可',890,170,280)],[E('operator','middle'),E('middle','target','実送信元')],['target']),
'nsg':cards('必要な許可を追加し、既存の制御を維持',('残す制御',['4NSGの関連付け','4区画の受信終端拒否','APIM・管理VMの送信終端拒否']),('絞り込む対象',['実送信元CIDR・宛先IP・ポート','管理VMの運用元設定も更新','PE許可は各実PE IPへ限定'])),
'vnet-tag':cards('VirtualNetworkタグの範囲を接続後に再確認',('ピアリングの影響',['自VNetだけの意味ではない','ピアリング先等も含む','既定許可を含む実効NSGを見る']),('特に確認する区画',['AppGW・PE','独自の送信終端拒否なし','ハブ全体の全面許可へ置換しない'])),
'preserve':cards('基盤・入口・private接続の役割を維持',('基盤の通信',['AppGW：GatewayManager / LB','APIM：管理TCP 3443等','APIM管理の直接復路']),('入口と内部接続',['AppGW ↔ ローカルFW ↔ 内部入口','PE・APIM → ACAの直接通信','Public Network Access・IAMを維持'])),
'fw-rules':cards('ハブFWには、実効設定から依存先を追加',('対象の区画',['APIM：Azure基盤・外部backend','管理VM：認証・更新・ビルド等','ACA：ACR / MCR・監視・外部API等']),('規則の使い分け',['IP・タグ・ポート：Network','WebのFQDN：Application','中央の既定拒否・企業ルールも照合'])),
'fw-order':cards('広いTCP 443許可で、FQDN制御を代替しない',('評価の順番',['Networkが先に一致','その通信はApplicationへ進まない','広いNetwork Allowの影響を確認']),('維持する入口の許可',['ローカルFWの内部入口許可','対応する往復UDRとセット','出口集約で役割が消えるわけではない'])),
'snat':cards('NetworkとApplicationのSNATを区別',('Networkルール',['宛先とprivateRanges等で判断','SNATされる・されないを照合','FW両側の送信元と復路を見る']),('Applicationルール',['常にSNATする仕様','NetworkのSNAT除外と別','private宛てでも同じ前提にしない'])),
'ip-check':diag('公開先が観測する送信元を照合する',[node('apim','APIM等の対象区画','新しい公開宛て接続',20,170,280),node('hub','採用したハブFW','公開SNAT元',455,170,280),node('remote','接続先 / 公開AppGW','許可元・ログと照合',890,170,280)],[E('apim','hub'),E('hub','remote','実測で照合')],['hub','remote']),
'ip-fixed':cards('既存の許可元・期待値は、ハブIPへ自動追従しない',('allow-certificate-observer',['現行はローカルFW公開IPを参照','対象区画の実出口に合わせる','公開AppGWの許可元を照合']),('expectedEgressIp',['現行はローカルFW公開IPを参照','期待値と観測結果を区別する','Portal → 公開Gatewayも実送信元で確認'])),
'baseline':cards('現状保存と復旧条件を、切替の前に用意',('保存する構成',['CIDR・両側ピアリング','4RTの全ルート・経路伝搬','VNet / FWのDNS']),('保存する制御と名前',['NSG / FW・ゾーン・リンク・レコード','現在の出口IP・依存先の許可元','変更対象・担当・切戻し条件'])),
'prepare':cards('中央側の準備と保守経路を先に確保',('企業側と合意するもの',['ハブの許可・往復経路','中央DNSの転送・ゾーン参照','中央側ログ検索先と担当','切替前に中央DNSへ直接照会']),('Core側で確保するもの',['両側ピアリングの状態','必要なNSG / FW許可を先に追加','管理VMへの到達と作業権限'])),
'stages':diag('影響範囲ごとに切替・反映・確認する',[node('prepare','事前準備','許可・復路・DNS参照',20,170,280),node('change','対象範囲の切替','DNS・出口UDR・伝搬',455,170,280),node('test','新規接続で確認','同じ通信を各層で照合',890,170,280)],[E('prepare','change','準備後'),E('change','test','反映後')],['change']),
'new':cards('既存接続の成功だけで、切替成功としない',('新規接続の理由',['古いDNS回答や接続状態を避ける','対象サービスへの反映を確認','同じ宛先への新しい要求で確認']),('同じ通信でそろえる証拠',['DNS回答と実際の接続先','実効経路・NSG・FWログ','公開先が観測した送信元'])),
'verify-matrix':cards('業務通信と管理・基盤通信を確かめる',('名前とアプリ',['8ゾーンの実FQDN・公開名・外部名','API / Portal / PEの到達性','入口の往復・公開出口を比較']),('管理と依存先',['APIM管理・SSH・証明書更新','イメージ取得・ビルド・監視','中央DNS・社内宛ての往復'])),
'observe':cards('ログの記録と、通知の担当をそろえる',('Coreの確認',['DNS・実効経路・NSG・FW','同じ送信元・宛先・時刻で照合','API応答だけで完了としない']),('企業側の確認',['中央FW・DNS・ピアリング','診断・検索先・アラート・担当','Coreの既存通知が自動で覆うとはしない'])),
'rollback':cards('切戻しは、保存したCore側の変更値へ',('戻す対象',['対象範囲のDNS・出口・伝搬','変更した許可・参照設定等','保存した値と復旧条件に従う']),('共有資源への配慮',['共有ハブ全体を巻き戻さない','他スポークの設定を戻さない','戻した後も新規接続で再確認'])),
'iac':diag('手動変更を、次回配備の正本へ反映する',[node('manual','検証した変更','手動UDR・DNS等',20,170,280),node('source','config / IaC / 配備処理','宣言する管理元を変更',455,170,280),node('redeploy','次回 up','適用値・差分を確認',890,170,280)],[E('manual','source','恒久化'),E('source','redeploy','再確認')],['source']),
'route-keep':cards('既存ルート保持は、手動変更の恒久化とは別',('配備処理の保持',['4RTの既存ルートを読み取る','親RT更新で引き継ぐ','経路が一時的に消えることを防ぐ']),('Bicepの再適用',['宣言する同名ルートを再適用','経路伝搬も今回の設定を適用','default-egressがローカルFWへ戻り得る'])),
'delete':cards('配備だけでなく、撤去の担当も決める',('中央DNSとCore',['PE / zone group更新・削除','中央レコードへの影響を確認','資源・レコードごとに管理元を決定']),('downの分担',['両側ピアリングとGateway共有','中央ゾーン参照・FW許可の解除','共有資源をCore所有とみなさない'])),
'q1':dict(layout='quiz',heading='確認①：VNetだけ中央DNSへ変えたら？',bullets=['FW側のdnsServersは、明示的な [] のまま。','FW自身も中央DNSを使う？','使わない。VNetとFWの設定を別々に確認。']),
'q2':dict(layout='quiz',heading='確認②：ハブ接続後はハブCIDRを全面許可？',bullets=['NSGの許可元を、ハブCIDR全体に置き換える？','NSGが観測する実送信元・宛先・ポートで許可。','SNAT・VirtualNetworkタグの影響も確認する。']),
'q3':dict(layout='quiz',heading='確認③：疎通したら、次のupも同じ？',bullets=['手動でdefault-egressをハブFWへ変更した。','既存ルート保持があるから、恒久化も済んだ？','同名ルート・伝搬は再適用。正本へ反映する。']),
'summary':dict(layout='summary',heading='解決・到達・許可・管理を、同じ通信でそろえる',bullets=['VNetとFWのDNS、8ゾーンの回答と管理元。','NSG / FWの許可、実送信元と出口IP。','保存 → 準備 → 段階切替 → 新規接続で検証。','切戻し・監視担当・IaC・再配備・撤去まで。']),
'next':cards('次は第9回：WAFの検査と誤検知への対応',('経路の次は、要求の検査',['公開入口から各層の防御へ','WAFが何を検査するか','正規データの除外をどこまで絞るか']),('今回の土台',['通信がどこへ届くかを説明','観測する送信元・確認先を明確に','基盤の切替と運用を維持する']))
}
rows={
'opening':'''mako|opening|ハブ接続後にAPIが一度返れば、切替は完了でしょうか。DNSや出口を変えても、次の配備まで同じままか気になります。
manabu|opening|一度の成功だけでは足りません。新しい接続でも動き、問題があれば戻せることまで、完了条件にしましょう。
ikumi|opening|今日は中央DNSへの切替から、公開通信の許可と送信元、再配備までを追います。前回の往復経路に、名前と管理を合わせます。
mako|opening|変えた設定を維持できることも大切なんですね。何を保存し、どこで確かめるか、順に見ていきましょう。''',
'purpose':'''manabu|conditions|名前の回答、通信の経路、必要な許可、設定の管理元を一組で説明します。利用と保守を続けられる状態を目指します。
mako|conditions|A1の経路を土台に、ローカルFirewallの入口検査や、PEへの直接接続も維持する前提ですか。
ikumi|conditions|はい。対象区画を個別に選びます。今回も教材と構成定義に基づく接続案で、稼働Azureへ適用・実測した結果ではありません。
manabu|conditions|担当をそろえ、現状保存、事前準備、段階切替、検証、復旧、再配備の順で考えます。''',
'dns':'''mako|dns-two|VNetを中央DNSへ変えれば、Firewallも同じDNSを使うと考えてよいでしょうか。
ikumi|dns-two|VNetクライアントと、Firewall自身がFQDNを判定する上流DNSは、別に指定できます。
ikumi|dns-input|network.dnsServersへIP配列を指定すると、VNetのDNSと、AppGW、APIM、管理VMのNSGにあるDNS宛先が変わります。未指定か空配列ならAzure提供DNSです。
mako|dns-input|では、network.firewall.dnsServersの未指定と空配列には、違いがあるんですね。
ikumi|dns-input|あります。未指定ならVNet側の値を引き継ぎます。明示的な空配列は、Firewall自身がAzure提供DNSを使う指定です。中央DNSへ自動で追従しません。
manabu|proxy|似た入力でも、設定場所で意味が違うんですね。必要な名前について、クライアントとFirewallの回答を整合させます。
ikumi|proxy|DNS Proxyは有効ですが、VNetの問い合わせ先をFirewallのprivate IPへ自動変更する設定ではありません。問い合わせ先と上流を確認します。
mako|dns-allow|中央DNSが決まれば、NSGのDNS宛先だけ変えればよいのでしょうか。
ikumi|dns-allow|往復経路とTCP、UDPの53番も必要です。現行custom-dnsのFirewall許可はAPIMと管理VMだけ。AppGWやACAがFirewall経由になるなら、追加の許可を設計します。
ikumi|dns-apply|反映も計画します。APIMのネットワーク構成適用、AppGWの停止と開始、管理VMのDNS再取得や再起動など、必要な手順と保守時間を確認します。''',
'private':'''mako|zones-six|問い合わせ先が変わっても、内部名の回答は維持するんですね。確認するゾーンはいくつありますか。
ikumi|zones-six|Private Linkが六つです。Foundry用の三つと、Key Vault、ACR、SQL用の三つ。PEのzone groupが管理するレコードと、実際のPEのIPを対応付けます。
ikumi|zones-two|さらにAPIMとACAの内部ゾーンが二つあります。APIMは実Gateway FQDNと同名のゾーンで、apexのAレコードを実内部VIPへ向けます。
manabu|zones-two|ACAは実defaultDomainのゾーンで、ワイルドカードのAレコードが実static IPを指します。現行定義にはapexのAレコードがない点も分けて確認するんですね。
mako|forward|八つのゾーンへの参照も必要ですね。ピアリングをつないだだけで、その参照も共有されますか。
ikumi|forward|共有されません。中央DNSからAzure側Resolverやforwarderへ転送し、解決に使うVNetへゾーンをリンクします。Azure提供DNSを直接使う側のリンクも維持します。
ikumi|forward-detail|SQLならdatabase.windows.netのような公開ゾーンから条件付き転送し、Private Link名へのCNAME連鎖と最終のprivate IPを確認します。APIMとACAの内部名も別途参照可能にします。
mako|zone-own|ゾーンを残すか中央へ移すかで、管理の分担も違いますね。
ikumi|zone-own|中央へ移すなら、zone groupの参照先と、APIM、ACAの実IPレコードを向け直します。現行には既存中央ゾーンIDの公開入力がなく、IaCと配備、削除責任の変更が必要です。
manabu|no-double|zone groupのレコードを、手動でも二重管理しない。資源ごとの更新と削除の担当を合わせ、回答の食い違いを防ぎます。
ikumi|no-double|APIの公開GatewayとPortalの公開Aレコードは、AppGWの公開IPを維持します。内部DNSを集約することと、公開名をprivate IPへ一括変更することは別の設計です。''',
'policy':'''mako|policy-src|次は許可です。ハブ接続後は、NSGの許可元をハブ全体のCIDRへ置き換えればよいでしょうか。
ikumi|policy-src|NSGが実際に観測する送信元を使います。中継やSNATで変わるかを確認し、必要な送信元CIDR、対象IP、ポートへ絞ります。管理VMの運用元設定も照合します。
manabu|nsg|誰のどの作業に必要かを説明できる許可にします。PEへの許可も各サービスの実IPに絞ったまま維持します。
ikumi|nsg|四つのNSGと受信の終端拒否、APIMと管理VMの送信終端拒否も維持します。新しい中央DNSなど、変えた宛先へ必要な許可を追加します。
mako|vnet-tag|既定のVirtualNetworkタグも、以前と同じ範囲とは限らないんですね。
ikumi|vnet-tag|自VNetだけでなく、ピアリング先なども含みます。特に独自の送信終端拒否がないAppGWとPEでは、既定許可も含む実効NSGを接続後に確認します。
ikumi|preserve|AppGWの基盤通信、APIMの管理通信と直接復路、ローカルFirewallの入口検査は残します。PEやAPIMからACAへの直接通信も、公開出口と混同しません。
manabu|preserve|つながらない対策として、Public Network AccessやIAMをまとめて緩めることもしません。管理VMへ届く経路と、SSHで本人を確認する仕組みも別に維持します。
ikumi|fw-rules|ハブFirewallには、対象区画の実効設定から依存先を追加します。IPやタグとポートのNetworkルール、WebのFQDNを扱うApplicationルールを使い分けます。
ikumi|fw-order|Networkが先に一致すると、その通信はApplicationで評価されません。広いTCPの443番許可でFQDN制御を代替せず、中央の既定拒否や企業の規則も照合します。''',
'egress':'''mako|snat|企業内接続なら、Firewallを通っても元の送信元IPのままだと考えてよいですか。
ikumi|snat|方式を区別します。Networkルールは宛先やSNAT除外の設定で変わります。Applicationルールは常にSNATする仕様で、Networkの除外設定と同じではありません。
manabu|snat|Firewallの前後の送信元と復路で、許可を説明するんですね。
ikumi|ip-check|公開出口をハブへ移した区画では、新しい接続で公開SNAT元を確認します。外部接続先や公開AppGWが必要とする許可元と、実際の出口を合わせます。
mako|ip-fixed|構成にある証明書の確認用許可や、期待する出口IPは、ハブの公開IPへ自動で変わるのでしょうか。
ikumi|ip-fixed|変わりません。現行のallow-certificate-observerとexpectedEgressIpは、ローカルFirewallの公開IPを参照しています。採用した出口に合わせて管理元を更新します。
manabu|ip-fixed|期待値を保存することと、実際の出口を観測することも別です。Portalから公開Gatewayへ呼び出す通信も、実送信元で許可と結果を照合しましょう。''',
'rollout':'''mako|baseline|切り替える順番へつなげます。最初は、元へ戻すための保存からですね。
ikumi|baseline|はい。アドレス、ピアリング、四つのルートテーブルの全経路と伝搬、VNetとFirewallのDNSを保存します。NSGとFirewall、ゾーン、リンク、レコード、出口IPも対象です。
manabu|baseline|対象と担当、合格条件、切戻し条件を決めます。元の値と、復旧できる範囲を先に明確にします。
ikumi|prepare|中央側の許可・復路、DNS転送・ゾーン参照、ログ検索先を準備します。切替前に中央DNSへ直接問い合わせ、公開名と内部名の回答を確かめます。両側のピアリング、必要な許可、管理VMへの保守経路と権限も確保します。
mako|stages|準備が整ったら、DNS、区画別の出口、経路伝搬を全部同時に変えるのでしょうか。
manabu|stages|影響範囲ごとに分けて切り替えます。何を変えた結果かを追え、合格と判断できる単位を企業側と合わせることが大切です。
ikumi|stages|必要なNSGとFirewall許可を先に用意し、対象のDNS、出口UDR、伝搬を変更して、各サービスへ反映します。許可と経路を用意する前に切り替えないようにします。
mako|stages|中央DNSへ切り替えてから、APIMの公開通信を確かめる主例ですね。名前解決と公開宛ての要求は、別の通信として追います。''',
'verify':'''mako|new|切替後に、さっき開いていたAPI画面が動けば合格、としてよいでしょうか。
ikumi|new|新しい接続で確認します。古いDNS回答や既存接続の状態が残るためです。変更後のDNSを使い、期待する宛先へ新しく要求したことを確かめます。
manabu|new|成功か失敗かに加え、どのIPへ解決し、どの経路を通り、どこで許可されたかをそろえるんですね。
ikumi|verify-matrix|八つのゾーンに関わる実FQDN、公開名、外部名を確認します。実効経路、NSG判定、Firewallログ、公開先が見たSNAT元を、同じ通信で照合します。
ikumi|verify-matrix|APIとPortal、PEに加え、APIM管理、SSH、証明書更新、イメージ取得とビルド、監視も対象です。入口の往復、公開出口、社内や中央DNSへの往復を比較します。
mako|observe|ログが残ることと、担当者に異常が通知されることも、分けて確認する必要がありますね。
ikumi|observe|はい。中央Firewall、DNS、ピアリングの診断やアラート、担当を企業側と決めます。Coreの既存通知が、共有ハブ資源まで自動で覆うとは扱いません。''',
'restore':'''mako|rollback|不合格なら保存した設定へ戻しますね。企業ハブ全体も、接続前へ戻すのでしょうか。
manabu|rollback|戻すのは、合意したCore側の変更範囲です。共有ハブ全体や他スポークの設定を巻き戻さず、中央側の担当と対象を合わせます。復旧後も同じ通信を確認します。
ikumi|iac|継続採用する変更は、config、IaC、配備処理へ反映します。手動でハブ向けにしたUDRが、次回のupでも同じになるかは、宣言する管理元を見て判断します。
mako|route-keep|既存ルートを保持すれば、手動変更も恒久化されますか。
ikumi|route-keep|言えません。四つのルートテーブルの既存経路を親の更新へ引き継ぎますが、Bicepが宣言する同名ルートと伝搬設定は再適用されます。出口がローカルFirewallへ戻る可能性があります。
manabu|delete|中央ゾーンは、資源とレコードの所有範囲を決めます。PEやzone groupの更新・削除が中央レコードへ及ぶ影響も、配備担当と共有します。
ikumi|delete|downのときの両側ピアリング、中央ゾーン参照、Firewall許可、Gateway共有の解除担当も定めます。疎通だけでなく、次回配備と撤去まで管理できて完了です。''',
'check':'''mako|q1|三つ確認しましょう。一つ目。VNetだけ中央DNSへ変え、Firewall側のdnsServersは明示的な空配列のままです。Firewallも中央DNSを使いますか。
ikumi|q1|使いません。明示的な空配列なら、Firewall自身はAzure提供DNSを使います。未指定の場合の継承と区別し、クライアントとFirewallの解決結果をそれぞれ確認します。
manabu|q1|誰が使う設定かを言葉にする。DNS Proxyだけで、問い合わせ先がそろうとも判断しません。
mako|q2|二つ目。ハブにつないだので、NSGの許可元をハブCIDR全体へ置き換えてよいでしょうか。
ikumi|q2|実送信元、宛先、ポートへ絞ります。SNATとVirtualNetworkタグの影響を確認し、終端拒否とPEの限定許可を維持します。
manabu|q2|接続の追加と、許可の全面拡大は別です。誰のどんな通信を通すかを、経路と送信元から説明しましょう。
mako|q3|最後です。default-egressを手動でハブFirewallへ変えました。次のupも、既存ルート保持で同じになりますか。
ikumi|q3|いいえ。同名ルートと伝搬はBicepの値を再適用します。正本へ恒久変更を反映し、次回の適用値と差分を確認します。切戻しと撤去の責任も決めます。
manabu|q3|保存、検証、復旧、再配備を一続きとして管理し、通信の成功を運用へつなげるんですね。''',
'summary':'''mako|summary|今日の要点です。VNetとFirewallのDNSを分け、八つのゾーンの回答、転送、参照とレコードの管理元をそろえます。
ikumi|summary|NSGとFirewallの許可は実送信元で合わせ、SNATと出口IPを確認します。名前、経路、許可、送信元を、同じ新しい接続について照合してください。
manabu|summary|保存と準備から段階的に切り替え、切戻し、監視担当、IaCと再配備、撤去まで管理する。これが完了条件です。
mako|next|次は第9回、WAFの検査と誤検知への対応です。通信の道筋から、入口で受けた要求を何に基づいて止めるかへ進みます。
manabu|next|攻撃の検査と、正しいデータを通すための調整を扱います。経路と確認先を土台に、各層の防御を見ていきましょう。'''
}
def units(s):return sum(.56 if ord(c)<128 else 1 for c in s)
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
 if not candidates:raise ValueError('Subtitle group too long: '+s)
 _,i=min(candidates);return [s[:i],s[i:]]
def chunks(caption):
 clauses=re.findall(r'.*?[、。！？]|.+$',caption);groups=[];current=''
 for clause in clauses:
  if units(clause)>66:
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
 ('network.firewall.dnsServers','ファイアウォール側のディーエヌエス設定'),('network.dnsServers','ブイネット側のディーエヌエス設定'),('dnsServers','ディーエヌエス設定'),
 ('allow-certificate-observer','アロー・サーティフィケート・オブザーバー'),('expectedEgressIp','期待する出口アイピー'),('default-egress','デフォルト・イーグレス'),
 ('Public Network Access','パブリック・ネットワーク・アクセス'),('VirtualNetwork','バーチャルネットワーク'),('Private Link','プライベートリンク'),('zone group','ゾーン・グループ'),('defaultDomain','デフォルトドメイン'),
 ('database.windows.net','データベース・ドット・ウィンドウズ・ドット・ネット'),('custom-dns','カスタム・ディーエヌエス'),
 ('Application','アプリケーション'),('Network','ネットワーク'),('Firewall','ファイアウォール'),('Resolver','リゾルバー'),('forwarder','フォワーダー'),('Gateway','ゲートウェイ'),
 ('AppGW','アプリケーションゲートウェイ'),('Key Vault','キー・ボールト'),('Foundry','ファウンドリー'),('Portal','ポータル'),('Azure','アジュール'),('Core','コア'),('VNet','ブイネット'),
 ('APIM','エーピーアイエム'),('ACA','エーシーエー'),('ACR','エーシーアール'),('MCR','エムシーアール'),('FQDN','エフキューディーエヌ'),('CNAME','シーネーム'),
 ('CIDR','サイダー'),('SNAT','スナット'),('UDR','ユーディーアール'),('NSG','エヌエスジー'),('DNS','ディーエヌエス'),('SQL','エスキューエル'),('SSH','エスエスエイチ'),('VIP','ブイアイピー'),
 ('IaC','アイアック'),('IAM','アイアム'),('API','エーピーアイ'),('PE','プライベートエンドポイント'),('VM','ブイエム'),('IP','アイピー'),('TCP','ティーシーピー'),('UDP','ユーディーピー'),
 ('WAF','ワフ'),('Proxy','プロキシ'),('private','プライベート'),('apex','エイペックス'),('static','スタティック'),('config','設定ファイル'),('Bicep','バイセップ'),('up','アップ'),('down','ダウン'),('A1','エー・ワン')]
turns=[]
for chapter,*_ in chapters:
 for row in rows[chapter].splitlines():
  speaker,vis,caption=row.split('|',2);speech=caption
  for a,b in replacements:speech=speech.replace(a,b)
  cue=chunks(caption)
  delivery={
   'mako':'視聴者の疑問を一緒に考える、自然な質問と短い納得。相手へ穏やかに確かめ、通常速度で話す。',
   'manabu':'運用する人の目的や判断を、同僚へ落ち着いて語る。何をもって完了とするかを明確にし、通常速度で話す。',
   'ikumi':'設定の違いと図の矢印を順に確かめる、穏やかで明瞭な説明。技術用語の前後を少し区切り、通常速度で話す。'}[speaker]
  turns.append(dict(id=f'lessona2-turn-{len(turns)+1:03}-{speaker}',speaker=speaker,chapterId=chapter,speech=speech,caption=caption,subtitleLines=cue[0]['lines'],subtitleChunks=cue,gapAfterSeconds=.16,delivery=delivery,visual=copy.deepcopy(V[vis])))
S.update(title='A2 ハブ接続後のDNS・通信制御と切替',topic='ハブ接続後のDNS・通信制御と切替',episodeLabel='必修 A2',seriesLabel='ハブ＆スポーク対応 / A1の続き',description='中央DNSへの切替から、Private DNS・NSGとFirewall・送信元と出口IPの照合、新規接続の検証、切戻しと再配備までを説明する。',status='draft',chapters=[dict(id=id,title=title,goal=goal,sourceRefs=[ref(a) for a in anchors]) for id,title,goal,anchors in chapters],turns=turns,sourceReferences=[ref(a) for a in ['hub-dns','hub-private-dns','hub-policy','hub-rollout']]+[dict(file='../../setup/07-hub-spoke.md'),dict(file='../../../infra/modules/network.bicep'),dict(file='../../../infra/modules/network-bindings.bicep'),dict(file='../../../infra/modules/portal-dns.bicep'),dict(file='../../../scripts/deployment/lifecycle.py')])
(HERE/'script.json').write_text(json.dumps(S,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(turns=len(turns),captionCharacters=sum(len(t['caption']) for t in turns),speechCharacters=sum(len(t['speech']) for t in turns),openingSpeechCharacters=sum(len(t['speech']) for t in turns if t['chapterId']=='opening'),chapters={c[0]:sum(t['chapterId']==c[0] for t in turns) for c in chapters}),ensure_ascii=False,indent=2))
