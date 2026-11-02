// Shared teaching settings view, adopted from episode 01 settings-v2.
// This is a purpose-built teaching menu, not an Azure Portal replica.
// Coordinates are local to the fixed 1207 x 511 body region. No animation.
export const SETTINGS_SIZE=Object.freeze({width:1207,height:511});
export const SETTINGS_CONTENT=Object.freeze({x:214,y:147,width:972,height:328});
const FONT='"Hiragino Sans","Yu Gothic",Meiryo,sans-serif';
const COLORS={ink:'#20323e',muted:'#647984',accent:'#087f8c',red:'#de343b'};
const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&apos;');
const units=value=>[...String(value)].reduce((sum,ch)=>sum+(/[\u0000-\u007f]/.test(ch)? .58:1),0);
const scalar=(value,name)=>{if(!['string','number','boolean'].includes(typeof value)||typeof value==='number'&&!Number.isFinite(value))throw new Error(`settings ${name} must be text or a finite scalar`);return String(value);};
const value=(raw,fallback,name)=>scalar(raw??fallback,name);
const rect=(x,y,width,height,fill='#fff',stroke='#d5e0e5',radius=3,strokeWidth=1)=>`<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeWidth}"/>`;
const text=(x,y,raw,size=19,color=COLORS.ink,weight=400,anchor='start')=>`<text x="${x}" y="${y}" fill="${color}" font-size="${size}" font-weight="${weight}" text-anchor="${anchor}" style="font-family:${esc(FONT)}">${esc(raw)}</text>`;
const outline=(x,y,width,height)=>rect(x,y,width,height,'none',COLORS.red,5,2.8);
function oneLine(raw,width,size,name){
  const s=scalar(raw,name);
  if(s.includes('\n')||units(s)*size>width)throw new Error(`settings ${name} is too long; shorten it or use a separate view`);
  return s;
}
function wrapped(raw,width,size,maxLines,name){
  const s=scalar(raw,name),result=[];let row='';
  for(const ch of s){
    if(ch==='\n'){result.push(row);row='';continue;}
    if(row&&units(row+ch)*size>width){result.push(row);row=ch;}else row+=ch;
  }
  result.push(row);
  if(result.length>maxLines)throw new Error(`settings ${name} needs ${result.length} lines; split the content across views`);
  return result;
}
function block(x,top,width,height,raw,size,color,name){
  const maxLines=height>=48?2:1,lineHeight=size+4;
  const lines=wrapped(raw,width,size,maxLines,name),first=top+height/2+size*.33-(lines.length-1)*lineHeight/2;
  return lines.map((s,i)=>text(x,first+i*lineHeight,s,size,color)).join('');
}
function uniqueRows(rows,name){
  if(!Array.isArray(rows)||rows.length<1||rows.length>6)throw new Error(`settings ${name} requires one to six rows`);
  const ids=new Set();
  for(const row of rows){if(!row||!/^[-a-zA-Z0-9_]+$/.test(row.id??'')||ids.has(row.id))throw new Error(`settings ${name} row IDs must be unique local IDs`);ids.add(row.id);}
  return ids;
}
function highlights(raw,ids,name){
  if(raw===undefined)return new Set();
  if(!Array.isArray(raw)||raw.some(id=>typeof id!=='string'||!ids.has(id))||new Set(raw).size!==raw.length)throw new Error(`settings ${name} must contain existing row IDs once`);
  return new Set(raw);
}
function staticView(visual){
  if(!visual||typeof visual!=='object')throw new Error('settings visual must be an object');
  for(const key of ['azureMotion','zoom','camera','motion'])if(visual[key])throw new Error('settings body camera is fixed; omit zoom, camera and motion instructions');
  if(visual.viewId!==undefined&&!/^[-a-zA-Z0-9_]+$/.test(visual.viewId))throw new Error('settings viewId must be a simple stable local ID');
}

/** Shared frame for an episode-authored diagram. content must be trusted,
 * self-contained SVG markup in SETTINGS_CONTENT coordinates; build-lesson
 * validates the complete custom SVG before using it. No episode 01 dependency.
 */
