// Episode 05 composites inside the shared teaching-settings-v2 shell.
import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

const folder=new URL('./',import.meta.url);
const plan=JSON.parse(await fs.readFile(new URL('settings-views.json',folder),'utf8'));
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const units=s=>[...String(s)].reduce((n,c)=>n+(c.charCodeAt(0)<128?.58:1),0);
const rect=(x,y,w,h,fill='#fff',stroke='#d7e1e5',sw=1)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const text=(x,y,s,size=17,color='#20323e',weight=400,anchor='start')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" text-anchor="${anchor}" font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">${esc(s)}</text>`;
function wrap(value,width,size,max=2){
  const lines=[];let line='';
  for(const c of String(value)){
    if(c==='\n'){lines.push(line);line='';continue;}
    if(line&&units(line+c)*size>width){lines.push(line);line='';}
    line+=c;
  }
  if(line)lines.push(line);
  if(lines.length>max)throw new Error(`Episode 05 text exceeds ${max} lines: ${value}`);
  return lines;
}
function block(x,y,w,h,value,size=17,color='#20323e',weight=400){
  const lines=wrap(value,w,size),gap=size+4,first=y+h/2+size*.33-(lines.length-1)*gap/2;
  return lines.map((s,i)=>text(x,first+i*gap,s,size,color,weight)).join('');
}
function draw(artifact){
  const {spec,highlightTargets}=artifact,selected=new Set(highlightTargets),hi=[];
  const highlight=(id,x,y,w,h)=>{if(selected.has(id))hi.push(rect(x,y,w,h,'none','#de343b',2.8));};
  const node=(id,x,y,w,h,label,detail='')=>{
    let s=rect(x,y,w,h,'#f5fafb','#8caab2');
    if(detail){s+=text(x+w/2,y+24,label,17,'#174b58',600,'middle')+text(x+w/2,y+45,detail,13,'#526c77',400,'middle');}
    else s+=block(x+10,y,w-20,h,label,16,'#174b58',600);
    highlight(id,x-2,y-2,w+4,h+4);return s;
  };
  const arrow=(x1,y1,x2,y2,label='',both=false)=>`<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="#308c99" stroke-width="2.2" marker-end="url(#ep05-arrow)" ${both?'marker-start="url(#ep05-arrow)"':''}/>`+(label?text(x2+13,(y1+y2)/2+4,label,13,'#4f6e78'): '');
  let content=rect(214,147,558,28,'#edf1f5')+text(226,167,'設定項目・参照対象',16,'#586875',600)+text(376,167,'値・教材注釈',16,'#586875',600);
  for(const [i,row] of spec.rows.entries()){
    const y=175+i*53;
    content+=rect(214,y,558,53)+block(226,y,124,53,row.label,16,'#586875')+block(376,y,384,53,row.value,17,'#087f8c');
    highlight(row.id,216,y+2,554,49);
  }
  const d=spec.diagram;
  content+=text(800,165,d.caption,15,'#526c77',600);
  if(d.kind==='vertical'){
    d.nodes.forEach((n,i)=>{content+=node(n.id,816,183+i*98,354,58,n.label,n.detail);});
    for(let i=0;i<2;i++)content+=arrow(993,241+i*98,993,281+i*98,d.labels?.[i],d.bidirectional);
  }else if(d.kind==='routes'){
    content+=text(800,193,d.upperLabel??'内部中継：Firewall経由',14,'#526c77');
    for(let i=0;i<3;i++)content+=node('relay-'+i,800+i*127,210,113,56,d.upper[i]);
    content+=arrow(913,238,927,238)+arrow(1040,238,1054,238);
    highlight('relay',797,176,374,94);
    content+=text(800,326,d.lowerLabel??'PE宛て：標準の直接経路',14,'#526c77');
    content+=node('direct-source',800,345,148,56,d.lower[0])+node('direct-target',1022,345,148,56,d.lower[1])+arrow(951,373,1018,373);
    highlight('direct',797,307,376,99);
  }else if(d.kind==='pair'){
    d.flows.forEach((f,i)=>{
      const y=198+i*132;
      content+=text(800,y,f.rule,16,'#174b58',600)+node(f.id+'-source',800,y+16,110,58,f.source)+node(f.id+'-target',966,y+16,204,58,f.destination);
      content+=arrow(913,y+46,962,y+46)+text(920,y+31,f.label,12,'#526c77');
      highlight(f.id,797,y-20,376,98);
    });
  }else if(d.kind==='choices'){
    content+=text(800,190,'通信条件と照合して判断する',14,'#647984');
    d.choices.forEach((choice,i)=>{
      const y=216+i*106;
      content+=rect(800,y,370,74,'#fff','#d7e1e5')+text(812,y+21,`候補 ${i+1}`,13,'#647984')+block(812,y+26,346,44,choice.label,18);
      highlight(choice.id,798,y-2,374,78);
    });
  }else throw new Error('Unknown episode 05 diagram kind: '+d.kind);
  const notes=wrap(spec.note,960,14,2);
  notes.forEach((s,i)=>{content+=text(222,455+i*17,s,14,'#526c77');});
  content+=`<g data-layer="highlights">${hi.join('')}</g>`;
  const shell=renderSettingsShell(spec,content);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511"><defs><marker id="ep05-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#308c99"/></marker></defs>${shell}</svg>`;
}
await fs.mkdir(new URL('diagrams/',folder),{recursive:true});
for(const [filename,artifact] of Object.entries(plan.artifacts))await fs.writeFile(new URL('diagrams/'+filename,folder),draw(artifact));
console.log(JSON.stringify({style:plan.visualStyle,diagrams:Object.keys(plan.artifacts).length,sharedShell:'../../settings-visual.mjs'}));
