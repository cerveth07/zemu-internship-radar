/** Public company labels only; no estimates from JD text, contacts or job counts. */
const clean=value=>typeof value==='string'&&!/[\uE000-\uF8FF\uFFFD]/u.test(value)?value.trim():''

export function companyProfileURL(value,platform) {
  try {
    const host=platform==='boss'?'www.zhipin.com':'www.shixiseng.com'
    const url=new URL(value,'https://'+host)
    const pattern=platform==='boss'?/^\/gongsi\/[A-Za-z0-9_~-]{1,128}\.html$/:/^\/com\/com_[A-Za-z0-9_-]{1,128}$/
    return url.protocol==='https:'&&[host,host.slice(4)].includes(url.hostname)&&!url.username&&!url.password&&(!url.port||url.port==='443')&&pattern.test(url.pathname)
      ?'https://'+host+url.pathname:''
  }catch{return ''}
}

export function companyFacts(fields,company,sourceURL,platform) {
  if(!clean(company)||clean(fields.profile_company||fields.company)!==clean(company))return {}
  const result={},profile=companyProfileURL(fields.company_profile_url,platform)
  if(profile)result.company_profile_url=profile
  for(const field of ['company_sector','company_headcount'])if(clean(fields[field])) {
    result[field]=clean(fields[field])
    result[field+'_source_url']=clean(fields[field+'_source_url'])||sourceURL
    result[field+'_observed_at']=clean(fields[field+'_observed_at'])||new Date().toISOString()
  }
  return result
}

// Serialized into the page by both Playwright and Chrome Use. Keep self-contained.
export function readCompanyDOM({platform,mode='detail',jobId=''}) {
  const text=el=>el?.innerText?.trim()||''
  const name=el=>el?[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim():''
  const size=value=>{value=String(value||'').trim();return /^(?:(?:公司|企业|人员)?规模\s*[:：]?\s*)?(?:\d[\d,，]*(?:\.\d+)?\s*万?\s*[-–—~～至]\s*\d[\d,，]*(?:\.\d+)?\s*万?\s*人|\d[\d,，]*(?:\.\d+)?\s*万?\s*(?:人(?:以上|及以上|以下|及以下|\+)?|\+人)|少于\s*\d+\s*人)$/.test(value)?value:''}
  const firstSize=nodes=>[...nodes].map(text).map(size).find(Boolean)||''
  let company='',headcount='',sector='',profile=''
  if(platform==='boss') {
    const root=document.querySelector(mode==='company'?'.company-banner .info-primary':'.sider-company,.job-company')
    if(!root)return {}
    company=mode==='company'?name(root.querySelector('h1.name,h1')):
      text(root.querySelector('.company-info a[href*="/gongsi/"],.company-info .name,.company-name'))
    const nodes=[...root.querySelectorAll('.company-info a')]
    if(!company&&mode!=='company')company=nodes.map(text).find(v=>v&&!/^https?:/.test(v))||''
    headcount=size(text(root.querySelector('.icon-scale')?.parentElement))||firstSize(root.querySelectorAll('li,span'))
    if(!headcount)headcount=[...root.querySelectorAll('p')].flatMap(p=>[...p.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim())).map(size).find(Boolean)||''
    sector=text(root.querySelector('.icon-industry')?.parentElement)||text(root.querySelector('.industry-link'))
    profile=mode==='company'?location.href:root.querySelector('a[href*="/gongsi/"]')?.href||''
  }else {
    const root=mode==='company'?document:document.querySelector('.job-about')
    if(!root)return {}
    company=text(root.querySelector(mode==='company'?'.com-title > span:first-child':'.com-name'))
    const rows=[...root.querySelectorAll(mode==='company'?'.com-msg .com-msg_detail':'.com-detail > div')]
    headcount=size(text(root.querySelector('.iconqiyeguimo')?.parentElement))||firstSize(rows)
    sector=text(root.querySelector('.iconhangyelingyu')?.parentElement)
    if(mode==='company') {
      const index=rows.findIndex(el=>size(text(el)));if(index>=0)sector=text(rows[index+1])
      profile=location.href
    }else {
      profile=root.querySelector('a[href*="/com/com_"]')?.href||''
      // The page's own job object contains normal text when the list uses a font.
      const seen=new Set(),stack=[window.__NUXT__]
      for(let n=0;stack.length&&n<20000;n++) {
        const value=stack.pop();if(!value||typeof value!=='object'||seen.has(value))continue;seen.add(value)
        const current=value.uuid===jobId||!value.uuid&&typeof value.iname==='string'&&value.iname.trim()===text(document.querySelector('.new_job_name'))
        if(typeof value.cname==='string'&&(typeof value.iname==='string'||typeof value.info==='string')&&current) {
          if(company&&company!==value.cname.trim())continue
          if(!company)company=value.cname.trim()
          if(company===value.cname.trim()) {
            headcount=headcount||size(value.scale)
            sector=sector||(typeof value.industry==='string'?value.industry.trim():'')
            if(!profile&&/^com_[A-Za-z0-9_-]+$/.test(value.cuuid||''))profile='https://www.shixiseng.com/com/'+value.cuuid
          }
          break
        }
        for(const next of Object.values(value))if(next&&typeof next==='object')stack.push(next)
      }
    }
  }
  return {profile_company:company,company_headcount:headcount,company_sector:sector,company_profile_url:profile}
}

export function companyEnricher(platform) {
  const attempted=new Set(),known=new Map();let unavailable=false
  return async (item,{visit})=>{
    const url=companyProfileURL(item.company_profile_url,platform)
    if(!url)return
    if(item.company_headcount) {known.set(url,{company:item.company,...companyFacts({...item,profile_company:item.company},item.company,item.url,platform)});return}
    const cached=known.get(url)
    if(cached?.company===item.company){Object.assign(item,companyFacts({...cached,profile_company:cached.company},item.company,url,platform));return}
    if(unavailable||attempted.has(url))return
    attempted.add(url)
    const fields=await visit(url,item.company)
    if(fields?.company_lookup_unavailable){unavailable=true;return}
    const facts=companyFacts(fields||{},item.company,url,platform)
    if(facts.company_headcount||facts.company_sector){known.set(url,{company:item.company,...facts});Object.assign(item,facts)}
  }
}