export function renderSettingsShell(visual={},content=''){
  staticView(visual);
  const area=oneLine(value(visual.areaLabel,'設定メニュー','areaLabel'),770,18,'areaLabel');
  const title=oneLine(value(visual.title,'設定の確認','title'),1167,26,'title');
  const kind=value(visual.kind,'設定','kind'),section=value(visual.section,'概要','section');
  const breadcrumb=oneLine(`${kind} / ${section}`,1167,14,'kind / section');
  let menu=visual.menu??[section];
  if(!Array.isArray(menu)||menu.some(m=>typeof m!=='string')||new Set(menu).size!==menu.length)throw new Error('settings menu must contain unique short labels');
  if(!menu.includes(section))menu=[...menu,section];
  if(menu.length>8)throw new Error('settings menu supports at most eight entries');
  const subtitle=oneLine(value(visual.subtitle,'設定値と関連付けを確認する','subtitle'),1167,14,'subtitle');
  const footer=oneLine(value(visual.footer,'公開設定例に基づく教材用の設定ビュー','footer'),1167,14,'footer');
  let out=rect(0,0,1207,511,'#f5f7f9','#75949f',9);
  out+=rect(0,0,1207,39,'#183844','#183844',0)+text(19,26,area,18,'#e0f4f2',650)+text(1186,26,'egakumi STUDY / 設定ビュー',15,'#b8d8dc',500,'end');
  out+=text(20,64,breadcrumb,14,'#52727d',500)+text(20,100,title,26,COLORS.ink,650)+text(21,123,subtitle,14,COLORS.muted);
  out+='<path d="M0 135H1207" stroke="#cfdde1"/>'+rect(0,136,192,346,'#eaf0f3','#d5e0e5',0)+text(18,161,'確認する設定',13,COLORS.muted,600);
  menu.forEach((label,i)=>{oneLine(label,154,16,'menu label');const y=192+i*35,on=label===section;if(on)out+=rect(9,y-23,173,32,'#d4e9e9','#c1dddd',5)+rect(9,y-23,3,32,COLORS.accent,COLORS.accent,0);out+=text(23,y,label,16,on?'#086975':'#4d6571',on?600:400);});
  out+=rect(0,483,1207,28,'#e8eff2','#d1dfe4',0)+text(18,502,footer,14,'#5a737d');
  return out+content;
}

function notice(visual){
  if(!visual.notice)return '';
  const label=oneLine(visual.notice,938,19,'notice');
  return rect(214,147,972,39,'#e8f4ff','#b6d9f3',3)+text(231,173,label,19,'#17496b',600);
}
function table(visual){
  const data=visual.table,columns=data?.columns,rows=data?.rows;
  if(!data||!Array.isArray(columns)||columns.length<2||columns.length>5)throw new Error('settings table requires two to five columns');
  const ids=uniqueRows(rows,'table'),selected=highlights(visual.highlightRows,ids,'highlightRows');
  if(visual.highlightFields?.length)throw new Error('settings table uses highlightRows, not highlightFields');
  if(columns.some(c=>!c||typeof c.label!=='string'||c.width!==undefined&&(!Number.isFinite(c.width)||c.width<100)))throw new Error('settings table columns need labels and widths of at least 100px');
  const fixed=columns.filter(c=>c.width!==undefined),remaining=columns.length-fixed.length;
  const used=fixed.reduce((sum,c)=>sum+c.width,0),share=remaining?(972-used)/remaining:0;
  if(remaining&&share<100||!remaining&&Math.abs(used-972)>.01)throw new Error('settings column widths must fit the 972px content width');
  const widths=columns.map(c=>c.width??share),top=visual.notice?199:151;
  const rowHeight=Math.min(56,Math.floor((475-top-30)/rows.length));
  if(rowHeight<40)throw new Error('settings table does not fit the content region');
  let out=notice(visual)+rect(214,top,972,30,'#edf1f5','#d6dfe7',0),x=226;
  columns.forEach((col,i)=>{out+=text(x,top+21,oneLine(col.label,widths[i]-24,16,'column heading'),16,'#586875',600);x+=widths[i];});
  rows.forEach((row,i)=>{
    if(!Array.isArray(row.cells)||row.cells.length!==columns.length)throw new Error('settings table row cells must match the columns');
    const y=top+30+i*rowHeight,on=selected.has(row.id);out+=rect(214,y,972,rowHeight,on?'#eaf4ff':'#fff','#e3e8ed',0);x=226;
    row.cells.forEach((cell,j)=>{out+=block(x,y,widths[j]-24,rowHeight,cell,j?17:18,j?COLORS.ink:COLORS.accent,`row ${row.id} cell ${j+1}`);x+=widths[j];});
    if(on)out+=outline(216,y+2,968,rowHeight-4);
  });
  return out;
}
function fields(visual){
  const rows=visual.fields,ids=uniqueRows(rows,'fields'),selected=highlights(visual.highlightFields,ids,'highlightFields');
  if(visual.highlightRows?.length)throw new Error('settings fields use highlightFields, not highlightRows');
  const top=visual.notice?205:151,height=Math.min(52,Math.floor((475-top)/rows.length));
  let out=notice(visual);
  rows.forEach((row,i)=>{
    const y=top+i*height;out+=rect(214,y,972,height,'#fff','#e0e6ec',0)+block(232,y,302,height,row.label,18,'#586875',`field ${row.id} label`)+block(558,y,609,height,row.value,20,COLORS.accent,`field ${row.id} value`);
    if(selected.has(row.id))out+=outline(550,y+4,626,height-8);
  });
  return out;
}

/** Local 1207x511 fragment, suitable for authoring a standalone SVG. */
export function renderSettingsBody(visual={}){
  staticView(visual);
  if(Boolean(visual.table)===Boolean(visual.fields))throw new Error('settings requires exactly one of table or fields');
  return renderSettingsShell(visual,visual.table?table(visual):fields(visual));
}
/** Standalone artifact for an episode-local custom SVG. */
export function renderSettingsSvg(visual={}){
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${renderSettingsBody(visual)}</svg>`;
}
/** Full lesson frame integration: branding, captions and presenters stay shared. */
export function renderSettingsContent(visual={}){
  return `<g transform="translate(65 204)">${renderSettingsBody(visual)}</g>`;
}
