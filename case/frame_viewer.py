#!/usr/bin/env python3
"""The Tube Board in its frame, as a page you can turn round in 3D.

    cd case && python3 frame_viewer.py      # writes frame_viewer.html; publish that

Everything but the sled and the Pi is built in the page from a handful of numbers
(the mount border, the plug reach, the bezel), so the sliders resize the frame,
the mount, the foam and the backing live and the sizes to order follow. The sled
and the Pi are the real meshes from sled.stl and pi_dummy.stl. The renderer is the
one from ~/.claude/tools/cad_viewport.py (flat shading by true normals, back faces
culled, each triangle stroked in its own colour), extended to an assembly.

Measured or published, and used as such: the moulding (EasyFrame 364453492's
profile drawing: 20 mm face, 45 mm deep, 40 mm rebate, 6 mm lip), the mount board
(1.5 mm), the opening (346 x 195, 1 mm over the lit area), the monitor's
thickness (12 mm at the socket edge, 7 mm elsewhere, Raoul 7 Oct), the plug reach
(30 mm with right-angle connectors, Raoul 10 Oct; 40 mm straight), the panel's lit area (344 x 194, a 15.6" 16:9 panel) and its
body (368 x 225, the Amazon listing). Assumed until measured: how the bezel splits
round the picture (12 mm each side, 7 at the top, 24 at the bottom) and the spine's
width (30 mm).
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def stl_tris(name):
    """An ASCII STL as a flat list of vertex coordinates, 9 per triangle."""
    with open(os.path.join(HERE, name), "rb") as f:
        b = f.read()
    v = re.findall(rb"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)", b)
    return [round(float(c), 2) for xyz in v for c in xyz]


PAGE = r'''<title>Tube Board Frame</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Hammersmith+One&family=Work+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: the model fills the left, a measuring card on the right says what to order;
   on a phone the card follows the model. */
:root{
  --bg:#ECEFF2; --surface:#FFFFFF; --ink:#1B2129; --muted:#5A6471; --line:#D3D9E0;
  --accent:#0019A8; --accent-ink:#FFFFFF; --vp:#DDE2E7;
  --ok:#1E7B47; --warn:#9A6200; --bad:#B3261E;
  --display:"Hammersmith One","Gill Sans","Trebuchet MS",sans-serif;
  --body:"Work Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#12161B; --surface:#1A2027; --ink:#E6EAEE; --muted:#97A1AD; --line:#2B333C;
  --accent:#7D91FF; --accent-ink:#0B0F14; --vp:#0D1115;
  --ok:#5FC08A; --warn:#E3A93B; --bad:#F2867D; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#12161B; --surface:#1A2027; --ink:#E6EAEE; --muted:#97A1AD; --line:#2B333C;
  --accent:#7D91FF; --accent-ink:#0B0F14; --vp:#0D1115;
  --ok:#5FC08A; --warn:#E3A93B; --bad:#F2867D; color-scheme:dark}
*{box-sizing:border-box}
html,body{background:var(--bg);color:var(--ink)}
body{font:15px/1.5 var(--body);padding-inline:16px;padding-block:18px 40px}
.wrap{max-width:1180px;margin:0 auto;display:grid;gap:16px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px}
h1{font:400 26px/1.15 var(--display);margin:0;letter-spacing:.01em;text-wrap:balance}
.lede{color:var(--muted);margin:0;max-width:68ch}
.main{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
@media (max-width:900px){.main{grid-template-columns:minmax(0,1fr)}}
.stage{position:relative;border:1px solid var(--line);border-radius:12px;background:var(--vp);overflow:hidden;min-width:0}
canvas{display:block;width:100%;height:68vh;min-height:360px;touch-action:none;cursor:grab}
canvas:active{cursor:grabbing}
.bar{position:absolute;left:10px;right:10px;top:10px;display:flex;flex-wrap:wrap;gap:8px;pointer-events:none}
.bar>*{pointer-events:auto}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--surface)}
.seg button,.chip{font:500 13px/1 var(--body);border:0;background:var(--surface);color:var(--muted);padding:8px 11px;cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button[aria-pressed="true"],.chip[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink)}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.chip{border:1px solid var(--line);border-radius:8px}
.hint{position:absolute;left:12px;bottom:10px;color:var(--muted);font-size:12.5px}
.explode{position:absolute;right:12px;bottom:8px;display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12.5px;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:5px 10px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;gap:14px;min-width:0}
.card h2{font:400 17px/1.2 var(--display);margin:0;letter-spacing:.02em}
.order{display:grid;gap:8px}
.row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:baseline}
.row .k{color:var(--muted);font-size:13.5px}
.row .v{font:500 15px/1.3 var(--mono);font-variant-numeric:tabular-nums;text-align:right}
.big .v{font-size:19px;color:var(--ink)}
.state{display:flex;gap:8px;align-items:flex-start;font-size:13.5px;border-radius:8px;padding:9px 10px;border:1px solid var(--line)}
.state b{font-weight:600}
.state.ok{color:var(--ok)} .state.warn{color:var(--warn)} .state.bad{color:var(--bad)}
.dot{width:9px;height:9px;border-radius:50%;background:currentColor;margin-top:5px;flex:none}
.ctl{display:grid;gap:4px}
.ctl label{display:flex;justify-content:space-between;font-size:13.5px;color:var(--muted)}
.ctl label output{font:500 13.5px var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}
input[type=range]{width:100%;accent-color:var(--accent)}
.presets{display:flex;flex-wrap:wrap;gap:6px}
.sum{font-size:13.5px;color:var(--muted);margin:0}
.sum code{font:500 13px var(--mono);color:var(--ink)}
.legend{display:grid;gap:6px;margin:0;padding:0;list-style:none}
.legend button{display:grid;grid-template-columns:14px minmax(0,1fr);gap:8px;align-items:start;width:100%;
  text-align:left;font:inherit;font-size:13.5px;color:inherit;background:none;border:0;border-radius:6px;padding:3px 4px;cursor:pointer}
.sw{width:14px;height:14px;border-radius:3px;margin-top:3px;border:1px solid rgba(0,0,0,.25)}
.legend .off{opacity:.38}
.legend .nm{font-weight:600}
.legend .ds{color:var(--muted)}
.notes{color:var(--muted);font-size:13.5px;max-width:78ch;margin:0}
.notes b{color:var(--ink);font-weight:600}
@media (prefers-reduced-motion: reduce){*{scroll-behavior:auto}}
</style>
<div class="wrap">
<header>
  <h1>Tube Board Frame</h1>
  <p class="lede">The monitor face down behind the mount, in the 20&nbsp;mm brown stain frame, with the Pi on its sled behind the backing board. Drag to turn it, scroll or pinch to zoom.</p>
</header>
<div class="main">
  <div class="stage">
    <canvas id="c" aria-label="3D model of the framed board"></canvas>
    <div class="bar">
      <div class="seg" role="group" aria-label="Viewpoint">
        <button id="v-front" data-view="front">Front</button>
        <button id="v-three" data-view="three" aria-pressed="true">Corner</button>
        <button id="v-back" data-view="back">Back</button>
        <button id="v-top" data-view="top">From above</button>
      </div>
      <button class="chip" id="cut" aria-pressed="false">Cut through the plugs</button>
    </div>
    <div class="explode"><label for="ex">Pull apart</label><input id="ex" type="range" min="0" max="1" step="0.01" value="0"></div>
    <div class="hint" id="hint">drag to turn &middot; double-click to reset</div>
  </div>
  <aside class="card" aria-label="Sizes">
    <h2>What to order</h2>
    <div class="order">
      <div class="row big"><span class="k">Frame size to type in (EasyFrame)</span><span class="v" id="o-frame"></span></div>
      <div class="row"><span class="k">Mount: outside / opening</span><span class="v" id="o-mount"></span></div>
      <div class="row"><span class="k">Mount border, each side</span><span class="v" id="o-border"></span></div>
      <div class="row"><span class="k">White you see (6&nbsp;mm under the lip)</span><span class="v" id="o-white"></span></div>
      <div class="row"><span class="k">Outside of the frame</span><span class="v" id="o-outside"></span></div>
      <div class="row"><span class="k">Depth used of the 40&nbsp;mm rebate</span><span class="v" id="o-depth"></span></div>
    </div>
    <div class="state" id="state"><span class="dot"></span><span id="state-t"></span></div>
    <div class="ctl"><label for="b">Mount border <output id="b-o"></output></label><input id="b" type="range" min="30" max="70" step="0.5"></div>
    <div class="ctl"><label for="r">Plug reach past the monitor's edge <output id="r-o"></output></label><input id="r" type="range" min="15" max="50" step="0.5"></div>
    <div class="ctl"><label for="z">Bezel on the plug side <output id="z-o"></output></label><input id="z" type="range" min="4" max="20" step="0.5"></div>
    <div class="presets">
      <button class="chip" id="p-order">Your order now (55&nbsp;mm)</button>
      <button class="chip" id="p-min">Slimmest</button>
      <button class="chip" id="p-ra">Slimmest + 2&nbsp;mm spare</button>
    </div>
    <p class="sum" id="sum"></p>
    <ul class="legend" id="legend"></ul>
  </aside>
</div>
<p class="notes"><b>How the sizes relate.</b> EasyFrame's size is the inside of the frame at the back: the backing board and the mount are cut to it. The 6&nbsp;mm lip covers the outer 6&nbsp;mm of the mount, so the white you see is the border less 6. On the plug side the border also has to hold the bezel and the plugs: border &ge; bezel &minus; 1 + plug reach + 2&nbsp;mm clearance (the opening overlaps the bezel by 1&nbsp;mm). <b>Measured:</b> the moulding, the mount, the monitor's 12 and 7&nbsp;mm thickness, the 30&nbsp;mm plug reach with the right-angle connectors. <b>Assumed until you measure:</b> how the black bezel splits round the picture (12&nbsp;mm each side from the listing's 368&nbsp;mm width), so measure the plug-side bezel and set it with the slider.</p>
</div>
<script>
const SLED=__SLED__, PI=__PI__;
// ------------------------------------------------------------ the numbers
const OPEN=[346,195];          // mount opening, 1 mm over the lit area each side
const LIT=[344.2,193.6];       // the panel's lit area
const BODY=[368,225];          // the monitor's body (listing)
const BEZ_TOP=7;               // assumed: the rest of the 31 mm goes to the chin
const THIN=7, SPINE=12, SPINE_W=30, MOUNT_T=1.5, BACK_T=3;
const FACE=20, LIP=6, DEPTH=45, REBATE=40;
const SLED_H=14.9;             // sled, Pi and its GPIO header
const PRESET={order:{b:55,r:30,z:12}};
let P={...PRESET.order};
const MAT={frame:'#5A3A26',mount:'#FBFBF9',body:'#212328',screen:'#0B1424',foam:'#F5F7F8',
  backing:'#B48858',brass:'#C9A24A',sled:'#2E3136',pcb:'#1E7A3C',plug:'#141414',cable:'#1A1A1A',
  cork:'#B78A5C',pic:'#0019A8',row:'#E9EBEE',min:'#F5A623'};
const PARTS=[
 {id:'frame',name:'Frame',mat:'frame',layer:0,desc:'20 mm brown stain, 45 mm deep, 40 mm rebate, 6 mm lip'},
 {id:'mount',name:'Mount',mat:'mount',layer:1,desc:'white, 1.5 mm board, presses on the back of the lip'},
 {id:'monitor',name:'Monitor',mat:'body',layer:2,desc:'face down on the mount, 7 mm thick, 12 mm at the socket edge'},
 {id:'plugs',name:'Plugs and leads',mat:'plug',layer:2,desc:'mini-HDMI and USB-C out of the right-hand edge'},
 {id:'foam',name:'Foam board',mat:'foam',layer:3,desc:'two layers of 5 mm round the monitor, so the backing presses flat'},
 {id:'backing',name:'Backing board',mat:'backing',layer:4,desc:'3 mm MDF, a notch at the right for the leads'},
 {id:'buttons',name:'Turn buttons',mat:'brass',layer:5,desc:'screwed into the rebate wall, hold the backing'},
 {id:'pi',name:'Pi on its sled',mat:'pcb',layer:6,desc:'on the back of the backing, 14.9 mm deep, inside the frame'},
 {id:'cork',name:'Cork bumpers',mat:'cork',layer:-1,desc:'5 mm, hold the frame off the wall for the leads and air'},
];
const hidden=new Set();
// ------------------------------------------------------------ geometry
// Assembly axes: x right and y up as you face the board, z toward you. The frame's
// back is z=0 and its face z=45. Everything is a convex prism (a plan polygon with
// a z range) so a cut is just a polygon clip; the sled and Pi are meshes.
function clipY(poly,yc){const out=[];for(let i=0;i<poly.length;i++){const a=poly[i],b=poly[(i+1)%poly.length];
  const ia=a[1]<=yc,ib=b[1]<=yc;if(ia)out.push(a);
  if(ia!==ib){const t=(yc-a[1])/(b[1]-a[1]);out.push([a[0]+t*(b[0]-a[0]),yc])}}return out}
function prism(poly,z0,z1,mat,part,T){if(poly.length<3)return;
  const cx=poly.reduce((s,p)=>s+p[0],0)/poly.length,cy=poly.reduce((s,p)=>s+p[1],0)/poly.length,cz=(z0+z1)/2;
  const add=(a,b,c)=>{const ux=b[0]-a[0],uy=b[1]-a[1],uz=b[2]-a[2],vx=c[0]-a[0],vy=c[1]-a[1],vz=c[2]-a[2];
    const nx=uy*vz-uz*vy,ny=uz*vx-ux*vz,nz=ux*vy-uy*vx;
    const mx=(a[0]+b[0]+c[0])/3-cx,my=(a[1]+b[1]+c[1])/3-cy,mz=(a[2]+b[2]+c[2])/3-cz;
    T.push({v:(nx*mx+ny*my+nz*mz)>=0?[a,b,c]:[a,c,b],mat,part})};
  const top=poly.map(p=>[p[0],p[1],z1]),bot=poly.map(p=>[p[0],p[1],z0]);
  for(let i=1;i<poly.length-1;i++){add(top[0],top[i],top[i+1]);add(bot[0],bot[i],bot[i+1])}
  for(let i=0;i<poly.length;i++){const j=(i+1)%poly.length;add(bot[i],bot[j],top[j]);add(bot[i],top[j],top[i])}}
function box(x0,x1,y0,y1,z0,z1,mat,part,T,yc){let p=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]];
  if(yc!=null)p=clipY(p,yc);prism(p,Math.min(z0,z1),Math.max(z0,z1),mat,part,T)}
function ring(ox,oy,ix,iy,z0,z1,mat,part,T,yc){
  const bars=[[[-ox,-oy],[ox,-oy],[ix,-iy],[-ix,-iy]],[[ox,-oy],[ox,oy],[ix,iy],[ix,-iy]],
              [[ox,oy],[-ox,oy],[-ix,iy],[ix,iy]],[[-ox,oy],[-ox,-oy],[-ix,-iy],[-ix,iy]]];
  for(const b of bars)prism(yc!=null?clipY(b,yc):b,z0,z1,mat,part,T)}
function mesh(flat,place,mat,part,T,yc){
  // sled coordinates: x along the 73 mm side, y along 64, z up off the backing.
  // Placed lying on the back of the backing, so its z runs toward the wall (mirror)
  // and the winding is reversed to keep the faces outward.
  for(let i=0;i<flat.length;i+=9){const q=[];for(let k=0;k<3;k++)q.push(place(flat[i+3*k],flat[i+3*k+1],flat[i+3*k+2]));
    if(yc!=null&&(q[0][1]+q[1][1]+q[2][1])/3>yc)continue;T.push({v:[q[0],q[2],q[1]],mat,part})}}
function build(cut){
  const T=[],B=P.b,R=P.r,Z=P.z;
  const IX=OPEN[0]/2+B,IY=OPEN[1]/2+B;                 // inside of the frame (half sizes)
  const OX=IX+FACE-LIP,OY=IY+FACE-LIP;                 // outside of the frame
  const SX=IX-LIP,SY=IY-LIP;                           // sight: what the lip leaves open
  // the monitor, placed by its lit area, which sits 1 mm inside the opening
  const right=LIT[0]/2+Z, left=-(BODY[0]-LIT[0]-Z)-LIT[0]/2;
  const top=LIT[1]/2+BEZ_TOP, bottom=top-BODY[1];
  const zm=REBATE-MOUNT_T, zf=zm, zb=zf-THIN, zs=zf-SPINE, zk=zs-BACK_T;
  const plugY=[(top+bottom)/2+12,(top+bottom)/2-12];   // mini-HDMI above, USB-C below
  const yc=cut?plugY[0]:null;
  const tip=right+R;
  // frame: the back of the profile, then the lip
  ring(OX,OY,IX,IY,0,REBATE,'frame','frame',T,yc);
  ring(OX,OY,SX,SY,REBATE,DEPTH,'frame','frame',T,yc);
  // mount
  ring(IX,IY,OPEN[0]/2,OPEN[1]/2,zm,REBATE,'mount','mount',T,yc);
  // monitor: body, spine along the socket edge, the lit panel, a board on it
  box(left,right,bottom,top,zb,zf,'body','monitor',T,yc);
  box(right-SPINE_W,right,bottom,top,zs,zb,'body','monitor',T,yc);
  const lx=LIT[0]/2,ly=LIT[1]/2,zp=zf+0.4;
  box(-lx,lx,-ly,ly,zf,zp,'screen','monitor',T,yc);
  const u=LIT[0]/100,zr=zp+0.15;
  box(-lx+2.5*u,lx-2.5*u,ly-9.6*u,ly-9.4*u,zp,zr,'pic','monitor',T,yc);        // the line-colour rule
  for(const col of [0,1])for(let j=0;j<4;j++){const x0=-lx+2.5*u+col*(LIT[0]/2+1.2*u),w=LIT[0]/2-4.5*u,
    y=ly-17.5*u-j*7.1*u;box(x0+2*u,x0+w*0.62,y-1.1*u,y+1.1*u,zp,zr,'row','monitor',T,yc);
    box(x0+w*0.78,x0+w,y-1.1*u,y+1.1*u,zp,zr,'min','monitor',T,yc)}
  // plugs and leads: mini-HDMI to the Pi, USB-C out to the power brick
  const zpl=zb+THIN/2;
  box(right,tip,plugY[0]-5.5,plugY[0]+5.5,zpl-3,zpl+3,'plug','plugs',T,yc);
  box(right,tip,plugY[1]-4.5,plugY[1]+4.5,zpl-2.5,zpl+2.5,'plug','plugs',T,yc);
  const cx=Math.min(tip,IX-4)-3;                         // down through the backing's notch
  // the Pi on its sled, connectors toward the notch, below the plugs
  const sx=IX-48, sy=plugY[1]-14-73;                     // room for a full-size HDMI plug
  const place=(x,y,z)=>[sx-y,sy+x,zk-z];
  mesh(SLED,place,'sled','pi',T,yc);mesh(PI,place,'pcb','pi',T,yc);
  const piHdmi=sy+4+32,piUsb=sy+4+10.6,zc=zk-9;
  box(cx-2.5,cx+2.5,plugY[0]-2.5,plugY[0]+2.5,zc,zpl-3,'cable','plugs',T,yc);
  box(cx-2.5,cx+2.5,piHdmi-2.5,plugY[0]+2.5,zc-2.5,zc+2.5,'cable','plugs',T,yc);
  box(sx+2,cx+2.5,piHdmi-2.5,piHdmi+2.5,zc-2.5,zc+2.5,'cable','plugs',T,yc);
  box(cx+1,cx+5,plugY[1]-2,plugY[1]+2,-3,zpl-2.5,'cable','plugs',T,yc);
  box(cx+1,cx+5,-OY-40,plugY[1]+2,-5,-1,'cable','plugs',T,yc);
  box(sx+2,cx-3,piUsb-2,piUsb+2,zc-4,zc,'cable','plugs',T,yc);
  box(cx-7,cx-3,-OY-40,piUsb+2,-5,-1,'cable','plugs',T,yc);
  box(cx-7,cx-3,piUsb-2,piUsb+2,-5,zc,'cable','plugs',T,yc);
  // foam board round the monitor, clear of the plugs
  const g=0.5,fl=left-1,fr=right+1,ft=top+1,fb=bottom-1;
  box(-IX+g,fl,-IY+g,IY-g,zs,zf,'foam','foam',T,yc);
  box(fl,IX-g,ft,IY-g,zs,zf,'foam','foam',T,yc);
  box(fl,IX-g,-IY+g,fb,zs,zf,'foam','foam',T,yc);
  box(fr,IX-g,plugY[0]+12,ft,zs,zf,'foam','foam',T,yc);
  box(fr,IX-g,fb,plugY[1]-12,zs,zf,'foam','foam',T,yc);
  // backing board, notched at the right for the leads
  const nT=plugY[0]+10,nB=plugY[1]-10,nD=12;
  box(-IX+g,IX-g-nD,-IY+g,IY-g,zk,zs,'backing','backing',T,yc);
  box(IX-g-nD,IX-g,nT,IY-g,zk,zs,'backing','backing',T,yc);
  box(IX-g-nD,IX-g,-IY+g,nB,zk,zs,'backing','backing',T,yc);
  // turn buttons, on the back of the backing, screwed into the rebate wall
  for(const x of [-IX*.55,IX*.55]){box(x-7,x+7,IY-11,IY,zk-1.6,zk,'brass','buttons',T,yc);
    box(x-7,x+7,-IY,-IY+11,zk-1.6,zk,'brass','buttons',T,yc)}
  box(-IX,-IX+11,-7,7,zk-1.6,zk,'brass','buttons',T,yc);
  box(IX-11,IX,IY*.6-7,IY*.6+7,zk-1.6,zk,'brass','buttons',T,yc);
  // cork bumpers on the frame's back corners
  for(const sx2 of [-1,1])for(const sy2 of [-1,1])box(sx2*(OX-18)-7,sx2*(OX-18)+7,sy2*(OY-18)-7,sy2*(OY-18)+7,-5,0,'cork','cork',T,yc);
  return {T,IX,IY,OX,OY,right,tip,zk};
}
// ------------------------------------------------------------ rendering
const cv=document.getElementById('c');
let cut=false,explode=0,dirty=true,rebuild=true,G=build(false);
const VIEWS={front:[0,0],three:[-0.62,0.32],back:[Math.PI+0.5,0.25],top:[-0.12,1.2]};
let yaw=VIEWS.three[0],pitch=VIEWS.three[1],zoom=1;
const Lt=(()=>{const v=[-0.45,-0.62,0.64],m=Math.hypot(...v);return v.map(x=>x/m)})();
function tok(n){return getComputedStyle(document.documentElement).getPropertyValue(n).trim()}
function rgb(hex){const n=parseInt(hex.slice(1),16);return[((n>>16)&255)/255,((n>>8)&255)/255,(n&255)/255]}
function cssRGB(c){const d=document.createElement('div');d.style.color=c;document.body.appendChild(d);
  const m=getComputedStyle(d).color.match(/[\d.]+/g)||[0,0,0];d.remove();return m.slice(0,3).map(x=>+x/255)}
const layerOf={};for(const p of PARTS)layerOf[p.id]=p.layer;
const dzOf=part=>explode*(layerOf[part]>=0?-48*layerOf[part]:-48*6.6);
// The view: assembly (x right, y up, z toward you) turned by yaw about the vertical
// and pitch about the horizontal, seen straight on (orthographic, like a drawing).
function rows(){const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch),c=DEPTH/2;
  const sx=[cy,0,sy,-c*sy],y1=[sy,0,-cy,c*cy];
  return{sx,dep:y1.map((v,i)=>cp*v+(i===1?-sp:0)),up:y1.map((v,i)=>sp*v+(i===1?cp:0))}}
const gl=cv.getContext('webgl',{antialias:true})||cv.getContext('experimental-webgl');
let draw;
if(gl){
  const vs=`attribute vec3 aP,aN,aC;uniform vec4 uX,uY,uZ;uniform vec3 nX,nD,nU,uL;varying vec3 vC;
    void main(){vec4 p=vec4(aP,1.0);gl_Position=vec4(dot(uX,p),dot(uY,p),dot(uZ,p),1.0);
    vec3 n=vec3(dot(nX,aN),dot(nD,aN),dot(nU,aN));float l=max(0.0,dot(n,uL));
    if(n.y>0.0)l=max(0.0,dot(-n,uL))*0.5;vC=min(aC*(0.42+0.66*l),vec3(1.0));}`;
  const fs=`precision mediump float;varying vec3 vC;void main(){gl_FragColor=vec4(vC,1.0);}`;
  const sh=(t,src)=>{const o=gl.createShader(t);gl.shaderSource(o,src);gl.compileShader(o);return o};
  const pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));
  gl.linkProgram(pr);gl.useProgram(pr);
  const loc=n=>gl.getUniformLocation(pr,n),buf=gl.createBuffer();let count=0;
  function upload(){rebuild=false;const out=[];
    for(const t of G.T){if(hidden.has(t.part))continue;const dz=dzOf(t.part),c=rgb(MAT[t.mat]);
      const a=t.v[0],b=t.v[1],d=t.v[2];
      const ux=b[0]-a[0],uy=b[1]-a[1],uz=b[2]-a[2],vx=d[0]-a[0],vy=d[1]-a[1],vz=d[2]-a[2];
      let nx=uy*vz-uz*vy,ny=uz*vx-ux*vz,nz=ux*vy-uy*vx;const m=Math.hypot(nx,ny,nz)||1;nx/=m;ny/=m;nz/=m;
      for(const q of t.v)out.push(q[0],q[1],q[2]+dz,nx,ny,nz,c[0],c[1],c[2])}
    count=out.length/9;gl.bindBuffer(gl.ARRAY_BUFFER,buf);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(out),gl.STATIC_DRAW);
    const st=36;[['aP',0],['aN',12],['aC',24]].forEach(([n,o])=>{const l=gl.getAttribLocation(pr,n);
      gl.enableVertexAttribArray(l);gl.vertexAttribPointer(l,3,gl.FLOAT,false,st,o)})}
  draw=function(){dirty=false;if(rebuild)upload();
    const dpr=devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;
    if(cv.width!==Math.round(w*dpr)||cv.height!==Math.round(h*dpr)){cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr)}
    gl.viewport(0,0,cv.width,cv.height);const bg=cssRGB(tok('--vp'));gl.clearColor(bg[0],bg[1],bg[2],1);
    gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LESS);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    const span=Math.max(G.OX*2,G.OY*2)*1.18,k=Math.min(w,h*1.25)*0.86/span*zoom,R=rows();
    gl.uniform4fv(loc('uX'),R.sx.map(v=>v*2*k/w));gl.uniform4fv(loc('uY'),R.up.map(v=>v*2*k/h));
    gl.uniform4fv(loc('uZ'),R.dep.map(v=>v/1500));
    gl.uniform3fv(loc('nX'),R.sx.slice(0,3));gl.uniform3fv(loc('nD'),R.dep.slice(0,3));gl.uniform3fv(loc('nU'),R.up.slice(0,3));
    gl.uniform3fv(loc('uL'),[Lt[0],Lt[1],Lt[2]]);gl.drawArrays(gl.TRIANGLES,0,count)};
}else{
  // no WebGL: the painter's renderer from cad_viewport.py
  const ctx=cv.getContext('2d');
  const shade=(hex,k)=>{const n=parseInt(hex.slice(1),16);
    return`rgb(${Math.min(255,((n>>16)&255)*k)|0},${Math.min(255,((n>>8)&255)*k)|0},${Math.min(255,(n&255)*k)|0})`};
  draw=function(){dirty=false;rebuild=false;
    const dpr=devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;
    if(cv.width!==Math.round(w*dpr)||cv.height!==Math.round(h*dpr)){cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr)}
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle=tok('--vp');ctx.fillRect(0,0,w,h);
    const span=Math.max(G.OX*2,G.OY*2)*1.18,k=Math.min(w,h*1.25)*0.86/span*zoom,R=rows();
    const dot=(r,p)=>r[0]*p[0]+r[1]*p[1]+r[2]*p[2]+r[3];const Q=[];
    for(const t of G.T){if(hidden.has(t.part))continue;const dz=dzOf(t.part);
      const q=t.v.map(p=>{const pp=[p[0],p[1],p[2]+dz];return[dot(R.sx,pp),dot(R.dep,pp),dot(R.up,pp)]});
      const ax=q[1][0]-q[0][0],ay=q[1][1]-q[0][1],az=q[1][2]-q[0][2],bx=q[2][0]-q[0][0],by=q[2][1]-q[0][1],bz=q[2][2]-q[0][2];
      const nx=ay*bz-az*by,ny=az*bx-ax*bz,nz=ax*by-ay*bx;if(ny>=0)continue;
      const nm=Math.hypot(nx,ny,nz)||1,lam=Math.max(0,(nx*Lt[0]+ny*Lt[1]+nz*Lt[2])/nm);
      Q.push({q,d:(q[0][1]+q[1][1]+q[2][1])/3,l:.42+.66*lam,c:MAT[t.mat]})}
    Q.sort((a,b)=>b.d-a.d);ctx.lineJoin='round';ctx.lineWidth=0.9;
    for(const t of Q){const f=shade(t.c,t.l);ctx.fillStyle=f;ctx.strokeStyle=f;ctx.beginPath();
      ctx.moveTo(t.q[0][0]*k+w/2,h/2-t.q[0][2]*k);ctx.lineTo(t.q[1][0]*k+w/2,h/2-t.q[1][2]*k);
      ctx.lineTo(t.q[2][0]*k+w/2,h/2-t.q[2][2]*k);ctx.closePath();ctx.fill();ctx.stroke()}};
}
function loop(){if(dirty)draw();requestAnimationFrame(loop)}
// ------------------------------------------------------------ controls
const $=id=>document.getElementById(id);
const f1=x=>(Math.round(x*10)/10).toString();
function update(){
  G=build(cut);dirty=true;rebuild=true;
  const W=OPEN[0]+2*P.b,H=OPEN[1]+2*P.b,clear=P.b-(P.z-1)-P.r;
  $('o-frame').textContent=`${f1(W)} \u00d7 ${f1(H)} mm`;
  $('o-mount').textContent=`${f1(W)} \u00d7 ${f1(H)} / ${OPEN[0]} \u00d7 ${OPEN[1]}`;
  $('o-border').textContent=`${f1(P.b)} mm`;
  $('o-white').textContent=`${f1(P.b-LIP)} mm`;
  $('o-outside').textContent=`${f1(W+2*(FACE-LIP))} \u00d7 ${f1(H+2*(FACE-LIP))} mm`;
  const used=MOUNT_T+SPINE+BACK_T+SLED_H;
  $('o-depth').textContent=`${f1(used)} mm`;
  const st=$('state'),t=$('state-t');
  st.className='state '+(clear>=2?'ok':clear>=0?'warn':'bad');
  t.innerHTML=clear>=2?`<b>The plugs fit.</b> ${f1(clear)} mm between the plug ends and the wood.`
    :clear>=0?`<b>Tight.</b> Only ${f1(clear)} mm between the plug ends and the wood; the leads will press on it.`
    :`<b>The plugs do not fit.</b> They reach ${f1(-clear)} mm into the wood. Make the border at least ${f1(P.z-1+P.r+2)} mm.`;
  $('b-o').textContent=`${f1(P.b)} mm`;$('r-o').textContent=`${f1(P.r)} mm`;$('z-o').textContent=`${f1(P.z)} mm`;
  $('sum').innerHTML=`Plug side: bezel ${f1(P.z)} \u2212 1 + plugs ${f1(P.r)} + clearance 2 = <code>${f1(P.z-1+P.r+2)} mm</code> slimmest border. The other three sides match it, so the picture stays centred.`;
  for(const [id,key] of [['b','b'],['r','r'],['z','z']])$(id).value=P[key];
}
for(const [id,key] of [['b','b'],['r','r'],['z','z']])$(id).addEventListener('input',e=>{P[key]=+e.target.value;update()});
$('p-order').onclick=()=>{P={...PRESET.order};update()};
$('p-min').onclick=()=>{P.b=P.z-1+P.r+2;update()};
$('p-ra').onclick=()=>{P.b=P.z-1+P.r+4;update()};
$('cut').onclick=e=>{cut=!cut;e.currentTarget.setAttribute('aria-pressed',cut);update();
  if(cut){[yaw,pitch]=VIEWS.top;setView('top')}};
$('ex').addEventListener('input',e=>{explode=+e.target.value;dirty=true;rebuild=true});
function setView(v){[yaw,pitch]=VIEWS[v];zoom=1;dirty=true;
  document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.view===v))}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
const lg=$('legend');
for(const p of PARTS){const li=document.createElement('li');li.innerHTML=
  `<button type="button" aria-pressed="true" id="lg-${p.id}" title="Show or hide"><span class="sw" style="background:${MAT[p.mat]}"></span><span><span class="nm">${p.name}</span><br><span class="ds">${p.desc}</span></span></button>`;
  lg.appendChild(li);
  li.querySelector('button').onclick=e=>{const on=hidden.has(p.id);on?hidden.delete(p.id):hidden.add(p.id);
    e.currentTarget.setAttribute('aria-pressed',on);li.classList.toggle('off',!on);dirty=true;rebuild=true}}
let px=0,py=0,down=false,pinch=0;
cv.addEventListener('pointerdown',e=>{down=true;px=e.clientX;py=e.clientY;cv.setPointerCapture(e.pointerId)});
cv.addEventListener('pointermove',e=>{if(!down)return;yaw-=(e.clientX-px)*.01;pitch+=(e.clientY-py)*.01;
  px=e.clientX;py=e.clientY;dirty=true;document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',false))});
cv.addEventListener('pointerup',()=>down=false);
cv.addEventListener('wheel',e=>{e.preventDefault();zoom*=e.deltaY<0?1.08:.93;zoom=Math.max(.4,Math.min(8,zoom));dirty=true},{passive:false});
cv.addEventListener('dblclick',()=>setView('three'));
cv.addEventListener('touchmove',e=>{if(e.touches.length===2){e.preventDefault();
  const d=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);
  if(pinch)zoom=Math.max(.4,Math.min(8,zoom*d/pinch));pinch=d;dirty=true}},{passive:false});
cv.addEventListener('touchend',()=>pinch=0);
new MutationObserver(()=>dirty=true).observe(document.documentElement,{attributes:true});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>dirty=true);
addEventListener('resize',()=>dirty=true);
update();loop();
</script>'''


def main():
    out = (PAGE.replace("__SLED__", json.dumps(stl_tris("sled.stl"), separators=(",", ":")))
               .replace("__PI__", json.dumps(stl_tris("pi_dummy.stl"), separators=(",", ":"))))
    path = os.path.join(HERE, "frame_viewer.html")
    with open(path, "w") as f:
        f.write(out)
    print(f"{path}: {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    main()
