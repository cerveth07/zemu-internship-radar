/** Explicit job-list matching and BOSS favorites. The only click is btn-interest.
 * All navigation, login waits and task-owned browser cleanup reuse the collector.
 */
import {existsSync} from 'node:fs'
import {readFile,writeFile,rename} from 'node:fs/promises'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {createAdapter,openBrowser,jobURL,searchURL,cityMatches,ensureReadable} from './boss_runner.mjs'

const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms))
export const normalize=value=>String(value||'').normalize('NFKC').replace(/\s+/gu,'').toLowerCase()
const unavailable=message=>Object.assign(new Error(message),{fatal:true})

export function validate(config){
  const p=config?.preferences
  if(!/^[a-f0-9]{32}$/.test(config?.task_id)||!p||!['match','favorite'].includes(p.action)
    ||!['managed','chrome_use'].includes(p.browser_mode)||!Array.isArray(p.targets)||!p.targets.length||p.targets.length>60
    ||new Set(p.targets.map(t=>t.id)).size!==p.targets.length
    ||p.targets.some(t=>!Number.isSafeInteger(t.id)||t.id<=0
      ||typeof t.title!=='string'||t.title.length>2000||typeof t.company!=='string'||t.company.length>2000
      ||typeof t.city!=='string'||t.city.length>2000
      ||(p.action==='favorite'?!jobURL(t.url):t.source_url&&!jobURL(t.source_url))))
    throw new Error('岗位收藏任务格式无效')
  return p
}

