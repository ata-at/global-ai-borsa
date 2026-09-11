self.addEventListener("install",e=>self.skipWaiting());
self.addEventListener("activate",e=>e.waitUntil(self.clients.claim()));
self.addEventListener("push",e=>{
 let d={title:"Global AI Borsa",body:"Yeni sinyal.",url:"/"};
 try{d=JSON.parse(e.data.text())}catch(_){}
 e.waitUntil(self.registration.showNotification(d.title,{body:d.body,icon:"icon.svg",badge:"icon.svg",data:{url:d.url}}));
});
self.addEventListener("notificationclick",e=>{
 e.notification.close();
 e.waitUntil(clients.matchAll({type:"window",includeUncontrolled:true}).then(cs=>{
   if(cs.length)return cs[0].focus();
   return clients.openWindow(e.notification.data?.url||"/");
 }));
});