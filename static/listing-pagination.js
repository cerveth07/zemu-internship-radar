/* Shared, view-only pagination for local job rows and company cards.
   The filtered arrays always remain complete for export and analysis. */
const listingPageSizes = Object.freeze({
 jobs: [20, 50, 100],
 companies: [12, 24, 48]
});
function listingPageState(kind) {
 return state.listPagination[kind];
}
function setListingPage(kind, nextPage) {
 const record=listingPageState(kind);
 if (!record) return;
 const page=Number(nextPage);
 if(!Number.isSafeInteger(page)||page<1)return;
 record.page=page;
 render();
 const scroll=document.querySelector('#content');
 if(scroll){scroll.scrollTop=0;scroll.focus?.({preventScroll:true});}
}
function setListingSize(kind, nextSize) {
 const record=listingPageState(kind);
 const size=Number(nextSize);
 if(!record||!listingPageSizes[kind].includes(size))return;
 record.size=size;record.page=1;render();
}
function resetListingPage(kind) {
 if(state.listPagination[kind])state.listPagination[kind].page=1;
}
function listingPage(kind, allItems) {
 const record=listingPageState(kind);
 const sizes=listingPageSizes[kind];
 const size=sizes.includes(record.size)?record.size:sizes[0];
 const total=allItems.length, pages=Math.max(1,Math.ceil(total/size));
 const page=Math.max(1,Math.min(pages,Math.floor(Number(record.page)||1)));
 record.page=page;record.size=size;
 const start=(page-1)*size;
 return {items:allItems.slice(start,start+size),total,size,page,pages,
         from:total?start+1:0,to:Math.min(start+size,total)};
}
function listingPageButtons(kind,info) {
 if(info.pages<2)return '';
 const pages=new Set([1,info.pages]);
 for(let n=Math.max(1,info.page-2);n<=Math.min(info.pages,info.page+2);n++)pages.add(n);
 const ordered=[...pages].sort((a,b)=>a-b);
 let result='',previous=0;
 for(const n of ordered){
  if(previous&&n-previous>1)result+='<span class="listing-ellipsis" aria-hidden="true">…</span>';
  result+='<button type="button" class="btn btn-small listing-page-number'+
   (n===info.page?' listing-current-page':'')+'" data-listing-kind="'+kind+'" data-listing-page="'+n+
   '" aria-label="第 '+n+' 页"'+(n===info.page?' aria-current="page"':'')+'>'+n+'</button>';
  previous=n;
 }
 return result;
}
function listingPager(kind,info,unit){
 if(info.total<=listingPageSizes[kind][0])return '';
 const options=listingPageSizes[kind].map(n=>'<option value="'+n+'"'+(info.size===n?' selected':'')+'>'+n+' 条</option>').join('');
 return '<nav class="listing-pagination" aria-label="'+(kind==='jobs'?'岗位库':'企业库')+'分页">'+
  '<span class="listing-range" role="status">显示 '+info.from+'–'+info.to+' / 共 '+info.total+' '+unit+'</span>'+
  '<div class="listing-pager-controls">'+
  '<label class="listing-size-label" for="listing-size-'+kind+'">每页</label>'+
  '<select class="select listing-size-select" id="listing-size-'+kind+'" data-listing-size="'+kind+'">'+options+'</select>'+
  '<button class="btn btn-small" type="button" data-listing-page="'+Math.max(1,info.page-1)+'" '+
     'data-listing-kind="'+kind+'" aria-label="上一页" '+(info.page===1?'disabled':'')+'>上一页</button>'+
  '<div class="listing-pages" aria-label="页码">'+listingPageButtons(kind,info)+'</div>'+
  '<button class="btn btn-small" type="button" data-listing-page="'+Math.min(info.pages,info.page+1)+'" '+
     'data-listing-kind="'+kind+'" aria-label="下一页" '+(info.page===info.pages?'disabled':'')+'>下一页</button>'+
  '<span class="listing-page-total">第 '+info.page+' / '+info.pages+' 页</span></div></nav>';
}

document.addEventListener('click',event=>{
 const button=event.target.closest('button[data-listing-page]');
 if(button&&!button.disabled)setListingPage(button.dataset.listingKind,button.dataset.listingPage);
});
document.addEventListener('change',event=>{
 if(event.target.matches('select[data-listing-size]'))setListingSize(event.target.dataset.listingSize,event.target.value);
});