export function favoriteDOM(){
  const visible=el=>el.getClientRects().length>0&&getComputedStyle(el).visibility!=='hidden'
  const controls=Array.from(document.querySelectorAll('a.btn-interest,button.btn-interest')).filter(visible)
    .filter(el=>!/(?:chat|greet|contact|apply|deliver)/i.test(el.className+' '+(el.getAttribute('href')||'')))
    .map(el=>({label:(el.innerText||el.textContent||'').trim(),href:el.getAttribute('href')||''}))
    .filter(el=>!el.href||/^(?:#|javascript:(?:;|void\(0\);?)?)$/.test(el.href))
  const saved=controls.filter(el=>['取消感兴趣','已收藏','取消收藏'].includes(el.label))
  const available=controls.filter(el=>['感兴趣','收藏'].includes(el.label))
  return {url:location.href,title:document.querySelector('h1')?.innerText?.trim()||'',
    state:saved.length===1&&available.length===0?'saved':available.length===1&&saved.length===0?'available':'unavailable',
    label:available[0]?.label||'',count:controls.length}
}

export async function clickFavorite(page,url){
  const before=await page.evaluate(favoriteDOM)
  if(jobURL(before.url)!==url||before.state!=='available'||before.count!==1)
    throw new Error('未找到唯一的未收藏按钮，未操作其他控件')
  if(typeof page.request==='function'){
    // Use a fresh accessibility reference rather than eval(click). Neither
    // recruiter links nor messaging controls can pass these exact-name guards.
    const snapshot=await page.request(['snapshot'])
    if(jobURL(snapshot.origin)!==url)throw unavailable('页面已变化，未执行收藏')
    const refs=Object.entries(snapshot.refs||{}).filter(([,ref])=>['link','button'].includes(ref.role)&&ref.name===before.label)
    if(refs.length!==1||!/^e\d+$/.test(refs[0][0]))throw new Error('收藏按钮未能唯一定位，请重试；未点击其他控件')
    await page.request(['click','@'+refs[0][0]])
  }else{
    const button=page.locator('a.btn-interest:visible,button.btn-interest:visible').filter({hasText:new RegExp('^\\s*'+before.label+'\\s*$')})
    if(await button.count()!==1)throw new Error('收藏入口发生变化，未操作其他按钮')
    await button.click({timeout:10000})
  }
}

export function autoSelection(target,candidates){
  const active=candidates.filter(c=>c.availability==='active')
  if(target.source_url)return active.length===1&&active[0].favorite_status!=='already_saved'?[active[0].url]:[]
  const exact=active.filter(c=>target.company&&normalize(target.company)===normalize(c.company)
    &&normalize(target.title)===normalize(c.title)&&cityMatches(c.city,target.city))
  return exact.length===1&&exact[0].favorite_status!=='already_saved'?[exact[0].url]:[]
}

export async function run(config,dependencies={}){
  const prefs=validate(config),adapter=dependencies.adapter||createAdapter()
  const state={phase:'starting',message:'正在打开 BOSS 岗位页面',total:prefs.targets.length,processed:0,
    detail_attempts:0,list_attempts:0,saved:0,already_saved:0,closed:0,failed:0,browser_mode:prefs.browser_mode,
    cleanup_status:'complete',cleanup_message:''}
  const output={task_id:config.task_id,run_id:config.task_id,status:'running',items:[],results:[],total:0,
    heartbeat:Date.now()/1000,error:'',finished_at:null,platform_state:{boss:state}}
  let writes=Promise.resolve(),writeError,browser,page,stopping=false
  const persist=()=>{
    output.heartbeat=Date.now()/1000
    const encoded=JSON.stringify(output)
    writes=writes.then(async()=>{await writeFile(config.outputPath+'.tmp',encoded,'utf8');await rename(config.outputPath+'.tmp',config.outputPath)})
      .catch(error=>{writeError||=unavailable('无法保存岗位收藏进度，任务已停止');throw writeError})
    return writes
  }
  const started=Date.now(),checkStop=()=>{
    if(writeError)throw writeError
    if(stopping||existsSync(config.stopPath))throw new Error('USER_STOPPED')
    if(Date.now()-started>20*60*1000)throw new Error('TIME_BUDGET')
    if(browser?.isConnected?.()===false||page?.isClosed?.())throw unavailable('BOSS 浏览器已关闭或连接中断；已有结果保留')
  }
  const control={config,state,persist,checkStop,dependencies}
  const pause=dependencies.pause||(()=>wait(1600+Math.random()*500))
  const live=new Map()
  const tick=setInterval(()=>{if(existsSync(config.stopPath))stopping=true;persist().catch(()=>{})},2000)
  const tab=async()=>{
    checkStop()
    if(!browser){browser=await (dependencies.openBrowser||openBrowser)(config,{checkStop});state.cleanup_status='pending'}
    if(!page){
      page=await browser.takeInitialPage?.()||await browser.contexts()[0].newPage()
      // Defense in depth for native task tabs: conversation navigation is never
      // part of this workflow, even if the website changes a button handler.
      await page.route?.('**/*',route=>{
        const request=route.request()
        if(request.isNavigationRequest()&&/\/(?:web\/geek\/chat|web\/chat|chat\/im)(?:[/?#]|$)/i.test(request.url()))return route.abort()
        return route.continue()
      })
    }
    return page
  }
  const visit=async(kind,url,hit,read)=>{
    const p=await tab();await pause();checkStop()
    if(kind==='list')state.list_attempts++
    else state.detail_attempts++
    await persist()
    await adapter.navigate(p,{kind,url,hit,...control})
    return read(p)
  }
  const readJob=async(candidate,target)=>{
    if(live.has(candidate.url))return {...live.get(candidate.url)}
    if(state.detail_attempts>=60)return null
    let result={url:candidate.url,title:candidate.title||target.title,company:candidate.company||target.company,
      city:candidate.city||'',salary:'',jd:'',checked_at:'',availability:'failed',favorite_status:'not_checked',error:''}
    try{
      const item=await visit('detail',adapter.detailURL?.(candidate)||candidate.url,{},p=>adapter.readDetail(p,candidate,control))
      result.checked_at=new Date().toISOString()
      if(!item){result.availability='closed';result.error='BOSS 原始岗位提示已关闭'}
      else{
        const status=await page.evaluate(favoriteDOM)
        if(jobURL(status.url)!==candidate.url)throw unavailable('岗位页面发生跳转，已停止以免操作其他职位')
        result={...result,...item,checked_at:result.checked_at}
        if(!item.jd_available||!item.title||!item.company||status.state==='unavailable'){
          result.availability='unread';result.error='岗位详情或收藏入口尚未能完整读取，请重试'
        }else{
          result.availability='active'
          result.favorite_status=status.state==='saved'?'already_saved':'not_checked'
        }
      }
    }catch(error){
      if(error.fatal||/USER_STOPPED|TIME_BUDGET/.test(error.message))throw error
      result.error=String(error.message).slice(0,2000)
    }
    live.set(candidate.url,result)
    return {...result}
  }
  try{
    await persist()
    if(prefs.action==='match'){
      for(const target of prefs.targets){
        checkStop();state.phase='查找对应岗位';state.message=[target.company,target.title||target.source_url,target.city].filter(Boolean).join(' · ');await persist()
        let found=[]
        try{
          if(target.source_url)found=[{url:jobURL(target.source_url),title:target.title,company:target.company,city:''}]
          else{
            if(state.list_attempts>=12)break
            const keyword=[target.company,target.title].filter(Boolean).join(' '),hit={keyword,city:target.city,page:1}
            found=await visit('list',searchURL(keyword,target.city,1,prefs),hit,p=>adapter.readList(p,hit,control))
            found=found.sort((a,b)=>{
              const score=c=>Number(normalize(c.company)===normalize(target.company))*2+Number(normalize(c.title)===normalize(target.title))
              return score(b)-score(a)
            }).slice(0,3)
          }
          const result={id:target.id,state:'not_found',error:'',candidates:[]}
          output.results.push(result)
          for(const candidate of found){
            checkStop()
            const item=await readJob(candidate,target)
            if(!item){result.error='达到本轮 60 次详情上限，可重试本条';break}
            if(!target.source_url&&item.availability==='active'&&!cityMatches(item.city,target.city)){
              item.availability='unread';item.error='实际城市与清单不符，未自动勾选'
            }
            item.selected=false;item.match_reason=target.source_url?'由清单中的原始岗位链接定位':'BOSS 当前搜索结果；请按公司、城市选择对应职位'
            result.candidates.push(item)
            result.state=item.availability==='active'?'needs_choice':item.availability==='closed'?'closed':'failed'
            await persist()
          }
          const active=result.candidates.filter(c=>c.availability==='active')
          const selected=result.error?[]:autoSelection(target,result.candidates)
          for(const c of result.candidates)if(selected.includes(c.url)){
            c.selected=true;c.match_reason=target.source_url?'已重新读取清单原始链接':'公司、岗位名称和城市一致，已默认勾选'
          }
          result.state=active.length?(selected.length||active.every(c=>c.favorite_status==='already_saved')?'matched':'needs_choice'):
            !found.length?'not_found':result.candidates.every(c=>c.availability==='closed')?'closed':'failed'
          state.processed++
          state.closed+=result.candidates.filter(c=>c.availability==='closed').length
          state.failed+=result.candidates.filter(c=>['failed','unread'].includes(c.availability)).length
          await persist()
          if(state.detail_attempts>=60)break
        }catch(error){
          if(error.fatal||/USER_STOPPED|TIME_BUDGET/.test(error.message))throw error
          if(!output.results.some(r=>r.id===target.id))output.results.push({id:target.id,state:'failed',candidates:[],error:String(error.message).slice(0,2000)})
          state.failed++;state.processed++;await persist()
        }
      }
    }else{
      const byURL=new Map()
      for(const target of prefs.targets){const rows=byURL.get(target.url)||[];rows.push(target);byURL.set(target.url,rows)}
      state.total=byURL.size
      for(const [url,targets] of byURL){
        checkStop();state.phase='读取并收藏岗位';state.message=[targets[0].company,targets[0].title].filter(Boolean).join(' · ');await persist()
        let result={...targets[0],id:targets[0].id,url,availability:'failed',favorite_status:'not_checked',checked_at:'',error:''}
        try{
          const item=await readJob({url,title:result.title,company:result.company}, {...targets[0],source_url:url})
          if(!item)break
          result={...result,...item,id:targets[0].id}
          if(result.availability==='closed')state.closed++
          else if(result.availability!=='active'){result.favorite_status='failed';state.failed++}
          else if(result.favorite_status==='already_saved')state.already_saved++
          else{
            // Persist uncertainty before the only remote mutation. A crash or
            // timeout must never report success or cause a blind second click.
            result.favorite_status='unknown';result.error='收藏操作尚未确认；重试时会先重新读取状态'
            for(const target of targets)output.results.push({...result,id:target.id})
            await persist();checkStop()
            await (dependencies.clickFavorite||clickFavorite)(page,url)
            let confirmed=false
            for(let step=0;step<20;step++){
              checkStop();await (dependencies.verifyWait||wait)(500)
              await ensureReadable(page,url,control)
              const status=await page.evaluate(favoriteDOM)
              if(jobURL(status.url)!==url)throw unavailable('收藏后页面发生变化，已停止；请先检查本次收藏状态')
              if(status.state==='saved'){confirmed=true;break}
            }
            if(!confirmed)throw new Error('未收到收藏成功状态；未重复点击，重试时会先检查实际状态')
            result.favorite_status='saved';result.error='';result.checked_at=new Date().toISOString();state.saved++
          }
        }catch(error){
          result.error=String(error.message).slice(0,2000)
          if(result.favorite_status!=='unknown')result.favorite_status='failed'
          state.failed++
          for(const target of targets){const old=output.results.find(r=>r.id===target.id);if(old)Object.assign(old,result,{id:target.id});else output.results.push({...result,id:target.id})}
          await persist()
          if(error.fatal||/USER_STOPPED|TIME_BUDGET/.test(error.message))throw error
        }
        for(const target of targets){const old=output.results.find(r=>r.id===target.id);if(old)Object.assign(old,result,{id:target.id});else output.results.push({...result,id:target.id})}
        state.processed++;await persist()
      }
    }
    output.status=state.failed?'completed_with_errors':'completed';state.phase='已完成'
    state.message=prefs.action==='match'?`已查找 ${state.processed} 条清单；只读取本次岗位，未执行收藏`:
      `本次新增收藏 ${state.saved} 条，原本已收藏 ${state.already_saved} 条，已关闭 ${state.closed} 条，失败或待确认 ${state.failed} 条`
  }catch(error){
    const stopped=/USER_STOPPED|TIME_BUDGET/.test(error.message)
    output.status=stopped?'stopped':'failed';output.error=stopped?'':String(error.message).slice(0,2000)
    state.phase=stopped?'已停止':'已中断';state.message=stopped?'已保留清单和进度，可继续处理剩余岗位':output.error
  }finally{
    clearInterval(tick)
    const closing=page;page=undefined
    let closeError=''
    if(closing&&!closing.isClosed?.())try{await closing.close()}catch(error){closeError=String(error.message)}
    let stats,browserError=false
    if(browser)try{stats=await browser.close();Object.assign(state,stats)}catch(error){browserError=true;closeError=String(error.message);Object.assign(state,error.cleanupStats||{})}
    if(closeError&&(browserError||stats?.cleanup_status!=='complete')){state.cleanup_status='incomplete';state.cleanup_message='任务标签未能全部确认关闭，请检查本次 BOSS 任务标签'}
    else if(!closeError){state.cleanup_status='complete';state.cleanup_message||='本次 BOSS 任务标签已关闭'}
    output.finished_at=new Date().toISOString();await persist();await writes
  }
  return output
}

async function main(){
  if(process.argv.includes('--self-test')){
    if(jobURL('https://www.zhipin.com/job_detail/test.html')!=='https://www.zhipin.com/job_detail/test.html')throw new Error('岗位来源检查失败')
    console.log(JSON.stringify({result:'passed',feature:'boss_favorites'}));return
  }
  const index=process.argv.indexOf('--config')
  if(index<0||!process.argv[index+1])throw new Error('缺少收藏任务配置')
  await run(JSON.parse(await readFile(process.argv[index+1],'utf8')))
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{console.error(error.message);process.exitCode=1})
