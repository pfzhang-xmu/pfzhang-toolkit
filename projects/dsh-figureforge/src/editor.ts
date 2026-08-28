import {Hole,Obj,TextObject,SpriteObject} from './types'
export const clamp=(n:number,a:number,b:number)=>Math.max(a,Math.min(b,n))
export const rect=(a:{x:number;y:number},b:{x:number;y:number})=>({x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),w:Math.abs(a.x-b.x),h:Math.abs(a.y-b.y)})
export function textBounds(o:TextObject,c:CanvasRenderingContext2D){c.font=`${o.bold?'700':o.thin?'300':'400'} ${o.fontSize}px ${o.fontFamily}`;const w=c.measureText(o.text).width,h=o.fontSize*1.2;return {x:o.x,y:o.y,w,h}}
export function bounds(o:Obj,c:CanvasRenderingContext2D){return o.type==='text'?textBounds(o,c):{x:o.x,y:o.y,w:o.w,h:o.h}}
export function hit(o:Obj,p:{x:number;y:number},c:CanvasRenderingContext2D){const b=bounds(o,c);return p.x>=b.x&&p.x<=b.x+b.w&&p.y>=b.y&&p.y<=b.y+b.h}
export function edgeColor(ctx:CanvasRenderingContext2D,r:{x:number;y:number;w:number;h:number}){const x=clamp(Math.floor(r.x),0,ctx.canvas.width-1),y=clamp(Math.floor(r.y),0,ctx.canvas.height-1),w=Math.max(1,Math.min(Math.floor(r.w),ctx.canvas.width-x)),h=Math.max(1,Math.min(Math.floor(r.h),ctx.canvas.height-y));const d=ctx.getImageData(x,y,w,h).data;let R=0,G=0,B=0,n=0;for(let yy=0;yy<h;yy++)for(let xx=0;xx<w;xx++)if(yy===0||xx===0||yy===h-1||xx===w-1){const i=(yy*w+xx)*4;R+=d[i];G+=d[i+1];B+=d[i+2];n++}return `rgba(${Math.round(R/n)},${Math.round(G/n)},${Math.round(B/n)},1)`}
/** Estimate the background from pixels just outside the selected region.
 * Sampling outside prevents a foreground object inside the selection from
 * becoming the fill colour of the hole. */
export function outsideColor(ctx:CanvasRenderingContext2D,r:{x:number;y:number;w:number;h:number}){
 const W=ctx.canvas.width,H=ctx.canvas.height,x=Math.floor(r.x),y=Math.floor(r.y),w=Math.max(1,Math.floor(r.w)),h=Math.max(1,Math.floor(r.h));
 const samples:number[][]=[]; const band=clamp(Math.round(Math.min(w,h)*.06),2,12);
 const add=(px:number,py:number)=>{if(px>=0&&py>=0&&px<W&&py<H){const d=ctx.getImageData(px,py,1,1).data;samples.push([d[0],d[1],d[2]])}};
 for(let i=0;i<band;i++){for(let xx=x;xx<x+w;xx+=Math.max(1,Math.floor(w/24))) {add(xx,y-1-i);add(xx,y+h+i)} for(let yy=y;yy<y+h;yy+=Math.max(1,Math.floor(h/24))){add(x-1-i,yy);add(x+w+i,yy)}}
 if(!samples.length)return edgeColor(ctx,r);
 const med=(idx:number)=>{const a=samples.map(s=>s[idx]).sort((a,b)=>a-b);return a[Math.floor(a.length/2)]};
 return `rgba(${med(0)},${med(1)},${med(2)},1)`;
}
export function cloneObj(o:Obj):Obj{return o.type==='text'?{...o}:{...o,canvas:o.canvas}}
export type Snapshot={objects:Obj[];holes:Hole[];selectedId:number|null;nextId:number}
