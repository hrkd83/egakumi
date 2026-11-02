// Add an introductory connection-model comparison to the accepted settings edit.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {makeConnectivityComparison} from './make-connectivity-comparison.mjs';
const dir=path.dirname(fileURLToPath(import.meta.url));
const script=JSON.parse(await fs.readFile(path.join(dir,'versions/settings-v2/script.json'),'utf8'));
const comparison=await makeConnectivityComparison();
const lines=text=>{const result=[];let line='',width=0;for(const c of text){const w=/[\x00-\x7f]/.test(c)?.56:1;if(width+w>42){result.push(line);line='';width=0;}line+=c;width+=w;}if(line)result.push(line);if(result.length>2)throw new Error('Split the caption sentence');return result;};
const definitions=[
 ['011a3','mako','none',
  'PEを作る場合と、APIMやACAのように作らない場合。DNSの前に、VNetへのつなぎ方を整理してもらえますか？',
  'ピーイーを作る場合と、エーピーアイエムやエーシーエーのように作らない場合。ディーエヌエスの前に、ブイネットへのつなぎ方を整理してもらえますか？'],
 ['011b3','ikumi','pe',
  '図の上はPrivate Endpointです。Key Vaultなどのサービス本体を自分たちのVNetへ移さず、VNet内にprivate IPの接続口を作ります。アプリはこのPEを通じてサービスへ接続します。',
  '図の上はプライベートエンドポイントです。キーボールトなどのサービス本体を自分たちのブイネットへ移さず、ブイネット内にプライベートアイピーの接続口を作ります。アプリはこのピーイーを通じてサービスへ接続します。'],
 ['011c3','mako','subnets',
  '下の図は、APIMやACAの環境を、専用サブネットへ接続する形ですね。こちらの方式には、どんな名前が付いていますか？',
  '下の図は、エーピーアイエムやエーシーエーの環境を、専用サブネットへ接続する形ですね。こちらの方式には、どんな名前が付いていますか？'],
 ['011d3','ikumi','subnets',
  'この構成のAPIMは、VNetインジェクションの内部モードです。ACAは、専用サブネットを使う、独自VNetとの統合による内部環境です。この二つには、自身のPEを作っていません。',
  'この構成のエーピーアイエムは、ブイネットインジェクションの内部モードです。エーシーエーは、専用サブネットを使う、独自ブイネットとの統合による内部環境です。この二つには、自身のピーイーを作っていません。'],
 ['011e3','manabu','subnets',
  '同じサービスでも、構成によって別の接続方式を選べます。今回は、PEの入口と、APIM・ACAの内部入口を分けて押さえましょう。',
  '同じサービスでも、構成によって別の接続方式を選べます。今回は、ピーイーの入口と、エーピーアイエム、エーシーエーの内部入口を分けて押さえましょう。'],
 ['011f3','ikumi','dns',
  'DNSの答えも対応させます。PEを使うサービス名なら、PEの実IP。APIMの内部名なら内部VIP、ACAの内部アプリ名なら環境の実staticIpです。この区別を持って、まずPEから見ていきましょう。',
  'ディーエヌエスの答えも対応させます。ピーイーを使うサービス名なら、ピーイーの実アイピー。エーピーアイエムの内部名なら内部ブイアイピー、エーシーエーの内部アプリ名なら環境の実スタティックアイピーです。この区別を持って、まずピーイーから見ていきましょう。']
];
const newTurns=definitions.map(([key,speaker,focus,caption,speech])=>{
 const captions=caption.match(/[^。！？]+[。！？]?/g)??[caption];
 const subtitleChunks=captions.map(caption=>({caption,lines:lines(caption),weight:[...caption].length}));
 return {id:`lesson02-turn-${key}-${speaker}`,speaker,chapterId:'connection-models',speech,caption,
  subtitleChunks,gapAfterSeconds:.12,
  delivery:speaker==='mako'?'図を見ながら相手へ確かめる素朴な問いとして、穏やかに話す。':speaker==='manabu'?'目的と判断の違いを、同僚へ整理して返す。落ち着いた通常速度で話す。':'初めて学ぶ相手に図の上下を示すつもりで、用語の前後を軽く区切り、明瞭な通常速度で説明する。',
  visual:{...comparison.view,svg:comparison.files[focus]}};
});
const insertion=script.turns.findIndex(t=>t.id==='lesson02-turn-012-mako');
if(insertion<0)throw new Error('Missing accepted insertion point');
script.turns[insertion].id='lesson02-turn-012r3-mako';
script.turns.splice(insertion,0,...newTurns);
script.chapters.splice(script.chapters.findIndex(c=>c.id==='pe'),0,{
 id:'connection-models',title:'PEとVNet接続の方式を、先に整理する',
 goal:'接続口だけをVNet内へ置くPEと、この構成のAPIM・ACAのサブネット接続を図で区別し、DNSが返す入口IPへつなぐ',
 sourceRefs:[{file:'../01-network-control.html',anchor:'dns-private-endpoints'},{file:'../01-network-control.html',anchor:'dns-internal-services'}]
});
script.description='PEとAPIM・ACAのVNet接続方式を最初に図で比較し、各入口のIPとDNSの対応を設定メニューで学ぶ第2回。';
script.visualEdition='teaching-settings-connectivity-v3';
script.externalSourceReferences=[
 {title:'Private Endpoint overview',url:'https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview'},
 {title:'API Management virtual network concepts',url:'https://learn.microsoft.com/en-us/azure/api-management/virtual-network-concepts'},
 {title:'Container Apps virtual network configuration',url:'https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks'}
];
await fs.writeFile(path.join(dir,'script.json'),JSON.stringify(script,null,2)+'\n');
await fs.writeFile(path.join(dir,'connectivity-revision.json'),JSON.stringify({
 edition:script.visualEdition,baseScript:'versions/settings-v2/script.json',newTurnIds:newTurns.map(t=>t.id),
 rerecordedTurn:{old:'lesson02-turn-012-mako',current:'lesson02-turn-012r3-mako',reason:'The immediately preceding spoken context changed; preserve words while recording the new style context.'},
 unchangedOriginalTurnCount:79,newRecordingCount:7,totalTurnCount:86,
 comparison:{viewId:comparison.view.viewId,baselineSha256:comparison.baselineSha256,turnIds:newTurns.map(t=>t.id)},
 continuousPreview:{fromTurn:newTurns[0].id,throughTurn:newTurns[3].id},sources:script.externalSourceReferences
},null,2)+'\n');
console.log(JSON.stringify({turns:script.turns.length,newTurnIds:newTurns.map(t=>t.id),rerecordedTurn:'lesson02-turn-012r3-mako'}));
