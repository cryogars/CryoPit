// Pure JS UTM <-> WGS84 --------------------------------------------
function latLonToUtm(lat,lon){
  // Transverse Mercator forward (Snyder 1987, full terms through A^6) —
  // cm-accurate across a UTM zone. Returns the LATITUDE BAND letter
  // (C..X, the "11T" style users write on sheets), which is what
  // utmToLatLon expects back; bands C–M are the southern hemisphere.
  const a=6378137,f=1/298.257223563,b=a*(1-f),e2=1-(b*b)/(a*a),ep2=e2/(1-e2);
  const zn=Math.floor((lon+180)/6)+1,lcm=(zn-1)*6-180+3;
  const k0=0.9996,lr=lat*Math.PI/180,dl=(lon-lcm)*Math.PI/180;
  const n=(a-b)/(a+b);
  const A_=a*(1-n+(5/4)*(n*n-n**3)+(81/64)*(n**4-n**5));
  const B_=(3*a/2)*(n-n**2+(7/8)*(n**3-n**4)+(55/64)*n**5);
  const C_=(15*a/16)*(n*n-n**3+(3/4)*(n**4-n**5));
  const D_=(35*a/48)*(n**3-n**4+(11/16)*n**5);
  const E_=(315*a/512)*n**4;
  const M=A_*lr-B_*Math.sin(2*lr)+C_*Math.sin(4*lr)-D_*Math.sin(6*lr)+E_*Math.sin(8*lr);
  const sL=Math.sin(lr),cL=Math.cos(lr),tL=Math.tan(lr);
  const nu=a/Math.sqrt(1-e2*sL*sL),T=tL*tL,C=ep2*cL*cL,A=dl*cL;
  const east=k0*nu*(A+(1-T+C)*A**3/6+(5-18*T+T*T+72*C-58*ep2)*A**5/120)+500000;
  const north=k0*(M+nu*tL*(A*A/2+(5-T+9*C+4*C*C)*A**4/24
              +(61-58*T+T*T+600*C-330*ep2)*A**6/720))+(lat>=0?0:10000000);
  const bands="CDEFGHJKLMNPQRSTUVWX";
  const zl=bands[Math.max(0,Math.min(19,Math.floor((lat+80)/8)))];
  return{e:Math.round(east*10)/10,n:Math.round(north*10)/10,zn,zl};
}
function utmToLatLon(e,n,zn,zl){
  // zl is the UTM latitude BAND letter (C..X): bands C-M are southern,
  // N-X northern — matching what latLonToUtm emits and what field sheets use.
  const a=6378137,f=1/298.257223563,b=a*(1-f),e2=1-(b*b)/(a*a),ep2=e2/(1-e2);
  const N0=zl.toUpperCase()<'N'?10000000:0,k0=0.9996;
  const x=e-500000,y=n-N0,lcm=((zn-1)*6-180+3)*Math.PI/180;
  const M=y/k0,mu=M/(a*(1-e2/4-3*e2*e2/64-5*e2**3/256));
  const e1=(1-Math.sqrt(1-e2))/(1+Math.sqrt(1-e2));
  const phi1=mu+(3*e1/2-27*e1**3/32)*Math.sin(2*mu)+(21*e1*e1/16-55*e1**4/32)*Math.sin(4*mu)+(151*e1**3/96)*Math.sin(6*mu)+(1097*e1**4/512)*Math.sin(8*mu);
  const sP=Math.sin(phi1),cP=Math.cos(phi1),tP=Math.tan(phi1);
  const N1=a/Math.sqrt(1-e2*sP*sP),T1=tP*tP,C1=ep2*cP*cP,R1=a*(1-e2)/Math.pow(1-e2*sP*sP,1.5);
  const D=x/(N1*k0);
  const lat=phi1-(N1*tP/R1)*(D*D/2-(5+3*T1+10*C1-4*C1*C1-9*ep2)*D**4/24+(61+90*T1+298*C1+45*T1*T1-252*ep2-3*C1*C1)*D**6/720);
  const lon=lcm+(D-(1+2*T1+C1)*D**3/6+(5-2*C1+28*T1-3*C1*C1+8*ep2+24*T1*T1)*D**5/120)/cP;
  return{lat:Math.round(lat*180/Math.PI*1e6)/1e6,lon:Math.round(lon*180/Math.PI*1e6)/1e6};
}

let _cl=false;
function onUTM(){
  if(_cl)return;
  const e=num(document.getElementById('utme').value);
  const n=num(document.getElementById('utmn').value);
  const zr=document.getElementById('utmz').value.trim();
  if(e===null||n===null||!zr)return;
  const zm=zr.match(/^(\d{1,2})([A-Za-z])$/);
  if(!zm)return;
  try{
    const r=utmToLatLon(e,n,parseInt(zm[1]),zm[2]);
    _cl=true;
    document.getElementById('lat').value=r.lat;
    document.getElementById('lon').value=r.lon;
    document.getElementById('lat-note').textContent='↑ converted from UTM';
    document.getElementById('lon-note').textContent='↑ converted from UTM';
    document.getElementById('utme-note').textContent='';
    document.getElementById('utmn-note').textContent='';
    setTimeout(()=>_cl=false,200);
  }catch(ex){}
}
function onLatLon(){
  if(_cl)return;
  const lat=num(document.getElementById('lat').value);
  const lon=num(document.getElementById('lon').value);
  if(lat===null||lon===null)return;
  try{
    const r=latLonToUtm(lat,lon);
    _cl=true;
    document.getElementById('utme').value=r.e;
    document.getElementById('utmn').value=r.n;
    document.getElementById('utmz').value=r.zn+''+r.zl;
    document.getElementById('utme-note').textContent='↑ converted from lat/lon';
    document.getElementById('utmn-note').textContent='↑ converted from lat/lon';
    document.getElementById('lat-note').textContent='';
    document.getElementById('lon-note').textContent='';
    setTimeout(()=>_cl=false,200);
  }catch(ex){}
}

