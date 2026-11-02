import {renderLessonRaster as renderBaseRaster} from '../../lesson-visual.mjs';

const WIDTH=1207,HEIGHT=511;
const clamp=(v,min=0,max=1)=>Math.min(max,Math.max(min,v));
const ease=v=>{v=clamp(v);return v*v*(3-2*v);};
const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const n=v=>Number(v.toFixed(3));

export function cameraFor(focus){
  if(!focus)return {zoom:1,x:0,y:0};
  const zoom=Math.max(1,Math.min(focus.zoom??1.16,1.24,WIDTH/(focus.width+70),HEIGHT/(focus.height+64)));
  return {zoom,x:clamp(focus.x+focus.width/2-WIDTH/(2*zoom),0,WIDTH-WIDTH/zoom),y:clamp(focus.y+focus.height/2-HEIGHT/(2*zoom),0,HEIGHT-HEIGHT/zoom)};
}

export function motionAt(portal,time,previousPortal){
  const focus=portal.focus;
  const sameScreen=previousPortal?.screen===portal.screen;
  const from=cameraFor(sameScreen?previousPortal.focus:null),to=cameraFor(focus);
  const progress=ease((time-(sameScreen ? .12 : .55))/(sameScreen ? 1.05 : 1.3));
  const camera=Object.fromEntries(['zoom','x','y'].map(key=>[key,from[key]+(to[key]-from[key])*progress]));
  const destination={x:focus.x+focus.width*.72,y:focus.y+focus.height*.5};
  const previous=sameScreen?previousPortal.focus:null;
  const origin=previous?{x:previous.x+previous.width*.72,y:previous.y+previous.height*.5}:{x:118,y:Math.min(HEIGHT-70,focus.y+80)};
  const cursorProgress=ease(time/.62);
  return {camera,rectangleProgress:ease((time-.28)/.5),cursor:{x:origin.x+(destination.x-origin.x)*cursorProgress,y:origin.y+(destination.y-origin.y)*cursorProgress},clickProgress:clamp((time-.62)/.48),cursorOpacity:1-ease((time-1.16)/.28)};
}

function annotatedSvg(visual,time,previousPortal){
  const {portal}=visual,focus=portal.focus;
  if(!focus||![focus.x,focus.y,focus.width,focus.height].every(Number.isFinite)||focus.width<=0||focus.height<=0)throw new Error('A portal shot requires an explicit focus rectangle.');
  const original=Buffer.from(visual.svgDataUri.split(',')[1],'base64').toString('utf8');
  const match=original.match(/<svg\b[^>]*>([\s\S]*)<\/svg>\s*$/i);
  if(!match)throw new Error('Invalid portal source SVG.');
  const m=motionAt(portal,time,previousPortal),c=m.camera,perimeter=2*(focus.width+focus.height);
  const rectangle=`<rect x="${focus.x}" y="${focus.y}" width="${focus.width}" height="${focus.height}" rx="4" fill="#e52c32" fill-opacity="${n(.045*m.rectangleProgress)}" stroke="#e0202b" stroke-width="${n(3.5/c.zoom)}" stroke-dasharray="${perimeter}" stroke-dashoffset="${n(perimeter*(1-m.rectangleProgress))}"/>`;
  const click=m.clickProgress>0&&m.clickProgress<1?`<circle cx="${n(m.cursor.x)}" cy="${n(m.cursor.y)}" r="${n((5+18*m.clickProgress)/c.zoom)}" fill="none" stroke="#e0202b" stroke-width="${n(3/c.zoom)}" opacity="${n(1-m.clickProgress)}"/>`:'';
  const pointer=m.cursorOpacity>0?`<g transform="translate(${n(m.cursor.x)} ${n(m.cursor.y)}) scale(${n(1/c.zoom)})" opacity="${n(m.cursorOpacity)}"><path d="M0 0L0 25L7 19L12 30L17 28L12 17L22 17Z" fill="white" stroke="#202020" stroke-width="1.7"/></g>`:'';
  let notes='';
  const lines=[];
  for(const source of portal.noteLines??[]){
    let row='',units=0;
    for(const character of source){
      const size=character.charCodeAt(0)<128?.56:1;
      if(units+size>61){lines.push(row);row='';units=0;}
      row+=character;units+=size;
    }
    if(row)lines.push(row);
  }
  if(lines.length>2)throw new Error('Keep each shot to at most two short teaching-note lines.');
  if(lines.length){
    const h=lines.length*22+15,y=HEIGHT-h;
    notes=`<rect x="0" y="${y}" width="${WIDTH}" height="${h}" fill="#102638" fill-opacity=".97"/><rect x="0" y="${y}" width="5" height="${h}" fill="#efbd53"/>`+lines.map((line,i)=>`<text x="20" y="${y+25+i*22}" font-size="18" font-family="Hiragino Kaku Gothic ProN, sans-serif" fill="#fff2ce">${esc(i===0?'解説：'+line:line)}</text>`).join('');
  }
  const badge=portal.kind==='diagram'?'理解のための補助図':'教材用の再現画面';
  const badgeBackground=portal.kind==='diagram'?'#d6f1fa':'#fff4ce';
  return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}"><defs><clipPath id="portal-viewport"><rect width="${WIDTH}" height="${HEIGHT}" rx="4"/></clipPath></defs><g clip-path="url(#portal-viewport)"><g transform="scale(${n(c.zoom)}) translate(-${n(c.x)} -${n(c.y)})">${match[1]}${rectangle}${click}${pointer}</g>${notes}<rect x="1000" y="7" width="197" height="28" rx="4" fill="${badgeBackground}"/><text x="1098" y="27" font-family="Hiragino Kaku Gothic ProN, sans-serif" font-size="15" text-anchor="middle" fill="#344555">${badge}</text></g></svg>`;
}

export async function renderPortalRaster(options={}){
  if(options.phase==='body'&&options.visual?.portal){
    const svg=annotatedSvg(options.visual,options.turnTime??2,options.previousPortal);
    return renderBaseRaster({...options,visual:{...options.visual,svgDataUri:'data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64')}});
  }
  return renderBaseRaster(options);
}
